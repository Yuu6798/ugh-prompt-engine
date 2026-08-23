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
def _stub_module_if_missing(module_name: str) -> Iterator[None]:
    if module_name in sys.modules or importlib.util.find_spec(module_name) is not None:
        yield
        return

    stub = types.ModuleType(module_name)
    sys.modules[module_name] = stub
    try:
        yield
    finally:
        if sys.modules.get(module_name) is stub:
            del sys.modules[module_name]


@contextmanager
def stub_onnxruntime_if_missing() -> Iterator[None]:
    """Make gate_synth importable without pretending inference is available."""
    with _stub_module_if_missing("onnxruntime"):
        yield


@contextmanager
def stub_pyworld_if_missing() -> Iterator[None]:
    """Make pure Foundry helpers importable without pretending WORLD is available."""
    with _stub_module_if_missing("pyworld"):
        yield


def optional_runtime_available(module_name: str) -> bool:
    """Report real runtime availability after an import-only stub is removed."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False
