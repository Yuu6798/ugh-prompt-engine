"""run8/s7_io.py — run 8 の入出力ガード（同型穴をここ 1 箇所へ集約する）。

塞いでいる穴は 3 系統で、いずれも **レビューで同型の再発が指摘された**（1–2 = PR #300、
3 = PR #303）ため、モジュールごとに書かず**単一実装 + 全呼び出し口からの利用**にする。

1. `reject_output_collision` — 出力先が入力（事前登録 JSON / `transcriptions.csv` /
   MIDI 対応表 / 測定 spec）と一致していたら**書く前に**停止する。
   `scripts/measure_bands.py::_reject_output_collision` と同じ resolved 比較。
2. `read_json_with_pin` / `read_bytes_with_pin` — 入力を**一度だけ**読み、
   その同じバイト列から parse と sha256 の両方を作る（読み直すと、
   parse と hash の間で差し替わったときに「古いバイトで計算した結果」を
   「新しいバイトの sha」で pin してしまう）。
3. `child_path` — 文書が持ってきた名前をディレクトリへ連結するとき、**その
   ディレクトリの中に閉じている**ことを課す（PR #303 第 5 巡 P1 と同型）。
   `"wav": "../../x.wav"` のような値を許すと、pin 照合の対象が意図した
   ディレクトリの外のファイルになる。
4. `read_wav_with_pins` — **記録済みの pin を持つ WAV** を測る前に、容器 sha と
   標本 sha の両方をその場で照合する（PR #303 レビュー指摘）。照合しないと、
   機械ローカルの出力ディレクトリが再生成・編集・別バッチ差し替えを受けたとき、
   「元の 360 セル由来」と名乗ったまま**別の音**を測った成果物が出る。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


class OutputCollisionError(RuntimeError):
    """出力先が保護対象の入力と衝突した / 出力先どうしが衝突した（fail-closed）。"""


class NonFiniteArtifactError(ValueError):
    """canonical JSON へ非有限値（Infinity / NaN）を書こうとした（fail-closed）。"""


class PathEscapeError(ValueError):
    """文書が持ってきた名前が、連結先ディレクトリの外を指した（fail-closed）。"""


class WavPinMismatch(ValueError):
    """測ろうとした WAV が記録済み pin と一致しない（fail-closed）。"""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reject_output_collision(
    out_paths: Iterable[Path], input_paths: Iterable[Path]
) -> None:
    """出力先が入力のいずれかと一致したら停止する（symlink 解決後の完全一致）。"""
    resolved_inputs = {Path(p).resolve() for p in input_paths if Path(p).exists()}
    for out in out_paths:
        if Path(out).resolve() in resolved_inputs:
            raise OutputCollisionError(
                f"出力先 ({out}) が入力と衝突しています（fail-closed で拒否）"
            )


def reject_duplicate_outputs(out_paths: Iterable[Path]) -> None:
    """出力先どうしが同一パスなら停止する（片方が他方を上書きするため）。"""
    seen: Dict[Path, Path] = {}
    for out in out_paths:
        resolved = Path(out).resolve()
        if resolved in seen:
            raise OutputCollisionError(
                f"2 つの出力先が同じパスを指しています: {seen[resolved]} と {out}"
            )
        seen[resolved] = Path(out)


def assert_json_finite(doc: Any, path: str = "$") -> None:
    """`Infinity` / `NaN` を canonical JSON へ書かせない（RFC 8259 非準拠のため）。

    `json.dumps` は既定で `Infinity` / `NaN` をそのまま出力するが、これは**厳格な
    JSON パーサでは読めない**。下流（別実装の検算・外部ツール）が読めない成果物を
    正本として置かないよう、書き込み前に全ノードを走査して停止する。
    非有限値は「値」ではなく **status 語彙**で表現する（例: 境界を一度も越えない
    = level は null で status = never_crossed）。
    """
    if isinstance(doc, float):
        if not math.isfinite(doc):
            raise NonFiniteArtifactError(
                f"{path}: 非有限値 {doc!r} は canonical JSON へ書けない"
                "（null + status 語彙で表現する）"
            )
        return
    if isinstance(doc, dict):
        for k, v in doc.items():
            assert_json_finite(v, f"{path}.{k}")
        return
    if isinstance(doc, (list, tuple)):
        for i, v in enumerate(doc):
            assert_json_finite(v, f"{path}[{i}]")


def child_path(directory: Path, name: str) -> Path:
    """`directory / name` を返す。ただし **directory の中に閉じている**ときだけ。

    `name` は事前登録 JSON / manifest / 群 JSON など**文書から来た値**であり、
    絶対パスや `../` を含みうる。素直に連結すると、pin 照合の対象が意図した
    ディレクトリの外のファイルになる（PR #303 第 5 巡 P1 が export 側で指摘した
    のと同型）。字句（絶対 / 親参照）と解決後の両方で検査する。
    """
    rel = Path(str(name))
    if rel.is_absolute() or ".." in rel.parts:
        raise PathEscapeError(
            f"{name!r} は {directory} からの相対パスでなければならない"
            "（絶対パス / 親参照は連結先の外を指しうる）"
        )
    root = Path(directory).resolve()
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        raise PathEscapeError(f"{name!r} が {root} の外を指している")
    return path


def read_bytes_with_pin(path: Path) -> Tuple[bytes, str, int]:
    """(生バイト, sha256, バイト数) を**一度の読み込み**から返す。"""
    raw = Path(path).read_bytes()
    return raw, sha256_bytes(raw), len(raw)


def read_json_with_pin(path: Path) -> Tuple[Dict[str, Any], str, int]:
    """JSON を**一度だけ読んだバイト列**から parse し、その sha256 とともに返す。"""
    raw, sha, n = read_bytes_with_pin(path)
    return json.loads(raw.decode("utf-8")), sha, n


def read_wav_with_pins(
    path: Path, wav_sha256: Optional[str], samples_sha256: Optional[str]
) -> Tuple[Any, int]:
    """記録済み pin を照合してから WAV を復号する（**一度読み**）。

    2 本の sha は別々の問いなので**両方**照合する:

    - `wav_sha256` = 容器のバイト列。float WAV の容器は libsndfile の PEAK
      チャンクに**書き出し時刻**を含むため、同じ標本を再レンダしても一致しない。
      つまりこれは「ディスク上のこのファイルが**記録した当のファイルか**」を見る。
    - `samples_sha256` = 標本列そのもの。容器が違っても標本が同じなら一致する。
      つまりこれは「**同じ音か**」を見る。

    復号は float32（= FLOAT subtype の格納型）で行い、float64 へ拡大して返す。
    32→64 の拡大は厳密なので、`dtype="float64"` で直接読んだ場合と**ビット一致**
    する（run8 の全 WAV 54 本で実測確認済み）。標本 sha は float32 の
    バイト列に対して定義されているため、この順序でしか照合できない。
    """
    import io as _io

    import numpy as np
    import soundfile as sf

    # 記録が**片方でも欠けていたら**測らない。以前は `samples_sha256` を optional に
    # していたので、キーを消すだけで最強の照合を回避できた（容器 sha は再レンダで
    # 必ず変わるため、実質「照合なし」になる）。
    if not wav_sha256 or not samples_sha256:
        raise WavPinMismatch(
            f"{path}: pin が欠けている"
            f"（wav_sha256={wav_sha256!r} / samples_sha256={samples_sha256!r}）"
        )
    raw, got_container, _ = read_bytes_with_pin(Path(path))
    if got_container != str(wav_sha256):
        raise WavPinMismatch(
            f"{path}: 容器 sha256 {got_container} != 記録 {wav_sha256}"
        )
    y32, sr = sf.read(_io.BytesIO(raw), dtype="float32", always_2d=False)
    got_samples = sha256_bytes(np.ascontiguousarray(y32).tobytes())
    if got_samples != str(samples_sha256):
        raise WavPinMismatch(
            f"{path}: 標本 sha256 {got_samples} != 記録 {samples_sha256}"
        )
    return np.asarray(y32, dtype=np.float64), int(sr)
