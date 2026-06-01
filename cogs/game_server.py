"""Game server monitoring Cog - Main command handler"""

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta, timezone

from db import get_db
from utils import logger
from .servers.minecraft import MinecraftServerHandler
from .servers.palworld import PalworldServerHandler


# Server Handlers Registry
# ========================================================================================
SERVER_HANDLERS = {
    'minecraft': MinecraftServerHandler(),
    'palworld': PalworldServerHandler(),
    # Add more server handlers here as you implement them
    # 'ark': ArkServerHandler(),
}


# View definitions
# ========================================================================================
class ServerTypeSelectView(discord.ui.View):
    """View with dropdown menu to select server type"""
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="Choose a server type",
        options=[
            discord.SelectOption(
                label="Minecraft",
                value="minecraft",
                description="Minecraft server monitoring",
                emoji="🟩"
            ),
            discord.SelectOption(
                label="Palworld",
                value="palworld",
                description="Palworld server monitoring",
                emoji="🌴"
            ),
            # Add more game servers here later
            # discord.SelectOption(
            #     label="ARK",
            #     value="ark",
            #     description="ARK server monitoring",
            #     emoji="🦖"
            # ),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        server_type = select.values[0]

        handler = SERVER_HANDLERS.get(server_type)
        if handler:
            modal = handler.get_modal_class()()
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.send_message(
                f"Server type '{server_type}' is not yet implemented.",
                ephemeral=True
            )


# Panel Update Task
# ========================================================================================
async def update_server_panels(bot):
    """Updates all server panels using handler registry"""
    while True:
        try:
            with get_db() as db:
                for handler in SERVER_HANDLERS.values():
                    for server in handler.get_all_servers(db):
                        if server.last_status_check:
                            time_since_check = datetime.now(timezone.utc).replace(tzinfo=None) - server.last_status_check
                            if time_since_check < timedelta(seconds=30):
                                continue

                        server.last_status_check = datetime.now(timezone.utc).replace(tzinfo=None)
                        db.commit()

                        for setting in server.guild_settings:
                            try:
                                guild = bot.get_guild(int(setting.guild_id))
                                if not guild:
                                    continue

                                channel = guild.get_channel(int(setting.channel_id))
                                if not channel:
                                    continue

                                try:
                                    message = await channel.fetch_message(int(setting.message_id))
                                except discord.NotFound:
                                    logger.warning(f"GameServer || Panel message not found | Guild: {guild.id}, Channel: {channel.id}")
                                    continue

                                embed = await handler.create_panel_embed(
                                    setting.server_name,
                                    server,
                                    setting
                                )
                                await message.edit(embed=embed)
                                logger.info(f"GameServer || Panel updated | Guild: {guild.id}, Server: {setting.server_name}")

                            except Exception as ex:
                                logger.error(f"GameServer || Panel update error | Guild: {setting.guild_id}, Server: {setting.server_name}, Error: {ex}")
                                continue

                        await asyncio.sleep(1)

        except Exception as ex:
            logger.error(f"GameServer || Full panel update error: {ex}")

        await asyncio.sleep(300)


# Discord bot events
# ========================================================================================
class GameServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_task = None

    @app_commands.command(name="server_add", description="Create a game server monitoring channel")
    @app_commands.default_permissions(administrator=True)
    async def add_server(self, interaction: discord.Interaction):
        """Creates a game server monitoring channel (using dropdown and modal)"""
        view = ServerTypeSelectView()
        await interaction.response.send_message(
            "Select server type to monitor:",
            view=view,
            ephemeral=True
        )

    @app_commands.command(name="server_remove", description="Remove a game server monitoring channel")
    @app_commands.default_permissions(administrator=True)
    async def remove_server(
        self,
        interaction: discord.Interaction,
        server_name: str
    ):
        """Removes a game server monitoring channel"""
        await interaction.response.defer()

        guild = interaction.guild

        with get_db() as db:
            try:
                setting = None
                for handler in SERVER_HANDLERS.values():
                    setting = handler.find_guild_setting_by_name(db, guild.id, server_name)
                    if setting:
                        break

                if not setting:
                    await interaction.followup.send(f"Cannot find server '{server_name}'.")
                    return

                channel = guild.get_channel(int(setting.channel_id))
                if channel:
                    await channel.delete()

                db.delete(setting)
                db.commit()

                await interaction.followup.send(f"Server '{server_name}' monitoring channel has been removed.")
                logger.info(f"GameServer || Server monitoring channel removed | Guild: {guild.id}, Server: {server_name}")

            except Exception as ex:
                db.rollback()
                await interaction.followup.send("An error occurred during removal. Please try again.")
                logger.exception(f"GameServer || Channel removal error | Guild: {guild.id}, Server: {server_name}, Error: {ex}")

    @app_commands.command(name="server_list", description="View registered game servers")
    async def list_servers(self, interaction: discord.Interaction):
        """Views registered game servers"""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        with get_db() as db:
            all_settings = []
            for handler in SERVER_HANDLERS.values():
                all_settings.extend(handler.get_guild_settings(db, guild.id))

            if not all_settings:
                await interaction.followup.send("No registered servers.")
                return

            embed = discord.Embed(
                title="Registered Game Servers",
                color=discord.Color.blue()
            )

            for setting in all_settings:
                server = setting.server
                server_type = setting.server_type.capitalize()
                display_address = server.domain if server.domain else server.ip_address
                server_info = f"**Type:** {server_type}\n**Address:** {display_address}:{server.port}\n"
                channel = guild.get_channel(int(setting.channel_id))
                if channel:
                    server_info += f"**Channel:** {channel.mention}"
                embed.add_field(name=setting.server_name, value=server_info, inline=False)

            await interaction.followup.send(embed=embed)

    @app_commands.command(name="server_update", description="Update server panels immediately")
    @app_commands.default_permissions(administrator=True)
    async def update_now(self, interaction: discord.Interaction):
        """Updates server panels immediately"""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        with get_db() as db:
            all_settings = []
            for handler in SERVER_HANDLERS.values():
                all_settings.extend(handler.get_guild_settings(db, guild.id))

            if not all_settings:
                await interaction.followup.send("No registered servers.")
                return

            for setting in all_settings:
                try:
                    channel = guild.get_channel(int(setting.channel_id))
                    if not channel:
                        continue

                    message = await channel.fetch_message(int(setting.message_id))

                    handler = SERVER_HANDLERS.get(setting.server_type)
                    if not handler:
                        continue

                    embed = await handler.create_panel_embed(
                        setting.server_name,
                        setting.server,
                        setting
                    )
                    await message.edit(embed=embed)

                except Exception as ex:
                    logger.error(f"GameServer || Panel update error | Server: {setting.server_name}, Error: {ex}")

            await interaction.followup.send("All server panels have been updated.")
            logger.info(f"GameServer || Manual panel update completed | Guild: {guild.id}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Starts the automatic update task when bot starts"""
        if self.update_task is None or self.update_task.done():
            self.update_task = asyncio.create_task(update_server_panels(self.bot))
            logger.info("GameServer || Automatic update task started")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Deletes the settings from DB when a channel is deleted"""
        if not isinstance(channel, discord.TextChannel):
            return

        with get_db() as db:
            try:
                setting = None
                found_handler = None
                for handler in SERVER_HANDLERS.values():
                    setting = handler.find_guild_setting_by_channel(db, channel.guild.id, channel.id)
                    if setting:
                        found_handler = handler
                        break

                if setting:
                    server_name = setting.server_name
                    server_id = setting.server_id

                    db.delete(setting)
                    db.commit()

                    logger.info(f"GameServer || Settings removed due to channel deletion | Guild: {channel.guild.id}, Channel: {channel.id}, Server: {server_name}")

                    found_handler.cleanup_unused_server(db, server_id)

            except Exception as ex:
                db.rollback()
                logger.error(f"GameServer || Channel deletion event processing error | Guild: {channel.guild.id}, Channel: {channel.id}, Error: {ex}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameServer(bot))
