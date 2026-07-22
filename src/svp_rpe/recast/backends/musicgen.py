"""MusicgenInvoker: `invocation == "local"` かつ `generator == "musicgen"` の invoker。

PR3 時点での配線判断（`git grep -il musicgen src/ scripts/` 確認結果）:
`svp_rpe` パッケージ本体に MusicGen 単発生成を呼べる最小関数は存在しない。
`scripts/collect_musicgen_takes.py` の `generate_takes` / `perform_takes` は
実推論を持つが、`GenerationPlan` / `KnobPlan`（掃引バッチ用のノブ計画）を前提と
したスクリプト専用ヘルパーであり、1 回の `PreparedInvocation` を 1 テイクへ
変換する「最小配線」の対象にはならない（バッチ実行前提のパラメータ設計を
recast の 1 backend 呼び出し契約へ押し込めるのは PR3 の「最小配線」の範囲を
超える）。よって本 PR は **スタブ**（`RecastBackendUnavailable` を送出するのみ）
を選択する — `docs/musicgen_backend.md` の runbook（`musicgen` extra 導入 +
重み取得）で実際にローカル生成できる状態を整えた上で、将来 PR で
`generate_takes` 相当のロジックを 1 プロンプト単位に薄く配線し直す。
"""
from __future__ import annotations

from pathlib import Path

from svp_rpe.recast.backend import (
    GeneratedTake,
    PreparedInvocation,
    RecastBackendUnavailable,
    RecastRunContext,
    base_prepared_invocation,
)
from svp_rpe.recast.models import RecastError

_UNAVAILABLE_MESSAGE = (
    "musicgen backend は本 PR (recast PR3) では実推論に配線されていません。"
    "docs/musicgen_backend.md の runbook（`musicgen` extra の導入 + 重みの取得）に "
    "従ってローカル生成環境を整えてから、將来の配線 PR を待つか、代わりに "
    "'deterministic' backend か 'manual' backend を使用してください。"
)


class MusicgenInvoker:
    """`invocation == "local"` かつ `generator == "musicgen"` の invoker（スタブ）。"""

    def prepare(self, ctx: RecastRunContext) -> PreparedInvocation:
        # takes_dir の解決のみ（実推論の配線がないため invoke で常に失敗する）。
        return base_prepared_invocation(ctx)

    def invoke(self, prepared: PreparedInvocation) -> GeneratedTake:
        raise RecastBackendUnavailable(_UNAVAILABLE_MESSAGE)

    def collect(self, prepared: PreparedInvocation, supplied_audio: Path) -> GeneratedTake:
        raise RecastError(
            "local backend (musicgen) は collect 不可。invoke() の配線を待つか "
            "'manual' backend を使用してください"
        )
