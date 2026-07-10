"""Test release metadata and factory-default invariants."""

import json
from pathlib import Path

from custom_components.norman_gen1.const import DEFAULT_PASSWORD

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "norman_gen1"


def _json(path: Path) -> dict:
    """Load one repository JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def _png_size(path: Path) -> tuple[int, int]:
    """Return the dimensions stored in a PNG's IHDR chunk."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return int.from_bytes(data[16:20]), int.from_bytes(data[20:24])


def test_factory_password_is_the_exact_documented_default() -> None:
    """Prevent an accidental replacement of the required factory credential."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert DEFAULT_PASSWORD == "123456789"
    assert "The Norman Gen 1 factory password is:" in readme
    assert "123456789" in readme


def test_release_metadata_matches_supported_runtime() -> None:
    """Keep HACS, manifest, and release documentation in sync."""
    manifest = _json(INTEGRATION / "manifest.json")
    hacs = _json(ROOT / "hacs.json")

    assert manifest["version"] == "0.2.1"
    assert manifest["domain"] == "norman_gen1"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert hacs["homeassistant"] == "2024.11.0"


def test_local_brand_assets_are_valid() -> None:
    """Keep the bundled custom-integration icon and logo valid."""
    brand = INTEGRATION / "brand"

    assert _png_size(brand / "icon.png") == (256, 256)
    assert _png_size(brand / "logo.png") == (600, 200)


def test_english_translation_matches_source_strings() -> None:
    """Keep the bundled English translation synchronized with strings.json."""
    assert _json(INTEGRATION / "strings.json") == _json(
        INTEGRATION / "translations" / "en.json"
    )


def test_ci_keeps_current_and_minimum_home_assistant_gates() -> None:
    """Protect the compatibility matrix and Core-level coverage threshold."""
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    assert "2024.11 minimum" in workflow
    assert "--fail-under=95" in workflow
    assert "requirements_ha_current.txt" in workflow
