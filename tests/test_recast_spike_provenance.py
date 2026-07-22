"""Recast Phase 0 melody spike (``docs/recast_phase0_melody_spike.md``) の
provenance pin fixture 突合テスト。

AGENTS.md §8「provenance 成果物の再現レシピは全入力の pin 検証接続後に emit」
emit 前チェックリスト項目 5「pin は fixture テストで全数突合: working tree の
実 sha256 と照合し、将来の入力変更を機械検出（エントリ追加で自動拡張される
ループ実装で）」に対応する恒久ガード（PR #205 Codex P2 第2巡指摘）。

memo の provenance 節（`` `<path>` ... sha256:\\n  `<hex>` `` 形式の箇条書き/表）を
正規表現で parse し、parse された全 (path, sha256) エントリをループで working
tree の実ファイルと再ハッシュ照合する。将来 memo に pin エントリが追加・変更
されても、本テストはハードコードした個別パスではなく parse 結果に追従するため
変更なしで自動拡張される。parse 件数が 0（memo の記法変更でパースが機能しなく
なった場合など）で silent pass しないよう、件数の下限 assert も併置する
（スクリプト本体・S1 score・出力 JSON の 3 件 + スクリプトの `from svp_rpe...`
import 文実読の全数列挙による第一者測定モジュール 6 本の manifest pin で、
最低 9 件）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MEMO_PATH = Path("docs/recast_phase0_melody_spike.md")

#: 箇条書き `` `<path>`（任意の注記）sha256:\n  `<64桁hex>` `` を parse する。
#: バッククォート区切りのパスと、直後（同一行内、途中に注記が挟まってもよい）の
#: `sha256:` ラベル、その次行にインデントされたバッククォート区切りの hex 64桁を
#: 1 エントリとして拾う。新しい pin エントリが同じ記法で追加されれば自動的に
#: マッチ対象へ加わる。
PIN_PATTERN = re.compile(r"`([^`\n]+)`[^\n`]*sha256:\s*\n\s*`([0-9a-f]{64})`")

#: parse が silent に空へ縮退していないことの下限（スクリプト本体・S1 score 入力・
#: 出力 JSON の 3 件 + `from svp_rpe...` import 文実読の全数列挙による第一者測定
#: モジュール(compose/loader.py・compose/models.py・perform/performer.py・
#: perform/synth.py・rpe/models.py・rpe/physical_features.py)6 本の manifest pin
#: で、最低 9 件は常に memo に記載されている想定）。
MIN_EXPECTED_PIN_COUNT = 9


def _parse_provenance_pins(memo_text: str) -> list[tuple[str, str]]:
    return PIN_PATTERN.findall(memo_text)


def test_provenance_pins_parse_at_least_minimum_count() -> None:
    memo_text = MEMO_PATH.read_text(encoding="utf-8")
    pins = _parse_provenance_pins(memo_text)
    assert len(pins) >= MIN_EXPECTED_PIN_COUNT, (
        f"parsed only {len(pins)} (path, sha256) provenance pin(s) from {MEMO_PATH} "
        f"(expected >= {MIN_EXPECTED_PIN_COUNT}) -- if the memo's pin notation changed, "
        "PIN_PATTERN must be updated in lockstep so this doesn't silently pass empty"
    )


def test_provenance_pins_match_working_tree_sha256() -> None:
    """memo の provenance 節から parse した全 (path, sha256) エントリを working
    tree の実ファイルと再ハッシュ照合する。エントリ追加時もこのループはコード
    変更なしで新エントリを自動的に検証対象へ含める。
    """
    memo_text = MEMO_PATH.read_text(encoding="utf-8")
    pins = _parse_provenance_pins(memo_text)
    assert len(pins) >= MIN_EXPECTED_PIN_COUNT

    for raw_path, pinned_sha256 in pins:
        path = Path(raw_path)
        assert path.is_file(), (
            f"{MEMO_PATH}: provenance pin references {raw_path!r}, "
            "which does not exist in the working tree"
        )
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha256 == pinned_sha256, (
            f"{raw_path}: working tree sha256 {actual_sha256!r} does not match "
            f"{MEMO_PATH} provenance pin {pinned_sha256!r} (stale pin -- "
            "regenerate the memo's provenance block, do not edit the pin by hand)"
        )
