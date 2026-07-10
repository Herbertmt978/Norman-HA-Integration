"""Shared helpers for Norman Gen 1 entities and config flows."""

from __future__ import annotations

from collections.abc import Iterable


def clean_label(value: str) -> str:
    """Normalize a hub-provided display label."""
    return " ".join(value.split()) or "Unnamed"


def group_name(
    group_names: list[str], level: int, discovered_levels: Iterable[int]
) -> str:
    """Return the group name paired with a discovered level.

    Pairing names with sorted discovered levels supports both zero-based and
    one-based hubs without guessing an index base from a single level value.
    """
    levels = sorted(set(discovered_levels))
    base = 0 if 0 in levels else 1
    index = level - base
    if 0 <= index < len(group_names):
        return group_names[index]
    ordinal = index + 1 if index >= 0 else 1
    return f"Group {ordinal}"
