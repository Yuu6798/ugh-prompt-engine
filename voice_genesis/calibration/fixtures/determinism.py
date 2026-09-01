"""generator determinism の fresh-process byte-identity 検査（設計正本 §3.3,
§6）: 同一 secret・fresh process で 2 回生成し PCM byte 一致を要求する。違反は
`vocab.BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC`。

各 subprocess は本モジュールを `python -m` 起動し、
`row_id / secret(hex) / campaign_id / family / split / probe_index` を渡して
1 行分の PCM を再生成 → stdout へ hex 出力する。呼び出し側 (`check_determinism`)
は 2 回の出力を比較する。row 自体は subprocess 間で JSON として引き渡す
（`FixtureRow.to_canonical_dict()` の JSON 表現をそのまま再利用する）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from voice_genesis.calibration.fixtures.generators import common, render_row
from voice_genesis.calibration.fixtures.matrix import FixtureRow
from voice_genesis.calibration.streams import derive_generator
from voice_genesis.calibration.vocab import BlockedCode


def _row_from_canonical_dict(d: dict[str, object]) -> FixtureRow:
    kwargs = dict(d)
    poles = kwargs.get("pole_freqs_hz")
    if poles is not None:
        kwargs["pole_freqs_hz"] = tuple(poles)  # type: ignore[arg-type]
    return FixtureRow(**kwargs)  # type: ignore[arg-type]


def render_row_pcm_hex(
    row_canonical_json: str,
    secret_hex: str,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int,
) -> str:
    """1 行を render して PCM16 bytes の hex 文字列を返す（subprocess 側・親側
    双方から呼べる純粋関数）。"""
    row_dict = json.loads(row_canonical_json)
    row = _row_from_canonical_dict(row_dict)
    secret = bytes.fromhex(secret_hex)
    rng = derive_generator(
        secret,
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
        purpose="generator",
    )
    pcm = render_row(row, rng)
    return common.pcm16_bytes(pcm).hex()


@dataclass(frozen=True)
class DeterminismCheckResult:
    row_id: str
    identical: bool
    blocked_code: str | None
    pcm_hex_a: str
    pcm_hex_b: str


def check_determinism_in_process(
    row: FixtureRow,
    secret: bytes,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int = 0,
) -> DeterminismCheckResult:
    """within-process の 2 回描画比較（fresh-process 版は
    `check_determinism_fresh_process` を使う。こちらは高速なユニットテスト用）。
    """
    row_json = _row_json(row)
    a = render_row_pcm_hex(
        row_json,
        secret.hex(),
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
    )
    b = render_row_pcm_hex(
        row_json,
        secret.hex(),
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
    )
    identical = a == b
    return DeterminismCheckResult(
        row_id=row_id,
        identical=identical,
        blocked_code=None if identical else BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC.value,
        pcm_hex_a=a,
        pcm_hex_b=b,
    )


def _row_json(row: FixtureRow) -> str:
    import json as _json

    return _json.dumps(row.to_canonical_dict())


_SUBPROCESS_ENTRYPOINT = """
import sys, json
from voice_genesis.calibration.fixtures.determinism import render_row_pcm_hex
payload = json.loads(sys.argv[1])
out = render_row_pcm_hex(
    payload["row_json"],
    payload["secret_hex"],
    campaign_id=payload["campaign_id"],
    family=payload["family"],
    split=payload["split"],
    row_id=payload["row_id"],
    probe_index=payload["probe_index"],
)
sys.stdout.write(out)
"""


def check_determinism_fresh_process(
    row: FixtureRow,
    secret: bytes,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int = 0,
    timeout_s: float = 60.0,
) -> DeterminismCheckResult:
    """設計正本 §3.3/§6 の generator determinism 検査そのもの: 同一 secret・
    fresh process で 2 回生成し PCM byte 一致を要求する。違反は
    `BLOCKED_C1_GENERATOR_NONDETERMINISTIC`。
    """
    payload = {
        "row_json": _row_json(row),
        "secret_hex": secret.hex(),
        "campaign_id": campaign_id,
        "family": family,
        "split": split,
        "row_id": row_id,
        "probe_index": probe_index,
    }
    payload_json = json.dumps(payload)

    outputs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_ENTRYPOINT, payload_json],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
        outputs.append(proc.stdout.strip())

    a, b = outputs
    identical = a == b
    return DeterminismCheckResult(
        row_id=row_id,
        identical=identical,
        blocked_code=None if identical else BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC.value,
        pcm_hex_a=a,
        pcm_hex_b=b,
    )
