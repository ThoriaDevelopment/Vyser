"""
Custom command checks for Vyser Bot.

All permission gating is dynamic: roles are resolved at runtime via the
ConfigManager (stateless Discord-channel storage). The literal Server Owner
always bypasses every check.
"""

from discord.ext import commands


def is_server_owner():
    """Restrict a command to the literal Guild Owner indefinitely.

    This is used for ?manage setrole and other commands that must never
    be delegated.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")

        if ctx.guild.owner_id != ctx.author.id:
            raise commands.CheckFailure("This command is restricted to the Server Owner.")

        return True

    return commands.check(predicate)


def in_active_ticket():
    """Restrict a command to channels that are tracked as active tickets.

    Looks up the current channel ID inside the stateless config store
    (active_ticket_* entries). Used for ?ticket close / add / remove.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")

        config = await ctx.bot.config_manager.load_config(ctx.guild)
        for key, value in config.items():
            if key.startswith("active_ticket_") and value == ctx.channel.id:
                return True

        raise commands.CheckFailure(
            "This command can only be used inside an active ticket channel."
        )

    return commands.check(predicate)


def has_authorized_role(command_name: str):
    """Restrict a command to users holding the role authorised for *command_name*.

    The Server Owner bypass is unconditional. If no role has been configured
    yet, the check fails with instructions on how to set it up.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")

        # Server Owner bypass — unconditional, overrides all role checks.
        if ctx.guild.owner_id == ctx.author.id:
            return True

        role_id = await ctx.bot.config_manager.get_role_for_command(ctx.guild, command_name)
        if role_id is None:
            raise commands.CheckFailure(
                f"No authorised role configured for `{command_name}`. "
                f"Use `?manage setrole {command_name} @role` first."
            )

        role = ctx.guild.get_role(role_id)
        if role is None:
            raise commands.CheckFailure(
                f"The authorised role for `{command_name}` no longer exists. "
                f"Ask the Server Owner to re-run `?manage setrole`."
            )

        if role not in ctx.author.roles:
            raise commands.CheckFailure(
                f"You need the `{role.name}` role to use this command."
            )

        return True

    return commands.check(predicate)
