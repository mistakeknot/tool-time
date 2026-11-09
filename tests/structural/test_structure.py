"""Tests for plugin structure."""

import sys
from pathlib import Path

# Add interverse/ to path so _shared package is importable
_interverse = Path(__file__).resolve().parents[3]
if str(_interverse) not in sys.path:
    sys.path.insert(0, str(_interverse))

from _shared.tests.structural.test_base import StructuralTests


class TestStructure(StructuralTests):
    """Structural tests -- inherits shared base, adds plugin-specific checks."""

    def test_plugin_name(self, plugin_json):
        assert plugin_json["name"] == "tool-time"

    def test_scripts_are_the_expected_set(self, project_root):
        """The shell scripts in scripts/ are exactly the named set."""
        scripts_dir = project_root / "scripts"
        assert scripts_dir.is_dir(), "Expected scripts/ directory"
        expected = {
            "bump-version.sh",
            "check-versions.sh",
            "emit-interspect-evidence.sh",
            "validate-gitleaks-waivers.sh",
        }
        found = {s.name for s in scripts_dir.glob("*.sh")}
        assert found == expected, (
            f"Scripts changed. missing: {sorted(expected - found)}, "
            f"unexpected: {sorted(found - expected)}"
        )
