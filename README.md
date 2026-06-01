# Vyser Discord Bot

Vyser is a fully stateless Discord moderation, warning, and ticketing bot built with `discord.py`. All data — configurations, punishment logs, and warning history — lives inside dedicated Discord text channels. No JSON files, no SQLite databases, no environment variables for bot state.

---

## Features

- **Stateless Architecture** — All guild data is stored and retrieved by parsing message history inside hidden Discord text channels.
- **Moderation Engine** — Timed bans, mutes (Discord native timeouts), ticket bans, and message purging with automatic expiry handling.
- **Warning System** — Tracks warnings per user in chronological order with a 3-strike auto-ban escalation.
- **Ticketing System** — Users can open private support ticket channels with role-based access control for staff.
- **Server Owner Bypass** — The literal Guild Owner can run any command at any time, overriding all role checks.
- **Dynamic Role Authorisation** — Command groups can be delegated to specific roles via `?manage setrole`.

---

## Requirements

- Python **3.10+**
- `discord.py>=2.3.0`

```bash
pip install -r requirements.txt
```

---

## Setup

1. **Create a Discord Application**
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications).
   - Create a new application and navigate to the **Bot** tab.
   - Click **Reset Token** and copy it.

2. **Enable Privileged Intents**
   Under the **Bot** tab, enable both:
   - **MESSAGE CONTENT INTENT**
   - **SERVER MEMBERS INTENT**

3. **Invite the Bot**
   - OAuth2 > URL Generator > Scopes: `bot`
   - Bot Permissions: `Administrator` (recommended for full functionality)
   - Open the generated URL and invite the bot to your server.

4. **Set the Token**
   Export the token as an environment variable before starting:

   ```bash
   # PowerShell
   $env:DISCORD_TOKEN="your-token-here"

   # CMD
   set DISCORD_TOKEN=your-token-here

   # Bash
   export DISCORD_TOKEN="your-token-here"
   ```

5. **Run the Bot**

   ```bash
   python bot.py
   ```

---

## Initial Guild Configuration

After the bot joins your server, run these commands **in order** as the Server Owner:

```
?manage setrole manage @AdminRole
?manage setchannel punishments 123456789012345678
?manage setchannel warn 123456789012345678
?manage setchannel general 123456789012345678
?manage setcategory tickets 123456789012345678
?manage setrole tickets @SupportRole
```

Replace the IDs with your actual Discord channel and category IDs. Enable **Developer Mode** in Discord settings to copy IDs by right-clicking channels.

---

## Command Reference

### `?manage` — Guild Configuration & Moderation

All `?manage` subcommands (except `?manage setrole`) require the role configured via `?manage setrole manage [@role]`. The Server Owner bypasses every check unconditionally.

#### Configuration Commands

| Command | Permission | Description |
|---------|------------|-------------|
| `?manage setrole [command] [@role]` | Server Owner only | Delegates a command group (`manage`, `tickets`, etc.) to a role. |
| `?manage setchannel [outputchannel] [channelid]` | `manage` role | Maps a system channel: `punishments`, `warn`, or `general`. |
| `?manage setcategory [outputcategory] [categoryid]` | `manage` role | Maps a system category, e.g. `tickets`. |

#### Moderation Commands

| Command | Description |
|---------|-------------|
| `?manage ban [userid] [length] [reason]` | Bans a user for the specified duration. Timed bans auto-expire and lift. |
| `?manage mute [userid] [length] [reason]` | Times out a user using Discord's native timeout. Max clamped to 28 days. |
| `?manage ticketban [userid] [length] [reason]` | Prevents a user from creating tickets for the duration. |
| `?manage warn [userid] [reason]` | Issues a warning. Sends a DM (falls back to `#general`). On 3 active warnings, auto-bans for 1 day. |
| `?manage checkwarn [userid]` | Lists all active warnings for a user in chronological order. |
| `?manage history [userid]` | Displays all past bans, mutes, and ticketbans for a user from `#punishments`. |
| `?manage purge [userid] [length]` | Bulk-deletes a user's recent messages. Capped at 14 days by Discord API limits. |

**Duration Format:** `[length]` accepts `1s`, `1m`, `1h`, `1d`, `1mo`, `1y`, or `permanent`.

#### Punishment Log Format

All punishments are logged to `#punishments` in plain text so they can be parsed back by `?manage history`:

```
@123456789 has been banned for 1d. The reason specified is spam.
@123456789 has been muted for 2h. The reason specified is **excessive caps**.
@123456789 has been ticket banned for 1mo. The reason specified is **abuse**.
```

---

### `?ticket` — Ticketing System

#### Public Commands (No Role Required)

| Command | Description |
|---------|-------------|
| `?ticket help` | Shows how to open a ticket. |
| `?ticket open` | Creates a private ticket channel named `ticket-[username]` under the configured category. One active ticket per user. |

#### Management Commands (Active Ticket + `tickets` Role)

| Command | Description |
|---------|-------------|
| `?ticket close` | Permanently deletes the ticket channel and removes the active mapping. |
| `?ticket add [userid]` | Grants a user access to the current ticket channel. |
| `?ticket remove [userid]` | Revokes a user's access to the current ticket channel. |

**Ticket-Ban Check:** If a user is `?manage ticketban`ned, running `?ticket open` is blocked.

---

## Architecture

### Stateless Storage

The bot creates a hidden `vyser-config` text channel in each guild. Configuration entries are stored as JSON messages:

```json
{"role_manage": 987654321012345678}
{"channel_punishments": 123456789012345678}
{"category_tickets": 111111111111111111}
{"temp_ban_123456789": 1750000000}
{"active_ticket_123456789": 222222222222222222}
```

- `load_config()` scans message history newest-first and builds a key-value map.
- `save_config()` edits existing messages when possible to prevent channel clutter.
- `delete_config_key()` removes a message entirely.

### Timed Punishment Expiry

A background task polls every 60 seconds for expired entries:

- `temp_ban_*` entries are lifted by unbanning the user and deleting the key.
- `ticketban_*` entries are deleted when expired.

### Warning Lifecycle

Warnings are stored as JSON in `#warn`:

```json
{"warn": {"user_id": 123456789, "reason": "spam"}}
{"warn_reset": 123456789}
```

`checkwarn` counts warnings since the most recent reset marker for that user. On the 3rd active warning, a reset marker is sent and the user is auto-banned for 1 day.

---

## File Structure

```
Vyser Discord Bot/
├── bot.py              # Entry point, bot subclass, error handler
├── requirements.txt    # discord.py>=2.3.0
├── README.md           # This file
├── utils/
│   ├── config.py       # Stateless ConfigManager
│   ├── checks.py       # Permission decorators (is_server_owner, has_authorized_role, in_active_ticket)
│   └── duration.py     # Duration parser (1s, 1m, 1h, 1d, 1mo, 1y, permanent)
└── cogs/
    ├── manage.py       # ?manage configuration + moderation + warnings
    └── tickets.py      # ?ticket public + management commands
```

---

## License

MIT
