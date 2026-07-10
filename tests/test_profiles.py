"""Test canonical numeric shutter profiles and v0.2 migration."""

from __future__ import annotations

import unittest

from custom_components.norman_gen1.api import (
    NormanRoom,
    group_target_id,
    room_target_id,
)
from custom_components.norman_gen1.const import (
    CONF_CLOSE_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_DEFAULT_OPEN_POSITION,
    CONF_KNOWN_TARGETS,
    CONF_OPEN_POSITION,
    CONF_POSITION_PROFILES,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
)
from custom_components.norman_gen1.profiles import (
    configured_group_levels,
    make_position_profile,
    migrate_legacy_profile_options,
    resolve_configured_profile,
)


def _room(room_id: int, style: int) -> NormanRoom:
    return NormanRoom(
        id=room_id,
        name=f"Room {room_id}",
        group_names=["Panel"],
        raw={"Style": style},
    )


class TestConfiguredProfiles(unittest.TestCase):
    def test_new_default_is_open_37_and_closed_100(self) -> None:
        profile = resolve_configured_profile({}, 1)

        self.assertEqual(profile.open_position, 37)
        self.assertEqual(profile.close_position, 100)
        self.assertTrue(profile.closes_at_both_ends)

    def test_panel_override_precedes_room_then_global(self) -> None:
        options = {
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {
                room_target_id(1): {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                },
                group_target_id(1, 2): {
                    CONF_OPEN_POSITION: 61,
                    CONF_CLOSE_POSITION: 100,
                },
            },
        }

        self.assertEqual(
            resolve_configured_profile(options, 1, 2),
            make_position_profile(61, 100),
        )
        self.assertEqual(
            resolve_configured_profile(options, 1, 3),
            make_position_profile(42, 0),
        )
        self.assertEqual(
            resolve_configured_profile(options, 9),
            make_position_profile(37, 100),
        )

    def test_invalid_stored_values_fall_back_without_crashing(self) -> None:
        options = {
            CONF_DEFAULT_OPEN_POSITION: True,
            CONF_DEFAULT_CLOSE_POSITION: 50,
            CONF_POSITION_PROFILES: {
                room_target_id(1): {
                    CONF_OPEN_POSITION: "not-a-number",
                    CONF_CLOSE_POSITION: -1,
                }
            },
        }

        self.assertEqual(
            resolve_configured_profile(options, 1),
            make_position_profile(37, 100),
        )

    def test_profile_validation_rejects_equal_or_non_endpoint_close(self) -> None:
        for values in ((37, 37), (37, 50), (-1, 100), (101, 0), (True, 100)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                make_position_profile(*values)

    def test_configured_group_levels_ignore_other_or_malformed_targets(self) -> None:
        options = {
            CONF_POSITION_PROFILES: {
                group_target_id(1, 2): {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                },
                group_target_id(2, 3): {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                },
                "group:1:not-an-integer": {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                },
            }
        }

        self.assertEqual(configured_group_levels(options, 1), {2})


class TestLegacyMigration(unittest.TestCase):
    def test_style_profiles_are_preserved_and_new_defaults_are_37_100(self) -> None:
        migrated = migrate_legacy_profile_options(
            {},
            [_room(1, 3), _room(2, 13), _room(3, 99)],
            {1: [1], 2: [1], 3: [1]},
        )

        self.assertEqual(migrated[CONF_DEFAULT_OPEN_POSITION], 37)
        self.assertEqual(migrated[CONF_DEFAULT_CLOSE_POSITION], 100)
        self.assertEqual(
            migrated[CONF_POSITION_PROFILES],
            {
                room_target_id(1): {
                    CONF_OPEN_POSITION: 37,
                    CONF_CLOSE_POSITION: 0,
                },
                room_target_id(3): {
                    CONF_OPEN_POSITION: 100,
                    CONF_CLOSE_POSITION: 0,
                },
            },
        )
        self.assertEqual(
            resolve_configured_profile(migrated, 2),
            make_position_profile(37, 100),
        )

    def test_explicit_legacy_room_and_panel_choices_are_preserved(self) -> None:
        options = {
            CONF_TILT_OPEN_TARGETS: [room_target_id(1), group_target_id(2, 1)],
            CONF_REVERSED_CLOSE_TARGETS: [group_target_id(1, 2)],
            CONF_KNOWN_TARGETS: [
                room_target_id(1),
                group_target_id(1, 1),
                group_target_id(1, 2),
                room_target_id(2),
                group_target_id(2, 1),
            ],
        }

        migrated = migrate_legacy_profile_options(
            options,
            [_room(1, 99), _room(2, 99)],
            {1: [1, 2], 2: [1]},
        )

        self.assertEqual(
            resolve_configured_profile(migrated, 1), make_position_profile(37, 0)
        )
        self.assertEqual(
            resolve_configured_profile(migrated, 1, 1),
            make_position_profile(37, 0),
        )
        self.assertEqual(
            resolve_configured_profile(migrated, 1, 2),
            make_position_profile(37, 100),
        )
        self.assertEqual(
            resolve_configured_profile(migrated, 2),
            make_position_profile(100, 0),
        )
        self.assertEqual(
            resolve_configured_profile(migrated, 2, 1),
            make_position_profile(37, 0),
        )

    def test_empty_legacy_lists_explicitly_disable_style_13_defaults(self) -> None:
        options = {
            CONF_TILT_OPEN_TARGETS: [],
            CONF_REVERSED_CLOSE_TARGETS: [],
            CONF_KNOWN_TARGETS: [room_target_id(1)],
        }

        migrated = migrate_legacy_profile_options(
            options,
            [_room(1, 13)],
            {1: [1]},
        )

        self.assertEqual(
            resolve_configured_profile(migrated, 1),
            make_position_profile(100, 0),
        )

    def test_migration_is_idempotent_and_removes_legacy_keys(self) -> None:
        legacy = {
            CONF_TILT_OPEN_TARGETS: [],
            CONF_REVERSED_CLOSE_TARGETS: [],
            CONF_KNOWN_TARGETS: [room_target_id(1)],
            "unrelated": "preserved",
        }
        first = migrate_legacy_profile_options(legacy, [_room(1, 13)], {1: [1]})
        second = migrate_legacy_profile_options(first, [_room(1, 13)], {1: [1]})

        self.assertEqual(first, second)
        self.assertEqual(first["unrelated"], "preserved")
        self.assertNotIn(CONF_TILT_OPEN_TARGETS, first)
        self.assertNotIn(CONF_REVERSED_CLOSE_TARGETS, first)
        self.assertNotIn(CONF_KNOWN_TARGETS, first)

    def test_temporarily_missing_legacy_targets_keep_explicit_profiles(self) -> None:
        options = {
            CONF_TILT_OPEN_TARGETS: [room_target_id(99)],
            CONF_REVERSED_CLOSE_TARGETS: [group_target_id(99, 1)],
            CONF_KNOWN_TARGETS: [room_target_id(99), group_target_id(99, 1)],
        }

        migrated = migrate_legacy_profile_options(options, [], {})

        self.assertEqual(
            migrated[CONF_POSITION_PROFILES],
            {
                room_target_id(99): {
                    CONF_OPEN_POSITION: 37,
                    CONF_CLOSE_POSITION: 0,
                },
                group_target_id(99, 1): {
                    CONF_OPEN_POSITION: 37,
                    CONF_CLOSE_POSITION: 100,
                },
            },
        )

    def test_missing_group_still_inherits_an_equal_missing_room_profile(self) -> None:
        options = {
            CONF_TILT_OPEN_TARGETS: [room_target_id(99)],
            CONF_REVERSED_CLOSE_TARGETS: [],
            CONF_KNOWN_TARGETS: [room_target_id(99), group_target_id(99, 1)],
        }

        migrated = migrate_legacy_profile_options(options, [], {})

        self.assertEqual(
            migrated[CONF_POSITION_PROFILES],
            {
                room_target_id(99): {
                    CONF_OPEN_POSITION: 37,
                    CONF_CLOSE_POSITION: 0,
                }
            },
        )

    def test_missing_room_equal_to_global_default_remains_inherited(self) -> None:
        options = {
            CONF_TILT_OPEN_TARGETS: [room_target_id(99)],
            CONF_REVERSED_CLOSE_TARGETS: [room_target_id(99)],
            CONF_KNOWN_TARGETS: [room_target_id(99)],
        }

        migrated = migrate_legacy_profile_options(options, [], {})

        self.assertEqual(migrated[CONF_POSITION_PROFILES], {})


if __name__ == "__main__":
    unittest.main()
