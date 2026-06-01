"""
Vyser Discord Bot — Phase 1 Entry Point

Architecture:
- Stateless: No JSON, SQLite, or local file storage for guild data.
- All configuration, logs, and history live inside dedicated Discord text channels.
- ConfigManager handles read/write by parsing message history dynamically.
- Cog-based extensibility ready for moderation, warnings, and tickets.

Required Gateway Intents:
- message_content  (to read command messages)
- guilds           (to resolve guild-specific config)
- members          (to evaluate role memberships; must be enabled in the Developer Portal)
"""

import os
import sys

import discord
from discord.ext import commands

from utils.config import ConfigManager

# ------------------------------------------------------------------
# Bot constants
# ------------------------------------------------------------------
COMMAND_PREFIX = "?"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.guilds = True
INTENTS.members = True  # Privileged — enable in Discord Developer Portal


class VyserBot(commands.Bot):
    """Custom Bot subclass with stateless ConfigManager attached."""

    def __init__(self):
        super().__init__(
            command_prefix=COMMAND_PREFIX,
            intents=INTENTS,
            help_command=commands.DefaultHelpCommand(),
            description="Vyser — stateless Discord bot with moderation, warnings, and tickets.",
        )
        # Attach ConfigManager so every cog can access self.bot.config_manager
        self.config_manager = ConfigManager(self)

    async def setup_hook(self):
        """Load Phase 1 cog and prepare Phase 2 placeholders."""
        # Phase 1 — core configuration
        await self.load_extension("cogs.manage")

        # Phase 2 — moderation / warnings (implemented inside manage cog)
        # Phase 3 — ticketing system
        await self.load_extension("cogs.tickets")

    async def on_ready(self):
        """Emitted once the bot has finished logging in and setting up."""
        print(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"🌐 Connected to {len(self.guilds)} guild(s)")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening, name="?manage"
            )
        )

    # ------------------------------------------------------------------
    # Global error handler
    # ------------------------------------------------------------------
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        """Centralised error handling for all command invocations."""
        if isinstance(error, commands.CheckFailure):
            # Covers both is_server_owner and has_authorized_role failures
            await ctx.send(f"⛔ {error}")

        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"❌ Missing argument: `{error.param.name}`. "
                f"Use `?help {ctx.command}` for correct usage."
            )

        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument: {error}")

        elif isinstance(error, commands.CommandNotFound):
            # Silently ignore unknown commands to reduce noise
            return

        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used inside a server.")

        else:
            # Log unhandled errors but don't leak internal details to users
            print(f"[Unhandled Error] {ctx.command}: {error}", file=sys.stderr)
            await ctx.send("⚠️ An unexpected error occurred. Check console for details.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print(
            "ERROR: DISCORD_TOKEN environment variable is not set.\n"
            "Export it before starting the bot, e.g.:\n"
            "    export DISCORD_TOKEN=your_token_here\n"
            "    python bot.py",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = VyserBot()
    bot.run(token)


if __name__ == "__main__":
    main()
