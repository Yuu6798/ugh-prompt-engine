"""tests/test_learned_models_policy.py — adoption-matrix / pyproject extras sync.

Every `Adopt` entry in `docs/learned_models_policy.md` Section 3.1 that
names an optional-dependency extra (via the "extra `<name>`" convention)
must have a matching entry in `pyproject.toml`'s
`[project.optional-dependencies]`. This is a discipline-style sync test
in the spirit of `tests/discipline/` — it catches drift between the
policy doc's adoption matrix and the actual installable extras, without
being so strict that ordinary prose edits to the doc break it
spuriously (hence the tolerant regex rather than a full markdown parse).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_DOC = ROOT / "docs" / "learned_models_policy.md"
PYPROJECT = ROOT / "pyproject.toml"

# Backticks around the extra name are required so ordinary prose containing
# the bare word "extra" (e.g. "so pipelines without the extra still work")
# is never mistaken for a named extra.
_EXTRA_PATTERN = re.compile(r"extra `([a-z][a-z0-9-]*)`")


def _adopt_section_text() -> str:
    text = POLICY_DOC.read_text(encoding="utf-8")
    start = text.index("### 3.1 Adopt")
    end = text.index("### 3.2 Reject")
    return text[start:end]


def _named_extras() -> set[str]:
    return set(_EXTRA_PATTERN.findall(_adopt_section_text()))


def _pyproject_extras() -> set[str]:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return set(data["project"]["optional-dependencies"].keys())


class TestAdoptionMatrixExtrasSync:
    def test_at_least_one_extra_is_named_in_adopt_section(self):
        # Sanity guard: if this ever hits zero, the doc structure or the
        # "extra `<name>`" convention drifted and the sync check below
        # would otherwise pass vacuously.
        assert _named_extras(), "no 'extra `<name>`' mentions found in Section 3.1 Adopt"

    def test_every_named_extra_exists_in_pyproject(self):
        named = _named_extras()
        available = _pyproject_extras()
        missing = named - available
        assert not missing, (
            "docs/learned_models_policy.md Section 3.1 names extras not declared "
            f"in pyproject.toml [project.optional-dependencies]: {sorted(missing)}"
        )

    def test_semantic_embed_extra_is_declared(self):
        assert "semantic-embed" in _pyproject_extras()
        assert "laion-clap" in " ".join(
            str(dep)
            for dep in tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
                "optional-dependencies"
            ]["semantic-embed"]
        )
