"""依存ポリシー（pyproject.toml / uv.lock の運用規約）の機械検証。

- waiver 期限: `[tool.uv.exclude-newer-package]` は公開 7 日未満の修正版を先取りする一時措置。
  外せる日の 7 日後を `[tool.dependency-waivers]` に持ち、超過したら CI を赤にする。
- core numpy 2.x: lock の全面再生成で numpy が 1.26 へ崩れる退行を CI の dev 環境で検知する。
"""

from __future__ import annotations

import importlib.util
import tomllib
from datetime import date, datetime, timezone
from importlib.metadata import version
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
FIX = "pyproject の [tool.uv.exclude-newer-package] と [tool.dependency-waivers] から削除して `uv lock`"


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else datetime.fromisoformat(value).date()


def waiver_problems(waivers: dict, deadlines: dict, today: date) -> list[str]:
    """waiver 表と期限表の不整合（期限なし・対応 waiver なし・期限切れ）を列挙する。"""
    problems = [
        f"{p}: 期限が未登録（[tool.dependency-waivers] に追加するか {FIX}）"
        for p in sorted(set(waivers) - set(deadlines))
    ]
    for pkg, deadline in sorted(deadlines.items()):
        if pkg not in waivers:
            problems.append(f"{pkg}: 期限だけ残っている（[tool.dependency-waivers] から削除）")
        elif _as_date(deadline) < today:
            problems.append(f"{pkg}: 期限 {_as_date(deadline)} 超過（{FIX}）")
    return problems


def test_dependency_waivers_have_deadlines_and_are_not_expired() -> None:
    uv_tool = tomllib.loads(PYPROJECT.read_text(encoding="utf-8")).get("tool", {})
    waivers = uv_tool.get("uv", {}).get("exclude-newer-package", {})
    deadlines = uv_tool.get("dependency-waivers", {})
    problems = waiver_problems(waivers, deadlines, datetime.now(timezone.utc).date())
    assert not problems, "依存 waiver の期限管理違反: " + " / ".join(problems)


def test_waiver_problems_reports_missing_stale_and_expired() -> None:
    today = date(2026, 10, 30)
    waivers = {
        n: "2026-10-20T00:00:00Z" for n in ("no-deadline", "expired", "due-today", "iso-str")
    }
    deadlines = {
        "expired": date(2026, 10, 29),
        "due-today": datetime(2026, 10, 30, 12),
        "iso-str": "2026-11-01",
        "stale": date(2026, 11, 5),
    }
    problems = waiver_problems(waivers, deadlines, today)
    assert [p.split(":")[0] for p in problems] == ["no-deadline", "expired", "stale"]
    assert waiver_problems({}, {}, today) == []


def test_core_numpy_is_2x() -> None:
    """CI の dev 環境の numpy は 2.x でなければならない。

    laion-clap>=1.1.7 が numpy<2 を要求するため、uv.lock を消しての再生成や `uv lock --upgrade`
    （全更新）は全 fork の numpy を 1.26 へ引き下げ、`pip install -e ".[dev]"` の解（2.x）と
    数値が乖離する。既存 lock からの relock / `--upgrade-package` は 2.x を保つ。
    """
    for module in ("laion_clap", "basic_pitch"):  # semantic-embed / pitch fork は numpy<2 が正当
        if importlib.util.find_spec(module) is not None:
            pytest.skip(f"{module} 導入環境は numpy<2 を正当に固定する")
    assert version("numpy").split(".")[0] == "2", (
        f"core の numpy が {version('numpy')}。uv.lock が全面再生成されて numpy 1.26 に崩れた可能性が高い"
        "（pyproject の [tool.uv] コメントの検出コマンドで確認し、既存 lock から relock し直す）"
    )
