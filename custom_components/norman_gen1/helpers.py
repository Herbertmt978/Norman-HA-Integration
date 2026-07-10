"""Shared helpers for Norman Gen 1 entities and config flows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import NormanWindow


@dataclass(frozen=True, slots=True)
class BatteryMotorLabel:
    """Language-neutral label for one physical motor battery."""

    name: str | None
    number: int | None = None
    group_number: int | None = None


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
    name, ordinal = _group_label(group_names, level, discovered_levels)
    return name or f"Group {ordinal}"


def _group_label(
    group_names: list[str], level: int, discovered_levels: Iterable[int]
) -> tuple[str | None, int]:
    """Return a hub-provided group label and its one-based ordinal."""
    levels = sorted(set(discovered_levels))
    base = 0 if 0 in levels else 1
    index = level - base
    ordinal = index + 1 if index >= 0 else 1
    if 0 <= index < len(group_names):
        name = " ".join(group_names[index].split())
        if name:
            return name, ordinal
    return None, ordinal


def battery_motor_labels(
    group_names: list[str],
    windows: Iterable[NormanWindow],
    discovered_levels: Iterable[int],
) -> dict[int, BatteryMotorLabel]:
    """Return battery labels aligned with commandable panel groups.

    A Norman physical-window name can be an internal ID, use inconsistent
    casing, or describe an installation detail that differs from the panel
    presented by the app. The room/level relationship is the reliable join.
    Multiple physical motors can share one commandable level, so those motors
    receive deterministic suffixes ordered by the hub's per-level slot, with
    the stable window ID as a fallback and tie-breaker.
    """
    windows = list(windows)
    levels = sorted(
        set(discovered_levels)
        | {window.level for window in windows if window.level >= 0}
    )
    windows_by_level: dict[int, list[NormanWindow]] = defaultdict(list)
    labels = {}
    for window in windows:
        if window.level < 0:
            name = " ".join(window.name.split())
            labels[window.id] = BatteryMotorLabel(name or None)

    for window in windows:
        if window.level >= 0:
            windows_by_level[window.level].append(window)

    for level, grouped_windows in windows_by_level.items():
        panel_name, group_number = _group_label(group_names, level, levels)
        ordered_windows = sorted(
            grouped_windows,
            key=lambda window: (
                window.sort_order is None,
                window.sort_order if window.sort_order is not None else 0,
                window.id,
            ),
        )
        if len(ordered_windows) == 1:
            labels[ordered_windows[0].id] = BatteryMotorLabel(
                panel_name,
                group_number=group_number if panel_name is None else None,
            )
            continue
        for index, window in enumerate(ordered_windows, start=1):
            labels[window.id] = BatteryMotorLabel(
                panel_name,
                index,
                group_number if panel_name is None else None,
            )

    return labels
