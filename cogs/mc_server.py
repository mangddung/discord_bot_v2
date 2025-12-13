import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from sqlalchemy import Column, Integer, String, Boolean, desc, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from db import Base, get_db
from mcstatus import JavaServer
from typing import Optional
from utils import logger
import re
import socket
from datetime import datetime, timedelta

# Database table definitions
# ========================================================================================
class MinecraftServer(Base):
    """Minecraft server information table (shared across multiple guilds)"""
    __tablename__ = 'minecraft_servers'

    id = Column(Integer, primary_key=True)
    domain = Column(String, nullable=True)  # Domain address (e.g., mc.hypixel.net)
    ip_address = Column(String, nullable=True)  # IP address
    port = Column(Integer, nullable=False, default=25565)
    last_status_check = Column(DateTime, nullable=True)  # Last status check time

    # Relationship: Guild settings using this server
    guild_settings = relationship("GuildServerSettings", back_populates="server", cascade="all, delete-orphan")


class GuildServerSettings(Base):
    """Guild-specific server monitoring settings table"""
    __tablename__ = 'guild_server_settings'

    id = Column(Integer, primary_key=True)
    guild_id = Column(Integer, nullable=False)
    channel_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    server_name = Column(String, nullable=False)  # Server nickname
    max_players_display = Column(Integer, nullable=False, default=5)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # Creation time

    # Foreign key: Reference to MinecraftServer table
    server_id = Column(Integer, ForeignKey('minecraft_servers.id'), nullable=False)

    # Relationship: Connected Minecraft server
    server = relationship("MinecraftServer", back_populates="guild_settings")


# Minecraft server related functions
# ========================================================================================
def remove_minecraft_color_codes(text: str) -> str:
    """Removes Minecraft color codes (§)."""
    if isinstance(text, str):
        # Remove § followed by any character (§a, §c, §l, etc.)
        return re.sub(r'§.', '', text)
    return text


async def resolve_domain_to_ip(address: str) -> Optional[str]:
    """Resolves domain to IP address (nslookup)."""
    try:
        # Check if already an IP address
        try:
            socket.inet_aton(address)
            return None  # Already an IP address
        except socket.error:
            pass

        # Convert domain to IP
        ip = await asyncio.to_thread(socket.gethostbyname, address)
        return ip
    except Exception as e:
        logger.error(f"McServer || Failed to resolve domain to IP | Domain: {address}, Error: {e}")
        return None


async def get_or_create_server(db, address: str, port: int) -> MinecraftServer:
    """Gets or creates server information."""
    # Resolve IP
    ip_address = await resolve_domain_to_ip(address)

    # Determine if domain or IP
    is_ip = ip_address is None
    domain = None if is_ip else address
    ip_address = address if is_ip else ip_address

    # Find existing server (by domain or IP + port)
    server = None
    if domain:
        server = db.query(MinecraftServer).filter_by(domain=domain, port=port).first()
    if not server and ip_address:
        server = db.query(MinecraftServer).filter_by(ip_address=ip_address, port=port).first()

    # Create if not exists
    if not server:
        server = MinecraftServer(
            domain=domain,
            ip_address=ip_address,
            port=port
        )
        db.add(server)
        db.commit()
        db.refresh(server)
        logger.info(f"McServer || Created new server info | Domain: {domain}, IP: {ip_address}, Port: {port}")

    return server


async def get_server_status(server: MinecraftServer):
    """Queries Minecraft server status."""
    try:
        # Prefer domain, fallback to IP
        address = server.domain if server.domain else server.ip_address

        if not address:
            return {'online': False, 'error': 'No server address available'}

        # Address with port
        server_address = f"{address}:{server.port}" if server.port != 25565 else address

        # Create JavaServer object (async)
        mc_server = await JavaServer.async_lookup(server_address)

        # Query server status
        status = await mc_server.async_status()

        # Use sample data by default (max 12 players)
        players_list = [player.name for player in status.players.sample] if status.players.sample else []

        return {
            'online': True,
            'version': status.version.name,
            'protocol': status.version.protocol,
            'players_online': status.players.online,
            'players_max': status.players.max,
            'players_list': players_list,
            'motd': status.description,
            'latency': status.latency,
            'icon': status.icon
        }
    except Exception as e:
        return {
            'online': False,
            'error': str(e)
        }


async def create_server_panel_form(server_name: str, server: MinecraftServer, max_players_display: int = 5):
    """Creates server panel embed."""
    status = await get_server_status(server)

    # Server address (prefer domain, use code block for easy copying)
    display_address = server.domain if server.domain else server.ip_address
    server_address = f"{display_address}:{server.port}" if server.port != 25565 else display_address

    if status['online']:
        embed = discord.Embed(
            title=f"🟢 {server_name}",
            description=f"**Server Address**\n```\n{server_address}\n```",
            color=discord.Color.green()
        )
        embed.add_field(name="Version", value=status['version'], inline=True)
        embed.add_field(name="Ping", value=f"{status['latency']:.0f}ms", inline=True)
        embed.add_field(
            name="Players",
            value=f"{status['players_online']}/{status['players_max']}",
            inline=True
        )

        # Add MOTD (remove color codes)
        motd_text = status['motd']
        if isinstance(motd_text, dict):
            if 'text' in motd_text:
                motd_text = motd_text['text']
            else:
                motd_text = str(motd_text)

        motd_text = remove_minecraft_color_codes(motd_text)
        embed.add_field(name="Description", value=motd_text[:100] if motd_text else "None", inline=False)

        # Player list (display configured amount)
        if status['players_list']:
            display_count = min(max_players_display, 12)
            players_to_show = status['players_list'][:display_count]

            players_text = "\n".join([f"• {player}" for player in players_to_show])

            remaining = len(status['players_list']) - display_count
            if remaining > 0:
                players_text += f"\n\n+{remaining} more"

            embed.add_field(name="Online Players", value=players_text, inline=False)
        else:
            if status['players_online'] > 0:
                embed.add_field(name="Online Players", value="Unable to fetch player list", inline=False)
    else:
        embed = discord.Embed(
            title=f"🔴 {server_name}",
            description=f"**Server Address**\n```\n{server_address}\n```",
            color=discord.Color.red()
        )
        embed.add_field(name="Error", value=status.get('error', 'Unknown error')[:100], inline=False)

    embed.set_footer(text="Last Updated")
    embed.timestamp = discord.utils.utcnow()

    return embed


async def update_server_panels(bot):
    """Updates all server panels (queries each server only once)."""
    while True:
        try:
            with get_db() as db:
                # Get all server info (without duplicates)
                servers = db.query(MinecraftServer).all()

                # Query status and cache per server
                server_status_cache = {}

                for server in servers:
                    # Check last check time (skip if within 30 seconds)
                    if server.last_status_check:
                        time_since_check = datetime.utcnow() - server.last_status_check
                        if time_since_check < timedelta(seconds=30):
                            continue

                    # Query server status
                    status = await get_server_status(server)
                    server_status_cache[server.id] = status

                    # Update last check time
                    server.last_status_check = datetime.utcnow()
                    db.commit()

                    # Update all guild settings using this server
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
                                logger.warning(f"McServer || Panel message not found | Guild: {guild.id}, Channel: {channel.id}")
                                continue

                            embed = await create_server_panel_form(
                                setting.server_name,
                                server,
                                setting.max_players_display
                            )
                            await message.edit(embed=embed)
                            logger.info(f"McServer || Panel updated | Guild: {guild.id}, Server: {setting.server_name}")

                        except Exception as ex:
                            logger.error(f"McServer || Panel update error | Guild: {setting.guild_id}, Server: {setting.server_name}, Error: {ex}")
                            continue

                    # Wait time between servers (prevent excessive queries)
                    await asyncio.sleep(1)

        except Exception as ex:
            logger.error(f"McServer || Full panel update error: {ex}")

        # Update every 5 minutes
        await asyncio.sleep(300)


# Modal definitions
# ========================================================================================
class McServerModal(discord.ui.Modal, title="Add Minecraft Server"):
    channel_name = discord.ui.TextInput(
        label="Channel Name",
        placeholder="e.g., mc-server",
        required=True,
        max_length=100
    )
    server_name = discord.ui.TextInput(
        label="Server Name (Nickname)",
        placeholder="e.g., Minecraft Server",
        required=True,
        max_length=100
    )
    server_address = discord.ui.TextInput(
        label="Server Address",
        placeholder="e.g., mc.hypixel.net or IP",
        required=True,
        max_length=200
    )
    server_port = discord.ui.TextInput(
        label="Server Port",
        placeholder="25565 (default)",
        required=False,
        default="25565",
        max_length=5
    )
    max_players_display = discord.ui.TextInput(
        label="Players to Display",
        placeholder="5 (default, max 12)",
        required=False,
        default="5",
        max_length=2
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Executed when modal is submitted"""
        await interaction.response.defer()

        guild = interaction.guild
        channel_name = self.channel_name.value
        server_name = self.server_name.value
        server_address = self.server_address.value.strip()

        # Parse port
        try:
            server_port = int(self.server_port.value) if self.server_port.value else 25565
        except ValueError:
            server_port = 25565

        # Parse max players to display
        try:
            max_players = int(self.max_players_display.value) if self.max_players_display.value else 5
            max_players = max(1, min(max_players, 12))
        except ValueError:
            max_players = 5

        # Check for duplicate channel name
        search_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if search_channel:
            await interaction.followup.send(f"Channel '{channel_name}' already exists. Please use a different name.")
            return

        try:
            with get_db() as db:
                # Get or create server info
                server = await get_or_create_server(db, server_address, server_port)

                # Check guild registration limit (max 10)
                guild_server_count = db.query(GuildServerSettings).filter_by(guild_id=guild.id).count()
                if guild_server_count >= 10:
                    await interaction.followup.send("You have already registered 10 servers. Cannot register more.")
                    return

                # Check if server is already registered
                existing_setting = db.query(GuildServerSettings).filter_by(
                    guild_id=guild.id,
                    server_id=server.id
                ).first()

                if existing_setting:
                    await interaction.followup.send("This server is already registered.")
                    return

                # Rate limiting: Check registrations within last 1 minute (spam prevention)
                one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
                recent_registrations = db.query(GuildServerSettings).filter(
                    GuildServerSettings.guild_id == guild.id,
                    GuildServerSettings.created_at >= one_minute_ago
                ).count()

                if recent_registrations >= 3:
                    await interaction.followup.send("You are registering servers too quickly. Please try again in 1 minute.")
                    return

                # Create dedicated channel
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(send_messages=False),
                    guild.me: discord.PermissionOverwrite(send_messages=True)
                }
                created_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)

                # Send server status panel message
                embed = await create_server_panel_form(server_name, server, max_players)
                created_message = await created_channel.send(embed=embed)

                # Save guild settings
                new_setting = GuildServerSettings(
                    guild_id=guild.id,
                    channel_id=created_channel.id,
                    message_id=created_message.id,
                    server_name=server_name,
                    server_id=server.id,
                    max_players_display=max_players
                )
                db.add(new_setting)
                db.commit()

                await interaction.followup.send(f"Minecraft server '{server_name}' monitoring channel has been created.\nIt will update automatically every 5 minutes.")
                logger.info(f"McServer || Server monitoring channel created | Guild: {guild.id}, Channel: {created_channel.id}, Server: {server_name}")

        except Exception as ex:
            db.rollback()
            if 'created_channel' in locals():
                await created_channel.delete()
            await interaction.followup.send("An error occurred. Please try again.")
            logger.exception(f"McServer || Channel creation error | Guild: {guild.id}, Server: {server_name}, Error: {ex}")


# Discord bot events
# ========================================================================================
class McServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_task = None

    @app_commands.command(name="mcs_add", description="Create a Minecraft server monitoring channel")
    @app_commands.default_permissions(administrator=True)
    async def add_server(self, interaction: discord.Interaction):
        """Creates a Minecraft server monitoring channel (using modal)."""
        modal = McServerModal()
        await interaction.response.send_modal(modal)

    @app_commands.command(name="mcs_remove", description="Remove a Minecraft server monitoring channel")
    @app_commands.default_permissions(administrator=True)
    async def remove_server(
        self,
        interaction: discord.Interaction,
        server_name: str
    ):
        """Removes a Minecraft server monitoring channel."""
        await interaction.response.defer()

        guild = interaction.guild

        with get_db() as db:
            try:
                setting = db.query(GuildServerSettings).filter_by(
                    guild_id=guild.id,
                    server_name=server_name
                ).first()

                if not setting:
                    await interaction.followup.send(f"Cannot find server '{server_name}'.")
                    return

                channel = guild.get_channel(int(setting.channel_id))
                if channel:
                    await channel.delete()

                db.delete(setting)
                db.commit()

                await interaction.followup.send(f"Server '{server_name}' monitoring channel has been removed.")
                logger.info(f"McServer || Server monitoring channel removed | Guild: {guild.id}, Server: {server_name}")

            except Exception as ex:
                db.rollback()
                await interaction.followup.send("An error occurred during removal. Please try again.")
                logger.exception(f"McServer || Channel removal error | Guild: {guild.id}, Server: {server_name}, Error: {ex}")

    @app_commands.command(name="mcs_list", description="View registered Minecraft servers")
    async def list_servers(self, interaction: discord.Interaction):
        """Views registered Minecraft servers."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        with get_db() as db:
            settings = db.query(GuildServerSettings).filter_by(guild_id=guild.id).all()

            if not settings:
                await interaction.followup.send("No registered servers.")
                return

            embed = discord.Embed(
                title="Registered Minecraft Servers",
                color=discord.Color.blue()
            )

            for setting in settings:
                server = setting.server
                display_address = server.domain if server.domain else server.ip_address
                server_info = f"**Address:** {display_address}:{server.port}\n"
                channel = guild.get_channel(int(setting.channel_id))
                if channel:
                    server_info += f"**Channel:** {channel.mention}"
                embed.add_field(name=setting.server_name, value=server_info, inline=False)

            await interaction.followup.send(embed=embed)

    @app_commands.command(name="mcs_update", description="Update server panels immediately")
    @app_commands.default_permissions(administrator=True)
    async def update_now(self, interaction: discord.Interaction):
        """Updates server panels immediately."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        with get_db() as db:
            settings = db.query(GuildServerSettings).filter_by(guild_id=guild.id).all()

            if not settings:
                await interaction.followup.send("No registered servers.")
                return

            for setting in settings:
                try:
                    channel = guild.get_channel(int(setting.channel_id))
                    if not channel:
                        continue

                    message = await channel.fetch_message(int(setting.message_id))
                    embed = await create_server_panel_form(
                        setting.server_name,
                        setting.server,
                        setting.max_players_display
                    )
                    await message.edit(embed=embed)

                except Exception as ex:
                    logger.error(f"McServer || Panel update error | Server: {setting.server_name}, Error: {ex}")

            await interaction.followup.send("All server panels have been updated.")
            logger.info(f"McServer || Manual panel update completed | Guild: {guild.id}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Starts the automatic update task when bot starts."""
        if self.update_task is None or self.update_task.done():
            self.update_task = asyncio.create_task(update_server_panels(self.bot))
            logger.info("McServer || Automatic update task started")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Deletes the settings from DB when a channel is deleted."""
        if not isinstance(channel, discord.TextChannel):
            return

        with get_db() as db:
            try:
                # Find settings linked to the deleted channel
                setting = db.query(GuildServerSettings).filter_by(
                    guild_id=channel.guild.id,
                    channel_id=channel.id
                ).first()

                if setting:
                    server_name = setting.server_name
                    server_id = setting.server_id

                    # Delete settings
                    db.delete(setting)
                    db.commit()

                    logger.info(f"McServer || Settings removed due to channel deletion | Guild: {channel.guild.id}, Channel: {channel.id}, Server: {server_name}")

                    # Delete server info if no other guilds are using it
                    remaining_settings = db.query(GuildServerSettings).filter_by(server_id=server_id).count()
                    if remaining_settings == 0:
                        server = db.query(MinecraftServer).filter_by(id=server_id).first()
                        if server:
                            db.delete(server)
                            db.commit()
                            logger.info(f"McServer || Unused server info deleted | Server ID: {server_id}, Domain: {server.domain}, IP: {server.ip_address}")

            except Exception as ex:
                db.rollback()
                logger.error(f"McServer || Channel deletion event processing error | Guild: {channel.guild.id}, Channel: {channel.id}, Error: {ex}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(McServer(bot))
