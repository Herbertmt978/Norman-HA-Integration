"""Tests for shared Norman entity naming helpers."""

from dataclasses import dataclass
import unittest

from custom_components.norman_gen1 import helpers


@dataclass(frozen=True)
class _Motor:
    id: int
    name: str
    level: int
    sort_order: int | None = None


class TestBatteryMotorNames(unittest.TestCase):
    """Keep physical-motor diagnostics aligned with commandable panels."""

    def test_single_motors_use_their_group_labels(self) -> None:
        motors = [
            _Motor(31, "Id abcd", 1),
            _Motor(32, "panel beta", 2),
            _Motor(33, "Installer panel 3", 3),
        ]

        self.assertEqual(
            helpers.battery_motor_labels(
                ["Panel Alpha", "Panel Beta", "Window bay"],
                motors,
                [1, 2, 3],
            ),
            {
                31: helpers.BatteryMotorLabel("Panel Alpha"),
                32: helpers.BatteryMotorLabel("Panel Beta"),
                33: helpers.BatteryMotorLabel("Window bay"),
            },
        )

    def test_shared_group_uses_hub_order_before_window_id(self) -> None:
        motors = [
            _Motor(11, "Right panel", 1, 2),
            _Motor(12, "Left panel", 1, 1),
        ]

        self.assertEqual(
            helpers.battery_motor_labels(["Window bay"], motors, [1]),
            {
                11: helpers.BatteryMotorLabel("Window bay", 2),
                12: helpers.BatteryMotorLabel("Window bay", 1),
            },
        )

    def test_unlevelled_motor_keeps_its_normalized_hub_name(self) -> None:
        motors = [_Motor(44, "  Skylight   left ", -1)]

        self.assertEqual(
            helpers.battery_motor_labels([], motors, []),
            {44: helpers.BatteryMotorLabel("Skylight left")},
        )

    def test_missing_group_name_returns_translatable_structure(self) -> None:
        motors = [
            _Motor(51, "Internal A", 1, 1),
            _Motor(52, "Internal B", 1, 2),
        ]

        self.assertEqual(
            helpers.battery_motor_labels([], motors, [1]),
            {
                51: helpers.BatteryMotorLabel(None, 1, 1),
                52: helpers.BatteryMotorLabel(None, 2, 1),
            },
        )

    def test_blank_unlevelled_name_returns_unassigned_structure(self) -> None:
        motors = [_Motor(61, "   ", -1)]

        self.assertEqual(
            helpers.battery_motor_labels([], motors, []),
            {61: helpers.BatteryMotorLabel(None)},
        )


if __name__ == "__main__":
    unittest.main()
