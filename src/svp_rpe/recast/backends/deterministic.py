"""DeterministicInvoker: CI-safe in-process 演奏者経由の local invocation。

`config/capability_profiles/deterministic.yaml`（generator=`deterministic`）に
束ねられる local backend。実生成器（Suno/MusicGen）を必要とせず、
`perform.performer.perform` が derived `CompositionScore` を直接演奏するため
（`docs/composition_poc_report.md` の決定論的シンセ演奏者）、CI 上で
`recast run` の local invocation 経路全体を検証できる — E2E テストでは
「外部生成の代役」として使う（manual backend が生成させたい音声の代わりに、
決定論的に同じ役割の音声を用意する）。

同一入力（derived_score 一致）に対して 2 回 invoke すると byte-identical な
`take-01.wav` を生成する（`PerformanceStyle(seed=12)` 固定・非決定要素なし）。
"""
from __future__ import annotations

import json
from pathlib import Path

from svp_rpe.perform.performer import PerformanceStyle, perform
from svp_rpe.perform.synth import sha256_bytes, wav_bytes
from svp_rpe.recast.backend import (
    GeneratedTake,
    PreparedInvocation,
    RecastRunContext,
    atomic_publish_bytes_bundle,
    base_prepared_invocation,
)
from svp_rpe.recast.models import RecastError

_DETERMINISTIC_STYLE = PerformanceStyle(name="recast_deterministic", seed=12)


class DeterministicInvoker:
    """`invocation == "local"` かつ `generator == "deterministic"` の invoker。"""

    def prepare(self, ctx: RecastRunContext) -> PreparedInvocation:
        # takes_dir の解決のみ（manual と異なり注文書は公開しない）。
        return base_prepared_invocation(ctx)

    def invoke(self, prepared: PreparedInvocation) -> GeneratedTake:
        samples = perform(prepared.derived_score, _DETERMINISTIC_STYLE)
        audio_bytes = wav_bytes(samples)
        sha256 = sha256_bytes(audio_bytes)

        take_filename = "take-01.wav"
        target = prepared.takes_dir / take_filename

        take_record = (
            json.dumps(
                {
                    "sha256": sha256,
                    "source": "local",
                    "backend_name": prepared.backend_name,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        # 音声 + take.json を 1 組として atomic publish する（Codex P2 review,
        # PR3 #208 指摘 1: 個別 publish だと take.json 書き込み失敗時に
        # provenance の無い音声だけが takes_dir に残り得る）。
        # `prepared.protected_input_paths`（project/score/manifest/arrangement/
        # capability_profile/mode_overrides/anchor artifact・source）とは
        # 衝突しないことを保証する（Codex P2 review, PR3 #208 指摘 6:
        # 従来 invoke() は一切渡していなかった）。
        atomic_publish_bytes_bundle(
            prepared.takes_dir,
            {take_filename: audio_bytes, "take.json": take_record.encode("utf-8")},
            protected_inputs=prepared.protected_input_paths,
        )

        return GeneratedTake(
            audio_path=target,
            sha256=sha256,
            source="local",
            backend_name=prepared.backend_name,
            note=None,
        )

    def collect(self, prepared: PreparedInvocation, supplied_audio: Path) -> GeneratedTake:
        raise RecastError(
            "local backend (deterministic) は collect 不可。invoke() で生成済みの "
            "テイクを直接使用してください"
        )
