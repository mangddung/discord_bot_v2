"""Minecraft server monitoring handler"""

import discord
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from mcstatus import JavaServer

from db import Base, get_db
from utils import logger
from .base import ServerHandler
from .utils import remove_color_codes, resolve_domain_to_ip, format_server_address


# Database Models
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
    server_type = Column(String, nullable=False, default='minecraft')  # Server type
    max_players_display = Column(Integer, nullable=False, default=5)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    # Foreign key: Reference to MinecraftServer table
    server_id = Column(Integer, ForeignKey('minecraft_servers.id'), nullable=False)

    # Relationship: Connected Minecraft server
    server = relationship("MinecraftServer", back_populates="guild_settings")


# Modal
# ========================================================================================
class MinecraftServerModal(discord.ui.Modal, title="Add Minecraft Server"):
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

    def __init__(self, handler):
        super().__init__()
        self.handler = handler

    async def on_submit(self, interaction: discord.Interaction):
        """Executed when modal is submitted"""
        modal_data = {
            'channel_name': self.channel_name.value,
            'server_name': self.server_name.value,
            'server_address': self.server_address.value.strip(),
            'server_port': self.server_port.value,
            'max_players_display': self.max_players_display.value
        }
        await self.handler.process_modal_submission(interaction, modal_data)


# Handler
# ========================================================================================
class MinecraftServerHandler(ServerHandler):
    """Handles Minecraft server monitoring"""

    def get_modal_class(self):
        """Returns the modal class for Minecraft servers"""
        return lambda: MinecraftServerModal(self)

    async def get_server_status(self, server: MinecraftServer) -> Dict[str, Any]:
        """Queries Minecraft server status"""
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

    async def create_panel_embed(self, server_name: str, server: MinecraftServer, settings: GuildServerSettings) -> discord.Embed:
        """Creates server panel embed"""
        status = await self.get_server_status(server)

        # Server address (prefer domain, use code block for easy copying)
        server_address = format_server_address(server.domain, server.ip_address, server.port, default_port=25565)

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

            motd_text = remove_color_codes(motd_text)
            embed.add_field(name="Description", value=motd_text[:100] if motd_text else "None", inline=False)

            # Player list (display configured amount)
            if status['players_list']:
                display_count = min(settings.max_players_display, 12)
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

    def get_all_servers(self, db):
        return db.query(MinecraftServer).all()

    def get_guild_settings(self, db, guild_id: int):
        return db.query(GuildServerSettings).filter_by(guild_id=guild_id).all()

    def find_guild_setting_by_name(self, db, guild_id: int, server_name: str):
        return db.query(GuildServerSettings).filter_by(guild_id=guild_id, server_name=server_name).first()

    def find_guild_setting_by_channel(self, db, guild_id: int, channel_id: int):
        return db.query(GuildServerSettings).filter_by(guild_id=guild_id, channel_id=channel_id).first()

    def cleanup_unused_server(self, db, server_id: int):
        remaining = db.query(GuildServerSettings).filter_by(server_id=server_id).count()
        if remaining == 0:
            server = db.query(MinecraftServer).filter_by(id=server_id).first()
            if server:
                db.delete(server)
                db.commit()
                logger.info(f"Minecraft || Unused server info deleted | Server ID: {server_id}")

    async def get_or_create_server(self, db, address: str, port: int) -> MinecraftServer:
        """Gets or creates server information"""
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
            logger.info(f"Minecraft || Created new server info | Domain: {domain}, IP: {ip_address}, Port: {port}")

        return server

    async def process_modal_submission(self, interaction: discord.Interaction, modal_data: Dict[str, str]):
        """Processes modal submission and creates monitoring channel"""
        await interaction.response.defer()

        guild = interaction.guild
        channel_name = modal_data['channel_name']
        server_name = modal_data['server_name']
        server_address = modal_data['server_address']

        # Parse port
        try:
            server_port = int(modal_data['server_port']) if modal_data['server_port'] else 25565
        except ValueError:
            server_port = 25565

        # Parse max players to display
        try:
            max_players = int(modal_data['max_players_display']) if modal_data['max_players_display'] else 5
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
                server = await self.get_or_create_server(db, server_address, server_port)

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
                one_minute_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
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

                # Create new settings (without panel message yet)
                new_setting = GuildServerSettings(
                    guild_id=guild.id,
                    channel_id=created_channel.id,
                    message_id=0,  # Temporary, will update after message creation
                    server_name=server_name,
                    server_id=server.id,
                    server_type='minecraft',
                    max_players_display=max_players
                )
                db.add(new_setting)
                db.commit()
                db.refresh(new_setting)

                # Send server status panel message
                embed = await self.create_panel_embed(server_name, server, new_setting)
                created_message = await created_channel.send(embed=embed)

                # Update message_id
                new_setting.message_id = created_message.id
                db.commit()

                await interaction.followup.send(f"Minecraft server '{server_name}' monitoring channel has been created.\nIt will update automatically every 5 minutes.")
                logger.info(f"Minecraft || Server monitoring channel created | Guild: {guild.id}, Channel: {created_channel.id}, Server: {server_name}")

        except Exception as ex:
            if 'db' in locals():
                db.rollback()
            if 'created_channel' in locals():
                await created_channel.delete()
            await interaction.followup.send("An error occurred. Please try again.")
            logger.exception(f"Minecraft || Channel creation error | Guild: {guild.id}, Server: {server_name}, Error: {ex}")
