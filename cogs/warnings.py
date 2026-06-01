"""
Warnings cog — Phase 2 placeholder.

Planned features: warn, warnings, delwarn, clearwarns.
Warning history will be stored as JSON messages in the mapped `warn` channel
(configured via `?manage setchannel warn <id>`).
These commands will require the role set by `?manage setrole warnings @role`.
"""

from discord.ext import commands


class Warnings(commands.Cog):
    """Warning system (Phase 2)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
