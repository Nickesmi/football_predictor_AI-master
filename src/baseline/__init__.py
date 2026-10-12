"""Frozen football baseline: version switch, frozen artifacts, manifest and integrity checks.

See docs/BASELINE_FREEZE_DESIGN.md. Nothing here changes behaviour unless
BASELINE_FREEZE is set (src.config.BASELINE_FREEZE).
"""
from src.baseline.state import (  # noqa: F401
    BaselineFrozenError,
    BaselineIntegrityError,
    active_version,
    assert_mutation_allowed,
    integrity_report,
    is_frozen,
    require_official_integrity,
)
