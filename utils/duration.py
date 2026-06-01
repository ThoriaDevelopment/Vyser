"""
Duration parser for timed punishments.

Supports: [num]s, [num]m, [num]h, [num]d, [num]mo, [num]y, or `permanent`.
"""

import re
from datetime import timedelta

DURATION_RE = re.compile(r"^(\d+)(s|m|h|d|mo|y)$", re.IGNORECASE)


def parse_duration(text: str) -> tuple:
    """Parse a duration string into a timedelta and canonical text.

    Returns (timedelta, canonical_text) or (None, "permanent").
    Raises ValueError on invalid input.
    """
    text = text.strip().lower()
    if text == "permanent":
        return None, "permanent"

    match = DURATION_RE.match(text)
    if not match:
        raise ValueError(
            f"Invalid duration `{text}`. Use: `1s`, `1m`, `1h`, `1d`, `1mo`, `1y`, or `permanent`."
        )

    num = int(match.group(1))
    unit = match.group(2).lower()

    if unit == "s":
        delta = timedelta(seconds=num)
    elif unit == "m":
        delta = timedelta(minutes=num)
    elif unit == "h":
        delta = timedelta(hours=num)
    elif unit == "d":
        delta = timedelta(days=num)
    elif unit == "mo":
        delta = timedelta(days=num * 30)
    elif unit == "y":
        delta = timedelta(days=num * 365)
    else:
        raise ValueError(f"Unknown duration unit: {unit}")

    return delta, text


def format_timedelta(td: timedelta) -> str:
    """Return a human-readable string for a timedelta (e.g. '1 day', '2 hours')."""
    total_seconds = int(td.total_seconds())
    if total_seconds <= 0:
        return "0 seconds"

    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return ", ".join(parts)
