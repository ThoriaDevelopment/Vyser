"""
Stateless configuration manager using Discord text channels as the sole data store.

All configuration, role mappings, and channel mappings are stored as JSON messages
inside a dedicated per-guild text channel. No local files, SQLite, or env vars are
used for bot data.
"""

import json
from typing import Any, Dict, Optional

import discord

# Name of the hidden text channel used for configuration storage per guild.
CONFIG_CHANNEL_NAME = "vyser-config"


class ConfigManager:
    """Reads and writes guild configuration by parsing message history in Discord."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Channel helpers
    # ------------------------------------------------------------------
    async def _get_config_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Locate the existing configuration channel in the guild by name."""
        for channel in guild.text_channels:
            if channel.name == CONFIG_CHANNEL_NAME:
                return channel
        return None

    async def _ensure_config_channel(self, guild: discord.Guild) -> discord.TextChannel:
        """Return the configuration channel, creating it if necessary.

        Permissions are locked down so only the bot and server owner can view it.
        """
        channel = await self._get_config_channel(guild)
        if channel:
            return channel

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False, send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                read_message_history=True,
            ),
        }

        # Grant the literal server owner visibility for transparency / debugging.
        owner = guild.get_member(guild.owner_id)
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, read_message_history=True
            )

        return await guild.create_text_channel(CONFIG_CHANNEL_NAME, overwrites=overwrites)

    # ------------------------------------------------------------------
    # Core read / write
    # ------------------------------------------------------------------
    async def load_config(self, guild: discord.Guild) -> Dict[str, Any]:
        """Load configuration by scanning the config channel's message history.

        Parses each message as JSON. When multiple messages define the same key,
        the *newest* message wins (iterates newest-first, first-seen per key).
        """
        config: Dict[str, Any] = {}
        channel = await self._get_config_channel(guild)
        if not channel:
            return config

        async for message in channel.history(limit=300, oldest_first=False):
            try:
                data = json.loads(message.content)
                if isinstance(data, dict):
                    for key, value in data.items():
                        # Because we walk newest-first, the first occurrence of a
                        # key is the most recent authoritative value.
                        if key not in config:
                            config[key] = value
            except json.JSONDecodeError:
                # Skip non-JSON messages (e.g. manual admin notes).
                continue

        return config

    async def save_config(self, guild: discord.Guild, key: str, value: Any) -> None:
        """Persist a single key-value pair.

        Attempts to edit an existing message that already holds this key so the
        channel doesn't accumulate redundant entries. If no matching message is
        found, sends a new one.
        """
        channel = await self._ensure_config_channel(guild)
        payload = json.dumps({key: value}, separators=(",", ":"))

        async for message in channel.history(limit=300):
            try:
                data = json.loads(message.content)
                if isinstance(data, dict) and key in data:
                    await message.edit(content=payload)
                    return
            except json.JSONDecodeError:
                continue

        await channel.send(payload)

    async def delete_config_key(self, guild: discord.Guild, key: str) -> None:
        """Remove a key from the configuration store by deleting its message."""
        channel = await self._get_config_channel(guild)
        if not channel:
            return

        async for message in channel.history(limit=300):
            try:
                data = json.loads(message.content)
                if isinstance(data, dict) and key in data:
                    await message.delete()
                    return
            except json.JSONDecodeError:
                continue

    # ------------------------------------------------------------------
    # Typed helpers for future cogs (moderation, warnings, tickets)
    # ------------------------------------------------------------------
    async def get_role_for_command(self, guild: discord.Guild, command: str) -> Optional[int]:
        """Return the role ID authorised for a given command group, or None."""
        config = await self.load_config(guild)
        return config.get(f"role_{command}")

    async def get_mapped_channel(self, guild: discord.Guild, channel_name: str) -> Optional[int]:
        """Return the physical channel ID mapped to a logical system channel."""
        config = await self.load_config(guild)
        return config.get(f"channel_{channel_name}")

    async def get_mapped_category(self, guild: discord.Guild, category_name: str) -> Optional[int]:
        """Return the physical category ID mapped to a logical system category."""
        config = await self.load_config(guild)
        return config.get(f"category_{category_name}")
