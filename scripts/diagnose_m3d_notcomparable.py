"""scripts/diagnose_m3d_notcomparable.py — M3d `not_comparable` 診断の決定論的導出
（Codex レビュー #255 第 9 巡指摘 1 件対応）。

`docs/measurements/m3d_2026-08/notcomparable_diagnosis.json` は元々
scratchpad 限定のアドホックスクリプト（``build_diagnosis.py``、未 commit）で
生成されており、クリーンチェックアウトからは再生成不能だった（生成スクリプト
非同梱・`build/` 配下の非コミット中間物を読んでいた）。本スクリプトはその
集計を **committed 入力のみ + pin 検証済み外部素材** から再現する。

入力（全て決定論的・fail-closed）:

1. ``docs/measurements/m3d_2026-08/run1.json`` — commit 済み run report
   （98 pair、tuning 66 は実測、holdout 32 は ``holdout_locked_until_frozen``
   ロックマーカー）。
2. ``tests/fixtures/melody_bench/m3d_pairs_manifest.yaml`` — commit 済み
   pairs manifest。kind/split の single source of truth（run1.json の
   holdout ロック行は kind を持たないため、kind/split は本 manifest から
   引く。material は pair_id の `_real_`/`_synth_` 接頭辞から機械判別する
   ——`build_m3d_pairs.py` の設計判定と同じ流儀。holdout real clip の
   識別にも本 manifest が要る（run1.json の holdout 行は audio 名を持たない
   ロックマーカーのため）。
3. ``tests/fixtures/melody_bench/registry.yaml`` — commit 済み M1 観測ゲート
   レジストリ（gate parameters の値の出所）。sha256 は run1.json の各 pair
   の ``comparison.provenance.m1_registry_sha256`` と一致することを検証する
   （registry.yaml が実際に run に使われたものと同一であることの pin 束縛）。
4. ``tests/fixtures/melody_bench/m2c_external_fixtures.yaml`` — commit 済み
   vocadito 40 clip の事前登録 content pin（audio/annotation 双方の
   sha256）。
5. vocadito F0 アノテーション CSV（``--vocadito-dir`` 配下
   ``Annotations/F0/<clip_id>_f0.csv``）— **外部素材、非 commit**
   （M2c 流儀: 外部データはリポジトリへ commit せず pin で束縛し、
   プロビジョニング前提で再現する。取得手順は
   `docs/m2e_provisioning_runbook.md` 相当）。読む前に必ず
   (4) の ``expected_annotation_sha256`` と非キャッシュ照合し、1 件でも
   不一致・欠落があれば診断生成を一切開始しない（fail-closed・部分出力
   なし）。

出力: 元の ``notcomparable_diagnosis.json`` と同じ集計構造（``1_reason_
histogram``〜``6_coverage_floor_candidate_provenance``）+ 新設 ``inputs``
節（全入力の sha256 と、使用したアノテーション CSV 全件の sha256）。壁時計は
出力に含めない（``--generated-utc`` を明示指定した場合のみ ``generated_utc``
キーを追加する。既定では省略——決定論維持）。

**再導出との差分検出時の運用**: 本スクリプトは既存 committed diagnosis の
書き換えを行わない（``--output`` は明示指定した場合のみ書く）。標準の
呼び出しでは stdout へ新版 JSON を出力するので、呼び出し側が既存 committed
ファイルと集計値レベルで比較してから ``--output`` で上書きする（測定値の
無断書き換えを防ぐ二段階運用）。

**Codex レビュー #255 第 10 巡対応（AA1・AA2）**:

- **AA1（P1・出力/入力衝突の preflight 拒否）**: ``--output`` を指定した場合、
  resolve() 済み絶対パスを全宣言データ入力（``--run1``/``--manifest``/
  ``--registry``/``--m2c-fixtures``、および manifest から機械的に列挙した
  消費対象アノテーション CSV 全件）の resolve() 済み絶対パス集合と照合し、
  1 件でも一致すれば **導出（``build_diagnosis``）を呼ぶ前に** fail-closed で
  拒否する（``_reject_output_input_collisions``。``build_m3d_pairs.py``/
  ``screen_m3d_clips.py`` の同名関数と同型）。従来は衝突すると
  ``atomic_write_bytes`` が入力ファイルを診断 JSON で上書き破壊しうる穴が
  あった。
- **AA2（P2・inputs 節のパスラベルを実引数由来に是正）**: ``inputs`` 節の
  ``run1_path``/``manifest_path``/``m2c_external_fixtures_path``/
  ``gate_registry_path`` は、従来ハードコードされた既定値の文字列リテラル
  だったが、実際に読んだパス（＝ CLI 引数で供給されたパス、既定のまま
  なら既定パス）を ``_display_path``（repo 配下ならリポジトリルート相対、
  外なら resolve 済み絶対パス）で正規化して記録するよう是正した
  （``vocadito_annotations_dir`` は元々この流儀で実装済みだった）。既定引数
  で再導出した場合、committed diagnosis JSON はこれらのパスがそもそも
  既定パスと一致する文字列だったため、本是正後も既定引数での出力は
  committed 版と変わらない。

**Codex レビュー #255 第 11 巡対応（BB2・BB3・BB4）**:

- **BB2（P2・§1 universe 文言のパスラベルを実引数由来に是正）**: ``inputs``
  節は AA2 で実引数由来（``_display_path``）に是正済みだったが、
  ``1_reason_histogram.universe`` の説明文だけは
  ``build/external_m3d/m3d_run1.json`` というクリーンチェックアウトに
  存在しないハードコード文字列が残っていた（既定入力は committed
  ``docs/measurements/m3d_2026-08/run1.json``）。``_build_section1`` に
  ``run1_display_path`` を渡し、実際に読んだパスをそのまま埋め込むよう
  是正した。
- **BB3（P2・fail-closed な manifest 束縛）**: 従来 ``run1.json`` に
  ``manifest_sha256`` が無い（レガシー/編集済みレポート）場合、束縛検証を
  無条件でスキップし、pair id が互換であれば任意の manifest を無警告に
  受理していた。是正後は digest 欠落を fail-closed とし、束縛未確認の
  manifest との組み合わせを拒否する。
- **BB4（P2・§3 フレーズギャップ閾値の registry 配線）**: ``§2`` は
  ``--registry`` の実測値（``observation_gate.phrase_gap_sec``）を記録・
  ``run1.json`` との pin 一致検証まで行うのに、``§3`` のアノテーション
  フレーズ構造計算はハードコードされた ``0.6`` を使っており、
  ``--registry`` に別値を渡すと ``§2`` と ``§3`` が矛盾しうる穴があった。
  検証済みの registry 値を ``_build_section3`` へ配線し、``§2``/``§3`` を
  構造的に一致させた（``phrase_gap_sec`` が現行既定 ``0.6`` である限り、
  既定引数での出力は committed 版と変わらない）。

**Codex レビュー #255 第 12 巡対応（CC1・CC2）**:

- **CC1（P1・manifest 読みの単一化）**: AA1 は ``--output`` preflight のために
  ``main()`` で manifest を 1 回読み、``build_diagnosis`` 内部でも独立に
  もう 1 回読んでいた（2 回読み）。この間隙で manifest が別プロセスに
  差し替えられると、preflight は旧 manifest の消費アノテーション CSV 集合を
  衝突チェックし、``build_diagnosis`` は新 manifest の集合を消費するため、
  ``--output`` が新 manifest 由来のアノテーション CSV と衝突していても
  preflight が見逃し、そのファイルを読んだ直後に診断 JSON で上書きしうる
  TOCTOU があった。是正後は ``main()`` が manifest バイトを 1 回だけ読み
  （hash + パースを同一バッファから）、そのパース済み文書を
  ``_consumed_annotation_paths``（preflight 用列挙）と ``build_diagnosis``
  （本導出）の両方へ引数として渡す。``build_diagnosis`` は manifest を
  自分では読まなくなり（``manifest_doc``/``manifest_sha256`` を必須引数として
  受け取る）、``run1.json`` との ``manifest_sha256`` 束縛検証もこの同一
  バッファ由来の hash を使う。preflight が守る集合と導出が消費する集合が
  構造的に同一バッファへ帰着する。
- **CC2（P2・pair 集合の完全一致検証）**: 従来は ``manifest_sha256`` さえ
  一致すれば、``run1.json`` の pair 集合が manifest の pair_id 集合と完全に
  一致するかは検証していなかった。編集済み run report が
  ``manifest_sha256`` を正しく保持したまま余剰 pair を追加すると、
  ``§1``（``run1_pairs`` 直読み）はその余剰 pair を集計に含める一方、
  manifest 駆動の crosstab・``§3``/``§5``/``§6`` はそれを無視し、内部矛盾を
  抱えた診断が「束縛済み provenance」として出力されうる穴があった。是正後は
  集計（``_build_section*``）を呼ぶ前に ``set(run1_pairs.keys()) ==
  {manifest pair_id}`` を検証し、欠落・余剰のどちらでも fail-closed で
  拒否する。committed ``run1.json``（98 pair）と
  ``tests/fixtures/melody_bench/m3d_pairs_manifest.yaml``（98 pair）は
  完全一致しているため、既定引数での出力は committed 版と変わらない。

**Codex レビュー #255 第 13 巡対応（DD1・DD2）**:

- **DD1（P2・重複キー拒否ローダへの切替）**: 従来 ``_load_json``/``_load_yaml``
  は素の ``json.loads``/``yaml.safe_load`` を使っており、上書き/手編集された
  run report（や manifest/registry/m2c fixtures）が同一キーを重複エントリで
  持つ場合、last-wins で黙って後勝ちの値だけが見える穴があった——特に同一
  pair_id が重複すると、CC2 の pair 集合完全一致検証は最後に勝った 1 件しか
  見えず、曖昧な provenance から正規の診断を生成しうる。
  ``scripts/run_melody_comparison.py`` の ``_json_loads_no_dup_keys``／
  ``scripts/build_m3d_pairs.py`` の ``_yaml_load_no_dup_keys`` と同型の
  重複キー拒否ローダへ切替した（builder が既存ハーネスを import しない前例に
  ならい、本スクリプトも両ローダを独立複製する）。ファミリー掃討: 本スクリプト
  内で素の ``json.loads``/``yaml.safe_load`` を呼ぶ箇所は ``_load_json``/
  ``_load_yaml`` の 2 箇所のみ（他の入力読み——vocadito F0 CSV は ``csv.reader``
  経由でパース対象が JSON/YAML でないため対象外）であることを確認済み
  （両関数を経由しない直呼び出しは他に無い）。第 12 巡終端宣言（CC1/CC2）の
  ファミリー掃討列挙からは漏れていた読み取り経路だが、これは同一ファミリー
  （このスクリプトが読む committed/半信頼 provenance ファイルの重複キー
  last-wins 穴）の標準適用であり、終端宣言を盾に不採用とはせず採用する。
- **DD2（P2・生成器スクリプト自体の pin）**: ``diagnosis["inputs"]`` に
  ``generator_script_path``（repo-relative）と ``generator_script_sha256``
  （本スクリプト自身のソースバイトの sha256）を追加した。自己参照になるため、
  hash は ``GENERATOR_SCRIPT_PATH = Path(__file__).resolve()`` を実行時に
  読み直して計算する（committed record に書かれる値の算出時点 = その記録を
  生成した実行のソースバイト）。**この結合の帰結**: 本スクリプトを編集した
  ら、committed ``notcomparable_diagnosis.json`` の
  ``generator_script_sha256`` は再導出しない限り stale になり、
  ``tests/test_m3d_notcomparable_diagnosis_record.py`` の fixture テスト
  （committed record の ``generator_script_sha256`` == committed スクリプト
  ファイルの実バイト sha256）がそれを検出して落ちる。つまりスクリプト編集は
  vocadito 環境での record 再導出（machine-dependent 操作）を必須化する
  ——これは stale 防止の fail-closed として意図どおりであり、melody トラック
  は closeout 済み（``docs/m3d_calibration_record.md``）でスクリプトも安定期
  のため受容する設計判定。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.utils.atomic_io import atomic_write_bytes  # noqa: E402

DEFAULT_RUN1_PATH = ROOT / "docs" / "measurements" / "m3d_2026-08" / "run1.json"
DEFAULT_MANIFEST_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_pairs_manifest.yaml"
DEFAULT_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"
DEFAULT_M2C_FIXTURES_PATH = (
    ROOT / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"
)
DEFAULT_VOCADITO_DIR = ROOT / "build" / "external_m3d" / "vocadito"
DEFAULT_OUTPUT_PATH = ROOT / "docs" / "measurements" / "m3d_2026-08" / "notcomparable_diagnosis.json"

# DD2（Codex レビュー #255 第 13 巡・P2）: 診断 JSON の生成器自体を pin する。
# 自己参照になるため、hash は実行時にこのスクリプト自身のソースバイトを
# 読んで計算する（``record`` に書く値の算出時点 = 実行時のソースバイト——
# import 済みの ``.pyc`` やモジュールオブジェクトからは計算しない）。
GENERATOR_SCRIPT_PATH = Path(__file__).resolve()

_LOCK_STATUS = "holdout_locked_until_frozen"


def _display_path(path: Path) -> str:
    """記録用にリポジトリルート相対の POSIX パスへ正規化する（実行マシンの
    絶対パスをそのまま committed record へ焼き込まない——決定論維持）。
    ROOT 配下でなければ絶対パス文字列にフォールバックする。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


# --------------------------------------------------------------------------- #
# 読み取り + sha256（TOCTOU 解消: 1 回読んで hash とパースの両方に使う）
# --------------------------------------------------------------------------- #
def _read_bytes_and_sha256(path: Path) -> Tuple[bytes, str]:
    data = Path(path).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _generator_script_sha256() -> str:
    """DD2（Codex レビュー #255 第 13 巡・P2）: 本スクリプト自身のソースバイトの
    sha256。``GENERATOR_SCRIPT_PATH``（``Path(__file__).resolve()``）を都度
    読み直す——自己参照のため、記録される値は実行時点のソースバイトと一致する
    （逆に言えば、このスクリプトを編集すれば committed diagnosis の
    ``generator_script_sha256`` は再導出しない限り古いまま stale になる。
    これは意図された fail-closed の帰結——検証テスト側で「record の
    generator_script_sha256 == committed スクリプトの実バイト sha256」を
    突き合わせるため、スクリプト編集後に再導出を怠ると committed diagnosis
    のテストが落ちる）。"""
    return hashlib.sha256(GENERATOR_SCRIPT_PATH.read_bytes()).hexdigest()


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader（``scripts/build_m3d_pairs.py`` の
    ``_NoDupSafeLoader`` と同型）。本スクリプトは既存ハーネス（``scripts/
    run_melody_comparison.py``）/builder（``scripts/build_m3d_pairs.py``）を
    import しない設計（DD1・Codex レビュー #255 第 13 巡）のため、両者が
    それぞれ独立複製している流儀にならい、本スクリプト内でも独立複製する。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML mapping key {key!r}; last-wins で入力の一部を "
                "隠す穴を弾く (fail-closed)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def _json_loads_no_dup_keys(data: bytes, *, what: str) -> Any:
    """``scripts/run_melody_comparison.py`` の ``_json_loads_no_dup_keys`` と
    同型（``object_pairs_hook`` で全ネスト層の object に再帰適用——last-wins
    で重複 pair_id エントリを隠す穴を弾く）。DD1・Codex レビュー #255 第 13
    巡: 独立複製（上記 ``_NoDupSafeLoader`` と同じ理由）。"""

    def _reject_dupes(pairs: "List[Tuple[str, Any]]") -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"{what}: duplicate JSON object key {key!r}; last-wins で "
                    "入力の一部を隠す穴を弾く (fail-closed)"
                )
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=_reject_dupes)


def _load_json(path: Path) -> Tuple[Dict[str, Any], bytes, str]:
    """DD1（Codex レビュー #255 第 13 巡・P2）: 素の ``json.loads`` は last-wins
    で重複キーを黙殺する——上書き/手編集された run report が同一 pair_id を
    重複エントリで持つと、後段の pair 集合完全一致検証（CC2）は最後に勝った
    1 件しか見えず、曖昧な provenance から正規の診断を生成しうる。重複キー
    拒否ローダへ切替。"""
    data, digest = _read_bytes_and_sha256(path)
    return _json_loads_no_dup_keys(data, what=str(path)), data, digest


def _load_yaml(path: Path) -> Tuple[Any, bytes, str]:
    """DD1（Codex レビュー #255 第 13 巡・P2）: 素の ``yaml.safe_load`` も同じ
    last-wins 穴を持つ（registry.yaml/manifest.yaml/m2c_external_fixtures.yaml
    のいずれも重複キー編集で握りつぶされうる）。重複キー拒否ローダへ切替。"""
    data, digest = _read_bytes_and_sha256(path)
    return yaml.load(data, Loader=_NoDupSafeLoader), data, digest  # noqa: S506 (dup-key 拒否付き SafeLoader)


# --------------------------------------------------------------------------- #
# reason 文字列パース（`observability.py:gate_reasons` / `run_melody_comparison.py`
# が生成する reason 文字列の唯一のフォーマット。ここではフォーマットを消費する
# だけで、判定ロジックそのものは持たない — single source of truth は
# `gate_reasons()` / `evaluate_comparison()` 側。）
# --------------------------------------------------------------------------- #
_RE_GATE = re.compile(r"^observation_gate_insufficient_(a|b): (.*)$")
_RE_OVERLAP = re.compile(r"^insufficient_overlap\((.*)\)$")


def _parse_reason(reason: str) -> Tuple[str, Optional[str], Optional[str]]:
    if reason == "evidence_thresholds_uncalibrated":
        return ("evidence_thresholds_uncalibrated", None, None)
    m = _RE_GATE.match(reason)
    if m:
        return ("observation_gate_insufficient", m.group(1), m.group(2))
    m = _RE_OVERLAP.match(reason)
    if m:
        return ("insufficient_overlap", None, m.group(1))
    raise ValueError(f"unrecognized comparison.reasons entry (parser out of date?): {reason!r}")


def _material_of(pair_id: str) -> str:
    """pair_id の `_real_`/`_synth_` 接頭辞から material を機械判別する
    （`build_m3d_pairs.py` の設計判定と同じ流儀。manifest 自体は material
    フィールドを持たない）。"""
    token = pair_id.split("_")[1]
    if token not in ("real", "synth"):
        raise ValueError(f"pair_id does not encode material via _real_/_synth_: {pair_id!r}")
    return token


def _basename_clip_id(audio_path: str) -> Optional[str]:
    """audio_a/audio_b のファイル名から「生の vocadito clip」なら clip_id を
    返す。変形済みファイル（`vocadito_X__pitch_p3.wav` 等、`__` を含む）や
    synth 素材は None（生 clip の annotation は存在しないため対象外）。"""
    name = Path(audio_path).stem
    if "__" not in name and name.startswith("vocadito_"):
        return name
    return None


# --------------------------------------------------------------------------- #
# vocadito F0 アノテーション（外部・非 commit）: pin 照合 + フレーズ構造計算
# --------------------------------------------------------------------------- #
def _annotation_path(vocadito_dir: Path, clip_id: str) -> Path:
    return Path(vocadito_dir) / "Annotations" / "F0" / f"{clip_id}_f0.csv"


def _verify_and_load_annotation(
    vocadito_dir: Path, clip_id: str, fixtures: Dict[str, Dict[str, str]]
) -> Tuple[List[float], List[float], str]:
    """clip_id の F0 CSV を読み、``m2c_external_fixtures.yaml`` の
    ``expected_annotation_sha256`` と一致することを検証してから返す
    （fail-closed・非キャッシュ）。"""
    if clip_id not in fixtures:
        raise SystemExit(
            f"fail-closed: {clip_id} has no expected_annotation_sha256 pin in "
            "m2c_external_fixtures.yaml"
        )
    path = _annotation_path(vocadito_dir, clip_id)
    if not path.exists():
        raise SystemExit(f"fail-closed: missing external annotation CSV: {path}")
    data, digest = _read_bytes_and_sha256(path)
    expected = fixtures[clip_id]["expected_annotation_sha256"]
    if digest != expected:
        raise SystemExit(
            f"fail-closed: annotation sha256 mismatch for {clip_id}: "
            f"expected {expected}, got {digest} ({path})"
        )
    times: List[float] = []
    freqs: List[float] = []
    for row in csv.reader(data.decode("utf-8").splitlines()):
        if not row:
            continue
        times.append(float(row[0]))
        freqs.append(float(row[1]))
    return times, freqs, digest


def _annotation_phrase_structure(
    times: Sequence[float], freqs: Sequence[float], *, gap_threshold_sec: float
) -> Dict[str, Any]:
    """アノテーション F0 タイムラインから直接、voiced-run ベースのフレーズ構造を
    計算する（M1 の note 導出パイプラインとは独立。`3_annotation_phrase_
    structure_table.method` の定義そのままの実装）。

    - voiced フレーム: ``f0 > 0``
    - voiced_run: 連続 voiced フレームの塊（raw、閾値マージ前）
    - unvoiced ギャップは「内部ギャップ（連続する voiced run 間）」に加えて
      「clip 先頭 〜 最初の voiced フレーム」「最後の voiced フレーム 〜 clip
      末尾」の 2 つの境界ギャップも含める（無音のリード/トレイルも
      `longest_unvoiced_gap_sec` の対象——実測値の再現に必要な仕様）。
    - phrase 分割: 内部ギャップが ``gap_threshold_sec`` を超えたら新フレーズ
      （境界ギャップは phrase 分割には数えない——`observability.py:_phrase_
      count` が note 系列内の遷移のみを見るのと同じ扱い）。
    """
    n = len(times)
    voiced = [f > 0.0 for f in freqs]
    total_duration_sec = times[-1]
    voiced_ratio = sum(1 for v in voiced if v) / n if n else 0.0

    runs: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j + 1 < n and voiced[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1

    internal_gaps = [
        times[s1] - times[e0] for (_s0, e0), (s1, _e1) in zip(runs, runs[1:])
    ]
    boundary_gaps: List[float] = []
    if runs:
        first_start_idx, _ = runs[0]
        _last_start_idx, last_end_idx = runs[-1]
        boundary_gaps.append(times[first_start_idx])  # lead silence (t=0 起点)
        boundary_gaps.append(times[-1] - times[last_end_idx])  # trail silence
    all_gaps = internal_gaps + boundary_gaps
    longest_unvoiced_gap_sec = max(all_gaps) if all_gaps else 0.0

    phrase_count = 0
    if runs:
        phrase_count = 1
        for gap in internal_gaps:
            if gap > gap_threshold_sec:
                phrase_count += 1

    return {
        "total_duration_sec": round(total_duration_sec, 4),
        "voiced_ratio": round(voiced_ratio, 4),
        "annotation_phrase_count": phrase_count,
        "longest_unvoiced_gap_sec": round(longest_unvoiced_gap_sec, 4),
        "voiced_run_count_raw": len(runs),
    }


# --------------------------------------------------------------------------- #
# section 1: reason histogram
# --------------------------------------------------------------------------- #
def _build_section1(
    run1_pairs: Dict[str, Any],
    manifest_pairs: List[Dict[str, Any]],
    *,
    run1_display_path: str,
) -> Dict[str, Any]:
    manifest_by_id = {p["pair_id"]: p for p in manifest_pairs}
    pairs_total = len(run1_pairs)
    lock_ids = {pid for pid, p in run1_pairs.items() if p.get("status") == _LOCK_STATUS}
    pairs_holdout = sum(1 for p in manifest_by_id.values() if p["split"] == "holdout")
    pairs_tuning = sum(1 for p in manifest_by_id.values() if p["split"] == "tuning")

    all_reasons_typed: List[Tuple[str, Optional[str], Optional[str]]] = []
    zero_reason_pairs = 0
    for pid, entry in run1_pairs.items():
        if pid in lock_ids:
            continue
        reasons = entry["comparison"]["reasons"]
        if not reasons:
            zero_reason_pairs += 1
        for r in reasons:
            all_reasons_typed.append(_parse_reason(r))

    def _agg(keyfunc):
        counter: Dict[Any, int] = {}
        for item in all_reasons_typed:
            key = keyfunc(item)
            counter[key] = counter.get(key, 0) + 1
        return counter

    side_counter = _agg(lambda t: (t[0], t[1]))
    side_rows = [
        {"reason_type": k[0], "side": k[1], "count": v}
        for k, v in sorted(side_counter.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or ""))
    ]

    detail_counter = _agg(lambda t: (t[0], t[1], t[2]))
    detail_rows = [
        {"reason_type": k[0], "side": k[1], "detail": k[2], "count": v}
        for k, v in sorted(
            detail_counter.items(),
            key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or "", kv[0][2] or ""),
        )
    ]

    # crosstab kind/split/material
    groups: Dict[Tuple[str, str, str], List[str]] = {}
    for pid, mp in manifest_by_id.items():
        key = (mp["kind"], mp["split"], _material_of(pid))
        groups.setdefault(key, []).append(pid)

    crosstab = []
    for key in sorted(groups):
        kind, split, material = key
        pair_ids = groups[key]
        evidence_counts: Dict[str, int] = {}
        report_status_counts: Dict[str, int] = {}
        reason_type_side_counts: Dict[str, int] = {}
        for pid in pair_ids:
            entry = run1_pairs[pid]
            if entry.get("status") == _LOCK_STATUS:
                evidence_counts["null"] = evidence_counts.get("null", 0) + 1
                report_status_counts[_LOCK_STATUS] = report_status_counts.get(_LOCK_STATUS, 0) + 1
                continue
            evidence = entry["comparison"]["evidence"]
            ekey = evidence if evidence is not None else "none"
            evidence_counts[ekey] = evidence_counts.get(ekey, 0) + 1
            report_status_counts["null"] = report_status_counts.get("null", 0) + 1
            for r in entry["comparison"]["reasons"]:
                rtype, side, _detail = _parse_reason(r)
                rkey = f"{rtype}|{side}"
                reason_type_side_counts[rkey] = reason_type_side_counts.get(rkey, 0) + 1
        crosstab.append(
            {
                "kind": kind,
                "split": split,
                "material": material,
                "pair_count": len(pair_ids),
                "evidence_counts": evidence_counts,
                "report_status_counts": report_status_counts,
                "reason_type_side_counts": reason_type_side_counts,
            }
        )

    return {
        "universe": (
            f"all {pairs_total} pairs in {run1_display_path}['pairs'] "
            "(kind/split/material taken from tests/fixtures/melody_bench/"
            "m3d_pairs_manifest.yaml, which is unredacted for holdout too; "
            "comparison.reasons taken from the run report, which is only populated "
            f"for the {pairs_tuning} tuning-split pairs)"
        ),
        "pairs_total": pairs_total,
        "pairs_tuning": pairs_tuning,
        "pairs_holdout": pairs_holdout,
        "pairs_holdout_locked_no_comparison_field": len(lock_ids),
        "pairs_with_comparison_but_zero_reasons": zero_reason_pairs,
        "reason_type_side_histogram": side_rows,
        "reason_type_side_detail_histogram": detail_rows,
        "crosstab_kind_split_material": crosstab,
    }


# --------------------------------------------------------------------------- #
# section 2: gate parameters（値は registry.yaml 実測。行番号は静的な出所注記
# ——現行 registry.yaml / observability.py の該当行を指す）
# --------------------------------------------------------------------------- #
def _build_section2(registry_mapping: Dict[str, Any]) -> Dict[str, Any]:
    gate = registry_mapping["observation_gate"]
    param_specs = [
        ("min_voiced_coverage", "registry.yaml:24", "observability.py:115 (dataclass field, no default)"),
        ("min_note_count", "registry.yaml:25", "observability.py:116 (dataclass field, no default)"),
        ("min_phrase_count", "registry.yaml:26", "observability.py:117 (dataclass field, no default)"),
        ("min_confidence_mean", "registry.yaml:27", "observability.py:118 (dataclass field, no default)"),
        ("max_low_confidence_rate", "registry.yaml:28", "observability.py:119 (dataclass field, no default)"),
        ("max_octave_jump_rate", "registry.yaml:29", "observability.py:120 (dataclass field, no default)"),
        ("voiced_confidence_floor", "registry.yaml:30", "observability.py:121 (dataclass field, no default)"),
        ("low_confidence_floor", "registry.yaml:31", "observability.py:122 (dataclass field, no default)"),
        ("min_cross_extractor_agreement", "registry.yaml:35", "observability.py:123 (Optional[float] = None default)"),
        ("note_min_run_frames", "registry.yaml:37", "observability.py:125 (default=3)"),
        ("median_filter_frames", "registry.yaml:38", "observability.py:126 (default=5)"),
        ("phrase_gap_sec", "registry.yaml:39", "observability.py:127 (default=0.6)"),
        ("octave_jump_semitones", "registry.yaml:40", "observability.py:128 (default=11.0)"),
    ]
    notes = {
        "min_cross_extractor_agreement": "null = not used as a gate condition in M1",
        "phrase_gap_sec": (
            "gap (sec) between one note's end and the next note's start above "
            "which a new phrase starts; see observability.py:316-325 "
            "_phrase_count()"
        ),
    }
    parameters = []
    for name, reg_loc, code_loc in param_specs:
        entry: Dict[str, Any] = {"name": name, "value": gate[name]}
        if name in notes:
            entry["note"] = notes[name]
        entry["defined_at"] = [
            f"tests/fixtures/melody_bench/{reg_loc}",
            f"src/svp_rpe/melody/{code_loc}",
        ]
        parameters.append(entry)

    return {
        "source_of_truth_note": (
            "tests/fixtures/melody_bench/registry.yaml observation_gate block is "
            "loaded via ObservabilityThresholds.from_registry(); "
            "src/svp_rpe/melody/observability.py:107-158 defines the dataclass + "
            "the same defaults."
        ),
        "parameters": parameters,
        "gate_reasons_single_source_of_truth": (
            "src/svp_rpe/melody/observability.py:376-431 gate_reasons()"
        ),
        "phrase_count_definition": (
            "src/svp_rpe/melody/observability.py:316-325 _phrase_count(): notes "
            "sorted by start_sec; a new phrase starts whenever current.start_sec - "
            "prev.end_sec > phrase_gap_sec (strict >, seconds)."
        ),
    }


# --------------------------------------------------------------------------- #
# section 3: annotation phrase structure table + gate crosscheck
# --------------------------------------------------------------------------- #
def _build_section3(
    manifest_pairs: List[Dict[str, Any]],
    run1_pairs: Dict[str, Any],
    vocadito_dir: Path,
    fixtures: Dict[str, Dict[str, str]],
    *,
    gap_threshold_sec: float,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    # 1) real clip -> split（manifest から。holdout clip 名は run1.json の
    #    ロック行に残っていないため manifest が唯一の出所）。
    clip_split: Dict[str, str] = {}
    for mp in manifest_pairs:
        if _material_of(mp["pair_id"]) != "real":
            continue
        for audio_key in ("audio_a", "audio_b"):
            clip_id = _basename_clip_id(mp[audio_key])
            if clip_id is None:
                continue
            existing = clip_split.get(clip_id)
            if existing is not None and existing != mp["split"]:
                raise ValueError(
                    f"clip {clip_id} appears in both splits ({existing} vs {mp['split']})"
                )
            clip_split[clip_id] = mp["split"]

    tuning_clips = sorted((c for c, s in clip_split.items() if s == "tuning"), key=lambda c: int(c.split("_")[1]))
    holdout_clips = sorted((c for c, s in clip_split.items() if s == "holdout"), key=lambda c: int(c.split("_")[1]))
    all_clips = sorted(clip_split, key=lambda c: int(c.split("_")[1]))

    # 2) clip -> [manifest pairs where clip appears as a raw side], manifest 順序維持
    clip_pairs: Dict[str, List[Tuple[str, str]]] = {c: [] for c in all_clips}
    for mp in manifest_pairs:
        if _material_of(mp["pair_id"]) != "real":
            continue
        for side, audio_key in (("a", "audio_a"), ("b", "audio_b")):
            clip_id = _basename_clip_id(mp[audio_key])
            if clip_id in clip_pairs:
                clip_pairs[clip_id].append((mp["pair_id"], side))

    annotation_sha256: Dict[str, str] = {}
    rows = []
    for clip_id in all_clips:
        times, freqs, digest = _verify_and_load_annotation(vocadito_dir, clip_id, fixtures)
        annotation_sha256[clip_id] = digest
        structure = _annotation_phrase_structure(times, freqs, gap_threshold_sec=gap_threshold_sec)
        split = clip_split[clip_id]
        row: Dict[str, Any] = {"clip_id": clip_id, **structure, "split": split}
        if split == "holdout":
            row["gate_crosscheck"] = "not applicable: holdout comparison is locked "
        else:
            crosscheck = []
            measured_present = False
            for pair_id, side in clip_pairs[clip_id]:
                entry = run1_pairs[pair_id]
                comparison = entry["comparison"]
                evidence = comparison["evidence"]
                if evidence != "not_comparable":
                    measured_present = True
                reasons = comparison["reasons"]
                gate_insufficient_this_side = any(
                    r.startswith(f"observation_gate_insufficient_{side}:") for r in reasons
                )
                mp = next(m for m in manifest_pairs if m["pair_id"] == pair_id)
                crosscheck.append(
                    {
                        "pair_id": pair_id,
                        "side": side,
                        "kind": mp["kind"],
                        "evidence": evidence,
                        "gate_insufficient_this_side": gate_insufficient_this_side,
                        "reasons": list(reasons),
                    }
                )
            row["gate_crosscheck"] = crosscheck
            row["measured_evidence_pair_present"] = measured_present
        rows.append(row)

    table = {
        "gap_threshold_sec_used": gap_threshold_sec,
        "method": (
            "annotation_phrase_count = number of voiced-frame runs (f0>0) after "
            "merging consecutive voiced runs separated by an unvoiced gap <= "
            "gap_threshold_sec_used; computed directly on the annotation timeline "
            "(F0 CSV), independent of the M1 note derivation pipeline "
            "(notes_from_frames)."
        ),
        "clip_count_real_total": len(all_clips),
        "clip_count_tuning": len(tuning_clips),
        "clip_count_holdout": len(holdout_clips),
        "rows": rows,
    }
    return table, annotation_sha256


# --------------------------------------------------------------------------- #
# section 4: report pair-level keys observed（静的な事実確認 + report 実物から
# の union 計算）
# --------------------------------------------------------------------------- #
def _build_section4(run1_pairs: Dict[str, Any]) -> Dict[str, Any]:
    keys: set = set()
    for entry in run1_pairs.values():
        keys.update(entry.keys())
    return {
        "report_pair_level_keys_observed": sorted(keys),
        "conclusion": (
            "report carries only 'observation_sha256_a'/'observation_sha256_b' "
            "(content hashes) per pair, not the observed frame/note series "
            "themselves; crepe-side observed phrase_count is not recoverable from "
            "m3d_run1.json without re-running the crepe extraction, which this "
            "task does not do."
        ),
    }


# --------------------------------------------------------------------------- #
# section 5: measured pairs（manifest 順序で split=='tuning' and evidence !=
# 'not_comparable' を抽出）
# --------------------------------------------------------------------------- #
def _build_section5(manifest_pairs: List[Dict[str, Any]], run1_pairs: Dict[str, Any]) -> Dict[str, Any]:
    measured = []
    for mp in manifest_pairs:
        if mp["split"] != "tuning":
            continue
        entry = run1_pairs[mp["pair_id"]]
        comparison = entry["comparison"]
        if comparison["evidence"] == "not_comparable":
            continue
        measured.append(
            {
                "pair_id": mp["pair_id"],
                "kind": mp["kind"],
                "material": _material_of(mp["pair_id"]),
                "evidence": comparison["evidence"],
                "reasons": list(comparison["reasons"]),
                "coverage": dict(comparison.get("coverage") or {}),
                "audio_a": mp["audio_a"],
                "audio_b": mp["audio_b"],
            }
        )

    family_ids = sorted(
        p["pair_id"] for p in measured if "vocadito_17" in p["pair_id"] or "vocadito_22" in p["pair_id"]
    )

    return {
        "definition": (
            "split=='tuning' and comparison.evidence != 'not_comparable' "
            "(observed values: evidence='none' with "
            "reasons=['evidence_thresholds_uncalibrated'], meaning the "
            "observation gate + comparator both ran to completion but no verdict "
            "threshold is calibrated yet)"
        ),
        "count_all_material": len(measured),
        "count_vocadito_17_22_family": len(family_ids),
        "pairs": measured,
        "vocadito_17_22_family_pair_ids": family_ids,
    }


# --------------------------------------------------------------------------- #
# section 6: coverage floor candidate provenance（`_coverage_floor_candidate`
# と同一の選定規則を run1.json 直読みで再現。manifest 順序で走査。）
# --------------------------------------------------------------------------- #
def _build_section6(manifest_pairs: List[Dict[str, Any]], run1_pairs: Dict[str, Any]) -> Dict[str, Any]:
    samples = []
    for mp in manifest_pairs:
        if mp["split"] != "tuning" or mp["expected"] != "same":
            continue
        entry = run1_pairs[mp["pair_id"]]
        comparison = entry["comparison"]
        coverage = comparison.get("coverage") or {}
        for side, field in (("a", "aligned_note_fraction_a"), ("b", "aligned_note_fraction_b")):
            value = coverage.get(field)
            if value is not None:
                samples.append(
                    {
                        "pair_id": mp["pair_id"],
                        "side": side,
                        "field": field,
                        "value": value,
                        "evidence": comparison["evidence"],
                    }
                )

    values = [s["value"] for s in samples]
    computed = (
        {
            "candidate": min(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
            "sample_count": len(values),
        }
        if values
        else {"candidate": None, "reason": "no_tuning_positive_coverage_samples"}
    )

    return {
        "source_function": (
            "scripts/run_melody_comparison.py:_coverage_floor_candidate (around "
            "line 2495), called from evaluate_comparison around lines 3132/3155"
        ),
        "selection_rule": (
            "iterates ALL tuning-split pairs with expected=='same' "
            "(positive_transform), regardless of comparison.evidence, and appends "
            "comparison.coverage['aligned_note_fraction_a'] and "
            "['aligned_note_fraction_b'] whenever the field is not None -- this "
            "is NOT gated on evidence != 'not_comparable', so a pair whose gate "
            "marks it not_comparable can still contribute a coverage value "
            "(observed here as 0.0 from pt_real_tuning_vocadito_17_pitch_p3)."
        ),
        "computed_from_m3d_verdict_tuning_json": computed,
        "sample_count": len(samples),
        "samples": samples,
    }


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build_diagnosis(
    *,
    run1_path: Path,
    manifest_path: Path,
    manifest_doc: Any,
    manifest_sha256: str,
    registry_path: Path,
    m2c_fixtures_path: Path,
    vocadito_dir: Path,
    generated_utc: Optional[str],
) -> Dict[str, Any]:
    """``manifest_doc``/``manifest_sha256`` は呼び出し側（``main()``）が
    manifest バイトを 1 回だけ読んで hash + パースした結果をそのまま渡す
    （Codex レビュー #255 第 12 巡 CC1）。ここでは manifest を再読みしない
    ——``manifest_path`` は表示用パスラベルの算出にのみ使う。"""
    run1_doc, _run1_bytes, run1_sha256 = _load_json(run1_path)
    registry_doc, _registry_bytes, registry_sha256 = _load_yaml(registry_path)
    fixtures_doc, _fixtures_bytes, fixtures_sha256 = _load_yaml(m2c_fixtures_path)

    run1_pairs: Dict[str, Any] = run1_doc["pairs"]
    manifest_pairs: List[Dict[str, Any]] = manifest_doc["pairs"]
    fixtures: Dict[str, Dict[str, str]] = fixtures_doc["fixtures"]

    # registry.yaml が実際に run1.json 生成に使われたものと同一であることを、
    # per-pair provenance との一致で検証する（committed run1.json の主張を
    # 鵜呑みにしない）。
    tuning_pair_ids = [pid for pid, p in run1_pairs.items() if p.get("status") != _LOCK_STATUS]
    mismatched_registry = [
        pid
        for pid in tuning_pair_ids
        if run1_pairs[pid]["comparison"]["provenance"]["m1_registry_sha256"] != registry_sha256
    ]
    if mismatched_registry:
        raise SystemExit(
            "fail-closed: tests/fixtures/melody_bench/registry.yaml sha256 does "
            "not match run1.json comparison.provenance.m1_registry_sha256 for "
            f"{len(mismatched_registry)} pair(s): {mismatched_registry[:5]}..."
        )

    # manifest.yaml が実際に run1.json 生成に使われたものと同一であることを、
    # run report 自身が記録する top-level manifest_sha256 との一致で検証する
    # （run1.json の主張を鵜呑みにせず、独立に読んだ manifest.yaml バイトから
    # 再計算した sha256 と突合する）。欠落は fail-closed（Codex レビュー #255
    # 第 11 巡 BB3）: レガシー/編集済みレポートで manifest_sha256 が無い場合、
    # 束縛検証を無条件でスキップして任意の manifest を無警告に受理すると、
    # split/kind/audio が違う無関係な manifest と組み合わさっても診断が
    # 「束縛済み provenance」として提示されてしまう穴があった。
    recorded_manifest_sha256 = run1_doc.get("manifest_sha256")
    if recorded_manifest_sha256 is None:
        raise SystemExit(
            "fail-closed: run1.json に top-level 'manifest_sha256' が無く、"
            f"{manifest_path} がこの run report の生成に実際に使われた manifest "
            "であることを検証できない（束縛未確認の manifest との組み合わせを "
            "拒否する）"
        )
    if recorded_manifest_sha256 != manifest_sha256:
        raise SystemExit(
            "fail-closed: tests/fixtures/melody_bench/m3d_pairs_manifest.yaml "
            f"sha256 ({manifest_sha256}) does not match run1.json['manifest_sha256'] "
            f"({recorded_manifest_sha256})"
        )

    # pair 集合の完全一致検証（Codex レビュー #255 第 12 巡 CC2）: 集計開始前に
    # set(run1_pairs) == {manifest pair_id} を確認する。manifest_sha256 の
    # 一致だけでは、編集済み run report が正しい manifest_sha256 を保持した
    # まま余剰 pair を追加/削除するケースを検出できない——それを許すと
    # §1（run1_pairs 直読み）と manifest 駆動の crosstab/§3/§5/§6 とで
    # 母集団が食い違う内部矛盾した診断を出力しうる。欠落・余剰のどちらも
    # fail-closed。
    manifest_pair_ids = {mp["pair_id"] for mp in manifest_pairs}
    run1_pair_ids = set(run1_pairs.keys())
    if run1_pair_ids != manifest_pair_ids:
        missing_from_run1 = sorted(manifest_pair_ids - run1_pair_ids)
        extra_in_run1 = sorted(run1_pair_ids - manifest_pair_ids)
        raise SystemExit(
            "fail-closed: run1.json の pair 集合が manifest の pair_id 集合と "
            "完全一致しない（本診断は set(run1_pairs) == set(manifest pair_id) を "
            f"前提に集計する）: missing_from_run1={missing_from_run1}, "
            f"extra_in_run1={extra_in_run1}"
        )

    gate_registry = registry_doc["observation_gate"]
    gap_threshold_sec = float(gate_registry["phrase_gap_sec"])

    section1 = _build_section1(run1_pairs, manifest_pairs, run1_display_path=_display_path(run1_path))
    section2 = _build_section2(registry_doc)
    section3, annotation_sha256 = _build_section3(
        manifest_pairs,
        run1_pairs,
        vocadito_dir,
        fixtures,
        gap_threshold_sec=gap_threshold_sec,
    )
    section4 = _build_section4(run1_pairs)
    section5 = _build_section5(manifest_pairs, run1_pairs)
    section6 = _build_section6(manifest_pairs, run1_pairs)

    inputs = {
        "run1_path": _display_path(run1_path),
        "run1_sha256": run1_sha256,
        "manifest_path": _display_path(manifest_path),
        "manifest_sha256": manifest_sha256,
        "m2c_external_fixtures_path": _display_path(m2c_fixtures_path),
        "m2c_external_fixtures_sha256": fixtures_sha256,
        "gate_definition_module": "src/svp_rpe/melody/observability.py",
        "gate_registry_path": _display_path(registry_path),
        "gate_registry_sha256": registry_sha256,
        "gate_registry_bound_to_run1_via": (
            "verified equal to pairs[*].comparison.provenance.m1_registry_sha256 "
            f"for all {len(tuning_pair_ids)} executed (non-holdout-lock) pairs"
        ),
        "vocadito_annotations_dir": _display_path(vocadito_dir),
        "vocadito_annotation_sha256": annotation_sha256,
        "generator_script_path": _display_path(GENERATOR_SCRIPT_PATH),
        "generator_script_sha256": _generator_script_sha256(),
    }

    diagnosis = {
        "generated_by": (
            "scripts/diagnose_m3d_notcomparable.py (committed, deterministic; "
            "Codex review #255 round 9)"
        ),
        "inputs": inputs,
        "note": "purely mechanical aggregation; no interpretation/verdict language included by design",
        "1_reason_histogram": section1,
        "2_gate_parameters": section2,
        "3_annotation_phrase_structure_table": section3,
        "4_crepe_observed_series_in_report": section4,
        "5_measured_pairs": section5,
        "6_coverage_floor_candidate_provenance": section6,
    }
    if generated_utc is not None:
        diagnosis["generated_utc"] = generated_utc
    return diagnosis


# --------------------------------------------------------------------------- #
# AA1（Codex レビュー #255 第 10 巡・P1）: --output の preflight 衝突拒否
# --------------------------------------------------------------------------- #
def _consumed_annotation_paths(
    manifest_pairs: List[Dict[str, Any]], vocadito_dir: Path
) -> List[Path]:
    """manifest の real pair から、``_verify_and_load_annotation`` が実際に
    読む vocadito F0 アノテーション CSV の全パスを、音声/CSV I/O 抜きで
    （manifest の軽量パースのみで）列挙する。``_build_section3`` の clip
    列挙ロジックと同じ判定基準（single source of truth は ``_material_of``/
    ``_basename_clip_id``）を使う——判定基準が将来ずれても、この関数だけを
    見れば preflight が実際の消費パスと一致していることが分かる。"""
    clip_ids = set()
    for mp in manifest_pairs:
        if _material_of(mp["pair_id"]) != "real":
            continue
        for audio_key in ("audio_a", "audio_b"):
            clip_id = _basename_clip_id(mp[audio_key])
            if clip_id is not None:
                clip_ids.add(clip_id)
    return [_annotation_path(vocadito_dir, clip_id) for clip_id in sorted(clip_ids)]


def _reject_output_input_collisions(
    *,
    output_path: Path,
    run1_path: Path,
    manifest_path: Path,
    registry_path: Path,
    m2c_fixtures_path: Path,
    annotation_paths: Sequence[Path],
) -> None:
    """resolve() 済み ``--output`` を、全宣言データ入力（``--run1``/
    ``--manifest``/``--registry``/``--m2c-fixtures``、および消費する全
    アノテーション CSV）の resolve() 済み絶対パス集合と照合し、1 件でも
    一致すれば導出（``build_diagnosis``）を呼ぶ前に fail-closed で拒否する
    （``build_m3d_pairs.py``/``screen_m3d_clips.py`` の ``_reject_output_
    input_collisions`` と同型。Codex レビュー #255 第 10 巡 AA1）。

    是正前は ``--output`` が誤ってこれらの入力のいずれかを指した場合、
    ``atomic_write_bytes`` が入力ファイルを診断 JSON バイト列で無警告に
    上書き破壊しうる穴があった（例: ``--output`` に ``--run1`` と同じパスを
    誤指定）。
    """
    output_resolved = Path(output_path).resolve()
    inputs: Dict[Path, str] = {
        Path(run1_path).resolve(): f"run1_path ({run1_path})",
        Path(manifest_path).resolve(): f"manifest_path ({manifest_path})",
        Path(registry_path).resolve(): f"registry_path ({registry_path})",
        Path(m2c_fixtures_path).resolve(): f"m2c_fixtures_path ({m2c_fixtures_path})",
    }
    for annotation_path in annotation_paths:
        inputs.setdefault(
            Path(annotation_path).resolve(),
            f"vocadito annotation CSV ({annotation_path})",
        )

    label = inputs.get(output_resolved)
    if label is not None:
        raise SystemExit(
            f"fail-closed: --output が入力と衝突している: {output_resolved} は "
            f"{label} と同じ解決済みパスを指す"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run1", type=Path, default=DEFAULT_RUN1_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--m2c-fixtures", type=Path, default=DEFAULT_M2C_FIXTURES_PATH)
    parser.add_argument("--vocadito-dir", type=Path, default=DEFAULT_VOCADITO_DIR)
    parser.add_argument(
        "--generated-utc",
        type=str,
        default=None,
        help="省略時は generated_utc を出力に含めない（決定論維持）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="指定時のみそのパスへ atomic write する。省略時は stdout へ出力する。",
    )
    args = parser.parse_args()

    # CC1（Codex レビュー #255 第 12 巡・P1）: manifest バイトはここで 1 回だけ
    # 読み（hash + パースを同一バッファから）、そのパース済み文書を
    # 以下 (a) preflight 用のアノテーション CSV 列挙 と (b) build_diagnosis
    # の本導出の両方へそのまま渡す。build_diagnosis 側は manifest を自分では
    # 読み直さない——preflight が守る集合と導出が消費する集合を、同一バッファ
    # へ構造的に一致させる（旧実装は 2 回読んでおり、その間隙で manifest が
    # 差し替わると preflight が旧版を、導出が新版を見る TOCTOU があった）。
    manifest_doc, _manifest_bytes, manifest_sha256 = _load_yaml(args.manifest)

    # AA1（Codex レビュー #255 第 10 巡・P1）: --output 指定時は、実際の
    # derivation（build_diagnosis の各種 fail-closed 検証・atomic write）を
    # 一切始める前に、resolve() 済み --output が全宣言データ入力（消費する
    # 全アノテーション CSV を含む）と衝突しないか preflight で確認する。
    # アノテーション CSV の集合は上で読んだ manifest_doc から機械的に決まる。
    if args.output is not None:
        annotation_paths = _consumed_annotation_paths(
            manifest_doc["pairs"], args.vocadito_dir
        )
        _reject_output_input_collisions(
            output_path=args.output,
            run1_path=args.run1,
            manifest_path=args.manifest,
            registry_path=args.registry,
            m2c_fixtures_path=args.m2c_fixtures,
            annotation_paths=annotation_paths,
        )

    diagnosis = build_diagnosis(
        run1_path=args.run1,
        manifest_path=args.manifest,
        manifest_doc=manifest_doc,
        manifest_sha256=manifest_sha256,
        registry_path=args.registry,
        m2c_fixtures_path=args.m2c_fixtures,
        vocadito_dir=args.vocadito_dir,
        generated_utc=args.generated_utc,
    )
    payload = (json.dumps(diagnosis, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if args.output is not None:
        atomic_write_bytes(args.output, payload)
    else:
        sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
