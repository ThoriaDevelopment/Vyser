"""
Manage cog — Phase 1 core configuration + Phase 2 moderation engine.

Implements the ?manage command group:
  Configuration (Server Owner / authorised role):
    ?manage setrole, ?manage setchannel, ?manage setcategory
  Moderation (authorised 'manage' role):
    ?manage ban, ?manage mute, ?manage ticketban
  Warnings (authorised 'manage' role):
    ?manage warn, ?manage checkwarn
  History / Utility (authorised 'manage' role):
    ?manage history, ?manage purge

All timed punishments are backed by a background task that polls every
60 seconds for expired entries in the stateless config store.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from utils.checks import has_authorized_role, is_server_owner
from utils.duration import format_timedelta, parse_duration

# ------------------------------------------------------------------
# Regex patterns for parsing punishment logs back out of #punishments
# ------------------------------------------------------------------
_PUNISHMENT_PATTERNS = [
    # auto-ban (bold reason) or any bolded ban
    re.compile(
        r"@(\d+) has been banned for (.+?)\. The reason specified is \*\*(.+?)\*\*\."
    ),
    # manual ban (plain reason)
    re.compile(r"@(\d+) has been banned for (.+?)\. The reason specified is (.+)\."),
    # mute
    re.compile(
        r"@(\d+) has been muted for (.+?)\. The reason specified is \*\*(.+?)\*\*\."
    ),
    # ticketban
    re.compile(
        r"@(\d+) has been ticket banned for (.+?)\. The reason specified is \*\*(.+?)\*\*\."
    ),
]

# Discord timeout hard-cap
def _clamp_timeout(delta: timedelta) -> timedelta:
    """Discord timeouts may not exceed 28 days."""
    max_timeout = timedelta(days=28)
    return delta if delta <= max_timeout else max_timeout


class Manage(commands.Cog):
    """Guild configuration and moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._check_expired_punishments.start()

    def cog_unload(self):
        self._check_expired_punishments.cancel()

    # ==================================================================
    # Background task — expire temp bans / ticketbans
    # ==================================================================
    @tasks.loop(minutes=1)
    async def _check_expired_punishments(self):
        """Scan every guild's config store and lift expired timed punishments."""
        for guild in self.bot.guilds:
            try:
                config = await self.bot.config_manager.load_config(guild)
                now = int(time.time())
                for key, value in config.items():
                    if not isinstance(value, int):
                        continue

                    if key.startswith("temp_ban_") and value <= now:
                        user_id = int(key.split("_")[-1])
                        try:
                            await guild.unban(
                                discord.Object(id=user_id),
                                reason="Temporary ban expired",
                            )
                        except discord.NotFound:
                            pass  # user not banned or already unbanned
                        await self.bot.config_manager.delete_config_key(guild, key)

                    elif key.startswith("ticketban_") and value <= now:
                        await self.bot.config_manager.delete_config_key(guild, key)

            except Exception:
                # Prevent one guild's bad state from killing the loop.
                continue

    @_check_expired_punishments.before_loop
    async def _before_check_expired(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Helpers
    # ==================================================================
    async def _require_punishments_channel(self, guild: discord.Guild) -> discord.TextChannel:
        cid = await self.bot.config_manager.get_mapped_channel(guild, "punishments")
        if cid is None:
            raise commands.CheckFailure(
                "No `punishments` channel configured. Use `?manage setchannel punishments <id>`."
            )
        channel = guild.get_channel(cid)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise commands.CheckFailure(
                "The configured `punishments` channel no longer exists."
            )
        return channel

    async def _require_warn_channel(self, guild: discord.Guild) -> discord.TextChannel:
        cid = await self.bot.config_manager.get_mapped_channel(guild, "warn")
        if cid is None:
            raise commands.CheckFailure(
                "No `warn` channel configured. Use `?manage setchannel warn <id>`."
            )
        channel = guild.get_channel(cid)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise commands.CheckFailure(
                "The configured `warn` channel no longer exists."
            )
        return channel

    async def _require_general_channel(self, guild: discord.Guild) -> discord.TextChannel:
        cid = await self.bot.config_manager.get_mapped_channel(guild, "general")
        if cid is None:
            raise commands.CheckFailure(
                "No `general` channel configured. Use `?manage setchannel general <id>`."
            )
        channel = guild.get_channel(cid)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise commands.CheckFailure(
                "The configured `general` channel no longer exists."
            )
        return channel

    async def _log_punishment(
        self,
        guild: discord.Guild,
        user_id: int,
        action: str,
        length: str,
        reason: str,
        bold_reason: bool = False,
    ):
        """Post an exact-text punishment log to the #punishments channel."""
        channel = await self._require_punishments_channel(guild)
        if bold_reason:
            text = (
                f"@{user_id} has been {action} for {length}. "
                f"The reason specified is **{reason}**."
            )
        else:
            text = (
                f"@{user_id} has been {action} for {length}. "
                f"The reason specified is {reason}."
            )
        await channel.send(text)

    async def _count_warnings_since_reset(
        self, warn_channel: discord.TextChannel, user_id: int
    ) -> list:
        """Return warning dicts for a user since the most recent reset marker."""
        warnings = []
        async for message in warn_channel.history(oldest_first=False):
            try:
                data = json.loads(message.content)
                if data.get("warn_reset") == user_id:
                    break
                warn_data = data.get("warn")
                if warn_data and warn_data.get("user_id") == user_id:
                    warnings.append(warn_data)
            except (json.JSONDecodeError, AttributeError):
                continue
        warnings.reverse()  # chronological order
        return warnings

    # ==================================================================
    # Base group
    # ==================================================================
    @commands.group(invoke_without_command=True)
    async def manage(self, ctx: commands.Context):
        """Base configuration / moderation command."""
        if ctx.invoked_subcommand is None:
            await ctx.send(
                "**Available subcommands:** `setrole`, `setchannel`, `setcategory`, "
                "`ban`, `mute`, `ticketban`, `warn`, `checkwarn`, `history`, `purge`\n"
                "Use `?help manage <subcommand>` for detailed usage."
            )

    # ==================================================================
    # setrole — ONLY Server Owner, forever
    # ==================================================================
    @manage.command()
    @is_server_owner()
    async def setrole(self, ctx: commands.Context, command: str, role: discord.Role):
        """Authorise a role to use a command group. Server Owner only.

        **Usage:** `?manage setrole <command> <@role>`
        **Examples:**
        `?manage setrole manage @Moderator`
        `?manage setrole tickets @Support Team`
        """
        await self.bot.config_manager.save_config(ctx.guild, f"role_{command}", role.id)
        await ctx.send(
            f"✅ {ctx.author.mention} — {role.mention} is now authorised for `{command}` commands."
        )

    # ==================================================================
    # setchannel — authorised 'manage' role (or Server Owner)
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def setchannel(self, ctx: commands.Context, outputchannel: str, channelid: int):
        """Map a logical system channel to a physical Discord channel.

        **Supported channels:** `punishments`, `warn`, `general`

        **Usage:** `?manage setchannel <outputchannel> <channelid>`

        **Example:**
        `?manage setchannel punishments 123456789012345678`
        """
        valid = {"punishments", "warn", "general"}
        if outputchannel not in valid:
            await ctx.send(
                f"❌ Invalid system channel. Supported: `{', '.join(valid)}`"
            )
            return

        channel = ctx.guild.get_channel(channelid)
        if channel is None:
            await ctx.send("❌ Channel not found. Provide a valid numeric channel ID.")
            return

        await self.bot.config_manager.save_config(
            ctx.guild, f"channel_{outputchannel}", channelid
        )
        await ctx.send(f"✅ Mapped `{outputchannel}` to {channel.mention} (`{channelid}`).")

    # ==================================================================
    # setcategory — authorised 'manage' role (or Server Owner)
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def setcategory(self, ctx: commands.Context, outputcategory: str, categoryid: int):
        """Map a logical system category to a physical Discord category.

        **Usage:** `?manage setcategory <outputcategory> <categoryid>`

        **Example:**
        `?manage setcategory tickets 123456789012345678`
        """
        category = discord.utils.get(ctx.guild.categories, id=categoryid)
        if category is None:
            await ctx.send("❌ Category not found. Provide a valid numeric category ID.")
            return

        await self.bot.config_manager.save_config(
            ctx.guild, f"category_{outputcategory}", categoryid
        )
        await ctx.send(
            f"✅ Mapped `{outputcategory}` to category `{category.name}` (`{categoryid}`)."
        )

    # ==================================================================
    # ban
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def ban(self, ctx: commands.Context, userid: int, length: str, *, reason: str):
        """Ban a user from the server for a specified duration.

        **Usage:** `?manage ban <userid> <length> <reason>`

        **Examples:**
        `?manage ban 123456789 1d spam`
        `?manage ban 123456789 permanent cheating`
        """
        try:
            duration, canonical = parse_duration(length)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        member = ctx.guild.get_member(userid)
        user_display = member.mention if member else f"`{userid}`"

        await ctx.guild.ban(discord.Object(id=userid), reason=reason)

        if duration is None:
            await self._log_punishment(ctx.guild, userid, "banned", canonical, reason)
        else:
            await self._log_punishment(ctx.guild, userid, "banned", canonical, reason)
            # Store temp-ban expiry so the background task can unban.
            expires_at = int((datetime.now(timezone.utc) + duration).timestamp())
            await self.bot.config_manager.save_config(
                ctx.guild, f"temp_ban_{userid}", expires_at
            )

        await ctx.send(f"🔨 {user_display} has been banned for **{canonical}**.")

    # ==================================================================
    # mute (Discord native timeout)
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def mute(self, ctx: commands.Context, userid: int, length: str, *, reason: str):
        """Timeout a user using Discord's native timeout feature.

        **Usage:** `?manage mute <userid> <length> <reason>`

        **Example:**
        `?manage mute 123456789 2h excessive caps`
        """
        try:
            duration, canonical = parse_duration(length)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        member = ctx.guild.get_member(userid)
        if member is None:
            await ctx.send("❌ User not found in this server.")
            return

        if duration is None:
            # Discord timeout cannot be permanent; clamp to 28 days.
            applied = timedelta(days=28)
            canonical += " (clamped to 28 days)"
        else:
            applied = _clamp_timeout(duration)
            if applied != duration:
                canonical += " (clamped to 28 days)"

        until = datetime.now(timezone.utc) + applied
        await member.timeout(until, reason=reason)

        await self._log_punishment(
            ctx.guild, userid, "muted", canonical, reason, bold_reason=True
        )
        await ctx.send(f"🔇 {member.mention} has been muted for **{canonical}**.")

    # ==================================================================
    # ticketban
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def ticketban(self, ctx: commands.Context, userid: int, length: str, *, reason: str):
        """Prevent a user from creating support tickets for a duration.

        **Usage:** `?manage ticketban <userid> <length> <reason>`

        **Example:**
        `?manage ticketban 123456789 1mo abuse`
        """
        try:
            duration, canonical = parse_duration(length)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        if duration is not None:
            expires_at = int((datetime.now(timezone.utc) + duration).timestamp())
            await self.bot.config_manager.save_config(
                ctx.guild, f"ticketban_{userid}", expires_at
            )
        else:
            # Permanent — store a sentinel far in the future (or "permanent").
            await self.bot.config_manager.save_config(
                ctx.guild, f"ticketban_{userid}", "permanent"
            )

        await self._log_punishment(
            ctx.guild, userid, "ticket banned", canonical, reason, bold_reason=True
        )
        await ctx.send(f"🎫 User `{userid}` has been ticket banned for **{canonical}**.")

    # ==================================================================
    # warn
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def warn(self, ctx: commands.Context, userid: int, *, reason: str):
        """Issue a warning to a user. Triggers auto-ban on the 3rd active warning.

        **Usage:** `?manage warn <userid> <reason>`

        **Example:**
        `?manage warn 123456789 spam`
        """
        warn_channel = await self._require_warn_channel(ctx.guild)
        general_channel = await self._require_general_channel(ctx.guild)

        # Count existing warnings (most-recent-first until a reset marker).
        current = await self._count_warnings_since_reset(warn_channel, userid)

        # Log the new warning into #warn.
        payload = json.dumps({"warn": {"user_id": userid, "reason": reason}})
        await warn_channel.send(payload)

        # Attempt DM.
        dm_text = f"**You have been warned.** You have been warned for the reason of {reason}."
        user = self.bot.get_user(userid)
        if user is None:
            try:
                user = await self.bot.fetch_user(userid)
            except discord.NotFound:
                user = None
        dm_sent = False
        if user:
            try:
                await user.send(dm_text)
                dm_sent = True
            except discord.Forbidden:
                pass

        if not dm_sent:
            await general_channel.send(f"{user.mention if user else f'<@{userid}>'} {dm_text}")

        # 3-Warn escalation.
        if len(current) >= 2:
            # This is the 3rd active warning — reset and auto-ban for 1 day.
            reset_payload = json.dumps({"warn_reset": userid})
            await warn_channel.send(reset_payload)

            ban_delta = timedelta(days=1)
            await ctx.guild.ban(
                discord.Object(id=userid),
                reason="Warning limit reached (3 warns)",
            )
            expires_at = int((datetime.now(timezone.utc) + ban_delta).timestamp())
            await self.bot.config_manager.save_config(
                ctx.guild, f"temp_ban_{userid}", expires_at
            )

            await self._log_punishment(
                ctx.guild,
                userid,
                "banned",
                "1 day",
                "warning limit",
                bold_reason=True,
            )
            await ctx.send(
                f"⚠️ {user.mention if user else f'<@{userid}>'} reached 3 warnings and has been **auto-banned for 1 day**.")
        else:
            await ctx.send(
                f"⚠️ {user.mention if user else f'<@{userid}>'} has been warned. "
                f"Active warnings: **{len(current) + 1}**/3."
            )

    # ==================================================================
    # checkwarn
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def checkwarn(self, ctx: commands.Context, userid: int):
        """Display a user's active warnings in chronological order.

        **Usage:** `?manage checkwarn <userid>`
        """
        warn_channel = await self._require_warn_channel(ctx.guild)
        warnings = await self._count_warnings_since_reset(warn_channel, userid)

        user = self.bot.get_user(userid)
        if user is None:
            try:
                user = await self.bot.fetch_user(userid)
            except discord.NotFound:
                user = None
        mention = user.mention if user else f"<@{userid}>"

        if not warnings:
            await ctx.send(f"✅ {mention} has **0** active warnings.")
            return

        lines = [f"Warnings for {mention} — **{len(warnings)}** total:\n"]
        for idx, warn in enumerate(warnings, start=1):
            lines.append(f"{idx}. {warn['reason']}")

        await ctx.send("\n".join(lines))

    # ==================================================================
    # history
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def history(self, ctx: commands.Context, userid: int):
        """Display punishment history (bans, mutes, ticketbans) for a user.

        **Usage:** `?manage history <userid>`
        """
        punishments_channel = await self._require_punishments_channel(ctx.guild)

        user = self.bot.get_user(userid)
        if user is None:
            try:
                user = await self.bot.fetch_user(userid)
            except discord.NotFound:
                user = None
        mention = user.mention if user else f"<@{userid}>"

        entries = []
        async for message in punishments_channel.history(oldest_first=True):
            for pattern in _PUNISHMENT_PATTERNS:
                match = pattern.match(message.content)
                if match:
                    matched_user_id = int(match.group(1))
                    if matched_user_id == userid:
                        action = match.group(2)
                        length = match.group(3)
                        reason = match.group(4)
                        entries.append(
                            f"• **{action}** for `{length}` — reason: {reason}"
                        )
                    break

        if not entries:
            await ctx.send(f"✅ No punishment history found for {mention}.")
            return

        header = f"Punishment history for {mention} — **{len(entries)}** entries:\n"
        await ctx.send(header + "\n".join(entries))

    # ==================================================================
    # purge
    # ==================================================================
    @manage.command()
    @has_authorized_role("manage")
    async def purge(self, ctx: commands.Context, userid: int, length: str):
        """Delete recent messages sent by a specific user.

        Bulk-deletion is capped at 14 days due to Discord API limits.

        **Usage:** `?manage purge <userid> <length>`

        **Example:**
        `?manage purge 123456789 1d`
        """
        try:
            duration, canonical = parse_duration(length)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        max_api_age = timedelta(days=14)
        if duration is None:
            effective = max_api_age
            note = " (capped to 14 days)"
        elif duration > max_api_age:
            effective = max_api_age
            note = " (capped to 14 days)"
        else:
            effective = duration
            note = ""

        after = datetime.now(timezone.utc) - effective

        def _is_target(message: discord.Message) -> bool:
            return message.author.id == userid

        try:
            deleted = await ctx.channel.purge(limit=1000, after=after, check=_is_target)
        except discord.HTTPException as exc:
            await ctx.send(f"❌ Failed to purge messages: `{exc}`")
            return

        await ctx.send(
            f"🧹 Deleted **{len(deleted)}** message(s) by `{userid}` "
            f"in the last **{canonical}**{note}."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Manage(bot))
