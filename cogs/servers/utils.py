"""Shared utility functions for game servers"""

import re
import socket
import asyncio
from typing import Optional
from utils import logger


def remove_color_codes(text: str, pattern: str = r'§.') -> str:
    """
    Removes color codes from text.

    Args:
        text: Text to process
        pattern: Regex pattern for color codes (default: Minecraft format '§.')

    Returns:
        Text with color codes removed
    """
    if isinstance(text, str):
        return re.sub(pattern, '', text)
    return text


async def resolve_domain_to_ip(address: str) -> Optional[str]:
    """
    Resolves domain to IP address (nslookup).

    Args:
        address: Domain or IP address

    Returns:
        IP address if domain was resolved, None if already an IP
    """
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
        logger.error(f"GameServer || Failed to resolve domain to IP | Domain: {address}, Error: {e}")
        return None


def format_server_address(domain: Optional[str], ip_address: Optional[str], port: int, default_port: int = 25565) -> str:
    """
    Formats server address for display.

    Args:
        domain: Domain address (preferred)
        ip_address: IP address (fallback)
        port: Server port
        default_port: Default port (won't be displayed if matches)

    Returns:
        Formatted server address string
    """
    display_address = domain if domain else ip_address
    if port != default_port:
        return f"{display_address}:{port}"
    return display_address
