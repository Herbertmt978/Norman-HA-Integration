"""Keep Norman's schemas on the engine selected by Home Assistant."""

from importlib import import_module

from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
import voluptuous as vol

from custom_components.norman_gen1 import config_flow, rf_learning
from custom_components.norman_gen1.gen2 import cover


def test_schema_engine_identity():
    """Forms and entity services use HA's exact schema and marker classes."""
    assert config_flow.vol is vol
    assert rf_learning.vol is vol
    assert cover.vol is vol

    if (MAJOR_VERSION, MINOR_VERSION) >= (2026, 9):
        probatio = import_module("probatio")
        assert vol.Schema is probatio.Schema
        assert vol.Required is probatio.Required
        assert vol.Marker is probatio.Marker
    else:
        assert vol.Schema.__module__ == "voluptuous.schema_builder"
