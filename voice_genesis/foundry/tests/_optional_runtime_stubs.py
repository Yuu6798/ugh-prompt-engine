"""Test-only import stubs for optional Foundry runtimes.

The stub exists only while a module is imported.  It is removed immediately so
other tests still observe the real optional-dependency availability.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import sys
import types
from typing import Iterator


@contextmanager
def stub_onnxruntime_if_missing() -> Iterator[None]:
    """Make gate_synth importable without pretending inference is available."""
    if "onnxruntime" in sys.modules or importlib.util.find_spec("onnxruntime") is not None:
        yield
        return

    stub = types.ModuleType("onnxruntime")
    sys.modules["onnxruntime"] = stub
    try:
        yield
    finally:
        if sys.modules.get("onnxruntime") is stub:
            del sys.modules["onnxruntime"]
