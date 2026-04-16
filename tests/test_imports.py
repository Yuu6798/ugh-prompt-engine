"""tests/test_imports.py — verify package imports work."""
from __future__ import annotations


def test_import_svp():
    import svp  # noqa: F401
    from svp import encoder, decoder  # noqa: F401


def test_import_rpe():
    import rpe  # noqa: F401
    from rpe import extractor, analyzer  # noqa: F401
