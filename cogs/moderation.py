"""
Moderation cog — Phase 2 placeholder.

Planned features: kick, ban, mute, purge, lock/unlock.
These commands will reuse ConfigManager and the has_authorized_role("moderation")
check once the manage command `?manage setrole moderation @role` is configured.
"""

from discord.ext import commands


class Moderation(commands.Cog):
    """Server moderation tools (Phase 2)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
