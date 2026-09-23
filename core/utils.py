"""Small reusable helpers for modules."""

from __future__ import annotations

from datetime import timedelta


def format_uptime(seconds: float) -> str:
    """Format a duration as ``Xd Xh Xm Xs``."""
    total = max(0, int(seconds))
    delta = timedelta(seconds=total)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if secs or not parts:
        parts.append(f"{secs}с")
    return " ".join(parts)
