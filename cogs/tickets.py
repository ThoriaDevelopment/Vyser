"""
Tickets cog — Phase 3 complete ticketing system.

Public commands (no role required):
  ?ticket help  — shows how to open a ticket
  ?ticket open  — creates a private ticket channel

Management commands (active ticket channel + tickets role):
  ?ticket close   — deletes the ticket channel
  ?ticket add     — grants a user access to the ticket
  ?ticket remove  — revokes a user's access to the ticket

All state is stored in the stateless ConfigManager (Discord channel storage).
"""

import re
import time

import discord
from discord.ext import commands

from utils.checks import has_authorized_role, in_active_ticket


class Tickets(commands.Cog):
    """Support ticket lifecycle and access control."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==================================================================
    # Base group
    # ==================================================================
    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx: commands.Context):
        """Base ticket command."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `?ticket help` for assistance.")

    # ==================================================================
    # Public commands
    # ==================================================================
    @ticket.command()
    async def help(self, ctx: commands.Context):
        """Show ticket instructions."""
        await ctx.send('Use "?ticket open" to open a ticket!')

    @ticket.command()
    async def open(self, ctx: commands.Context):
        """Open a new support ticket channel.

        **Usage:** `?ticket open`

        Creates a private text channel under the configured tickets category.
        """
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used inside a server.")
            return

        config = await self.bot.config_manager.load_config(ctx.guild)

        # --------------------------------------------------------------
        # 1. Ticket-ban verification
        # --------------------------------------------------------------
        ban_entry = config.get(f"ticketban_{ctx.author.id}")
        if ban_entry is not None:
            if ban_entry == "permanent":
                await ctx.send(
                    "⛔ You are permanently banned from creating tickets."
                )
                return
            if isinstance(ban_entry, (int, float)) and int(ban_entry) > int(time.time()):
                await ctx.send(
                    "⛔ You are currently banned from creating tickets."
                )
                return
            # Expired entry — background task should have cleaned it, but we
            # tolerate stale data and proceed.

        # --------------------------------------------------------------
        # 2. One-ticket-per-user limit
        # --------------------------------------------------------------
        active_key = f"active_ticket_{ctx.author.id}"
        active_channel_id = config.get(active_key)
        if active_channel_id:
            existing = ctx.guild.get_channel(active_channel_id)
            if existing:
                await ctx.send(
                    f"⛔ You already have an active ticket: {existing.mention}"
                )
                return
            # Channel was deleted manually — clean up stale mapping.
            await self.bot.config_manager.delete_config_key(ctx.guild, active_key)

        # --------------------------------------------------------------
        # 3. Resolve ticket category
        # --------------------------------------------------------------
        category_id = await self.bot.config_manager.get_mapped_category(
            ctx.guild, "tickets"
        )
        if category_id is None:
            await ctx.send(
                "❌ No ticket category configured. Use `?manage setcategory tickets <id>`."
            )
            return

        category = discord.utils.get(ctx.guild.categories, id=category_id)
        if category is None:
            await ctx.send("❌ The configured ticket category no longer exists.")
            return

        # --------------------------------------------------------------
        # 4. Build unique channel name
        # --------------------------------------------------------------
        sanitized = self._sanitize_name(ctx.author.name)
        base_name = f"ticket-{sanitized}"
        channel_name = base_name
        counter = 2
        while discord.utils.get(ctx.guild.channels, name=channel_name):
            channel_name = f"{base_name}-{counter}"
            counter += 1

        # --------------------------------------------------------------
        # 5. Permission overwrites
        # --------------------------------------------------------------
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True,
                manage_permissions=True,
                read_message_history=True,
                manage_messages=True,
            ),
            ctx.author: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        # Grant access to any role authorised for the tickets command group.
        role_id = await self.bot.config_manager.get_role_for_command(ctx.guild, "tickets")
        if role_id:
            role = ctx.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True,
                )

        # --------------------------------------------------------------
        # 6. Create channel
        # --------------------------------------------------------------
        try:
            ticket_channel = await ctx.guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Ticket opened by {ctx.author}",
            )
        except discord.Forbidden:
            await ctx.send(
                "❌ I don't have permission to create channels in that category."
            )
            return

        # --------------------------------------------------------------
        # 7. Persist active-ticket mapping
        # --------------------------------------------------------------
        await self.bot.config_manager.save_config(
            ctx.guild, active_key, ticket_channel.id
        )
        await ctx.send(f"✅ Your ticket has been created: {ticket_channel.mention}")

    # ==================================================================
    # Management commands (active ticket + tickets role)
    # ==================================================================
    @ticket.command()
    @in_active_ticket()
    @has_authorized_role("tickets")
    async def close(self, ctx: commands.Context):
        """Permanently close and delete this ticket channel.

        **Usage:** `?ticket close`
        """
        channel_id = ctx.channel.id

        # Clean up the active-ticket mapping before deletion.
        config = await self.bot.config_manager.load_config(ctx.guild)
        for key, value in config.items():
            if key.startswith("active_ticket_") and value == channel_id:
                await self.bot.config_manager.delete_config_key(ctx.guild, key)
                break

        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")

    @ticket.command()
    @in_active_ticket()
    @has_authorized_role("tickets")
    async def add(self, ctx: commands.Context, userid: int):
        """Grant a user access to this ticket channel.

        **Usage:** `?ticket add <userid>`
        **Example:**
        `?ticket add 123456789`
        """
        member = ctx.guild.get_member(userid)
        if member is None:
            await ctx.send("❌ User not found in this server.")
            return

        await ctx.channel.set_permissions(
            member,
            read_messages=True,
            send_messages=True,
            read_message_history=True,
        )
        await ctx.send(f"✅ {member.mention} has been added to this ticket.")

    @ticket.command()
    @in_active_ticket()
    @has_authorized_role("tickets")
    async def remove(self, ctx: commands.Context, userid: int):
        """Revoke a user's access to this ticket channel.

        **Usage:** `?ticket remove <userid>`
        **Example:**
        `?ticket remove 123456789`
        """
        member = ctx.guild.get_member(userid)
        if member is None:
            await ctx.send("❌ User not found in this server.")
            return

        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"✅ {member.mention} has been removed from this ticket.")

    # ==================================================================
    # Helpers
    # ==================================================================
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitise a Discord username for use in a channel name.

        Channel names are lowercase, alphanumeric, hyphen, and underscore only.
        """
        name = name.lower().replace(" ", "-")
        name = re.sub(r"[^a-z0-9\-_]", "", name)
        return name[:95]  # Leave room for "ticket-" prefix + collision suffix


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
