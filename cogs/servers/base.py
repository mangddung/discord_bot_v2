"""Base classes and interfaces for game server handlers"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import discord


class ServerHandler(ABC):
    """Base class for game server handlers"""

    @abstractmethod
    def get_modal_class(self):
        """Returns the modal class for this server type"""
        pass

    @abstractmethod
    async def create_panel_embed(self, server_name: str, server_data: Any, settings: Any) -> discord.Embed:
        """Creates the server status panel embed"""
        pass

    @abstractmethod
    async def get_server_status(self, server_data: Any) -> Dict[str, Any]:
        """Queries server status"""
        pass

    @abstractmethod
    async def process_modal_submission(self, interaction: discord.Interaction, modal_data: Dict[str, str]):
        """Processes modal submission and creates monitoring channel"""
        pass

    @abstractmethod
    def get_all_servers(self, db) -> List:
        """Returns all server records"""
        pass

    @abstractmethod
    def get_guild_settings(self, db, guild_id: int) -> List:
        """Returns all guild settings for a guild"""
        pass

    @abstractmethod
    def find_guild_setting_by_name(self, db, guild_id: int, server_name: str):
        """Find guild setting by server name"""
        pass

    @abstractmethod
    def find_guild_setting_by_channel(self, db, guild_id: int, channel_id: int):
        """Find guild setting by channel id"""
        pass

    @abstractmethod
    def cleanup_unused_server(self, db, server_id: int):
        """Delete server record if no guild settings reference it"""
        pass
