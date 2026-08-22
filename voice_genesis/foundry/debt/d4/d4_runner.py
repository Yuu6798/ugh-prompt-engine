"""debt/d4/d4_runner.py — VG-DEBT-004 (D4) run5-7 の TRF 1.2 再測定ランナー。

**machine-independent 部分**（計器の配線 + 事前登録 spec との pin 照合）のみを
本 PR の範囲とする。実行（レンダ・実測そのもの）は checkpoint / 音響モデル
実体に依存する machine-dependent 作業であり、本モジュールは`--help` 相当の
形状・fail-closed ガードだけを CI で検査する（`tests/test_d4_remeasure.py`）。

**凍結物は一切変更しない。** 本モジュールは以下を**読み取り専用で import 再利用**
するだけで、書き換えない:

- `run8/s7_0b_probe.py`（`verify_spec` / `load_gate_synth` / `run_group` を
  そのまま呼ぶ — render の契約は 8-0b probe と同一にする）
- `run8/s7_b1_v12.py`（`measure_candidate_12` を経由して 1.2 の測定ロジック
  = `analyse_shape` / `voiced_mask_12` を呼ぶ。内部で `run8/s7_b1_v11.py`
  を transitively import する — 1.1 の候補・測定基盤を再利用しているため）
- `run8/s7_b1_calibration.py`（`Stimulus` / `measure_voicing_axes` /
  `verify_analysis_stack`）
- `run8/s7_io.py` / `s7_export_manifest.py` / `s7_trf.py` の pin ガード群

**pin 閉包（v0.3・PR #306 レビュー第 2 巡 P1・ファミリー終端）**:
`d4_remeasure_spec.json` の `pins.sources` は、このモジュールを起点に
`enumerate_repo_import_closure()` が AST で機械列挙した推移 import 閉包
（`s7_b1_v12.py` / `s7_b1_v11.py` / `s7_b1_calibration.py` / `s7_0b_probe.py` /
`s7_io.py` / `s7_export_manifest.py` / `s7_trf.py` / `s7_spec.py` の 8 ファイル +
このモジュール自身 `d4_runner.py` の自己参照）と、機械列挙では辿れない特別枠
2 点（動的 load される render ハーネス `gate_synth.py` / データファイルの
凍結済み 1.2 spec）を合わせた計 11 点で構成される。`load_and_verify_d4_spec`
は起動のたびに `pins.sources`（特別枠を除く）と実際の import 閉包の完全一致も
検査する（`_verify_closure_matches_declared_pins`。取りこぼしの構造的防止）。

**データ入力閉包（v0.4・PR #306 レビュー第7巡 P1）**: 上記はコード（import
閉包）だけを機械的に網羅する。データファイル側は `cell_definition_source`
（`s7_0b_probe_spec.json`）と `trf_measurement_spec_1_2_sha256`
（`trf_measurement_spec_1_2.json`）の 2 点しか pin されておらず、
`cmd_render` / `cmd_measure` の実行経路を全数確認したところ、他に 8 点の
未 pin データ入力（`s7_exporter_input_pins.json` / `trf_measurement_spec.json`
[1.0] / B-1 事前登録 3 点 × 1.0・1.2 の 2 系列）が見つかった
（`EXPECTED_DATA_INPUT_KEYS` の docstring に内訳を記載）。`pins.data_inputs`
としてこれらも pin し、`load_and_verify_d4_spec` が起動のたびに実ファイルと
照合する（`_verify_data_inputs`）。コード閉包は AST で機械列挙できるが、
データ入力は「どの関数がどのパス定数を読むか」を辿る必要があり機械列挙は
していない（手動の全数確認。将来の呼び出し経路追加で同種の漏れが再発しうる
点は残存する既知の限界として明記する）。**検証→消費 TOCTOU の閉塞（第8巡
P2・本系統終端）**: `load_and_verify_d4_spec` の起動時検証と、`v12.
load_prereg_12()` / `b1.load_prereg()` / `trf.load_frozen_measurement()` /
`xm.verify_export_manifest()` が実測時に行う別読みの間には時間窓があった
（起動時検証を通過した直後にファイルが差し替わっても、loader 側の再読込は
新しいバイトを黙って通す）。消費のたびに loader が返す digest
（`_verify_consumed_digests`）または消費直後の再 hash（`_reverify_data_
inputs_after_consumption`）で pin と再照合し、この窓は data-input の消費時
digest 照合まで防御する（同一プロセス内でのファイルシステム外の改変 =
メモリ改竄は、上記「脅威モデルと境界」節と同じ理由で境界外——この系統も
これで終端する）。**残件の先回り閉塞**: 第8巡完了報告で境界外として明示した
`cell_definition_source`（`s7_0b_probe_spec.json`。`cmd_render` が
`probe0b.run_group()` の消費前に読む）も同じ TOCTOU ファミリーに属するため、
`_verify_consumed_cell_definition_digest` で同様に閉じた——`pins.data_inputs`
とは別枠の pin だが、消費時 digest 照合という意味では対象範囲外に残す理由が
無いため。

**脅威モデルと境界**（PR #306 レビュー第3巡 P2・`docs/DESIGN_M2_extraction_
accuracy.md` §6「Scorer pin の脅威モデルと境界」と同型の切り分け）: pin 検証
+ pre-bind（`_bind_pinned_modules`）が**守る**のは、受動的な取り違え・pin
陳腐化・「検証と import の間に古い/別バージョンが割り込む」事故——凍結物が
pin 後に書き換わった・spec が陳腐化した・pin 検証より前に何かが pinned
module を import 済みだった、など。これらは sha256 照合 + import 遅延 +
preloaded module 拒否（`sys.modules` 検査）で fail-closed 検出される。
**守らない（境界）**: プロセスの `sys.modules` / ファイルシステム / インタ
プリタ自体を能動的に書き換えられる攻撃者。この能力があれば `.pyc` の差し替え、
インタプリタの改竄、import **後**のモジュール属性の書き換え（`s7_io.foo =
lambda ...` のような差し替え）で、pin 検証をすり抜けて任意の実装を実行させ
られる——しかもこの経路は sha256 pin をどれだけ強化しても防げない（読んだ
ファイルと実行されるコードオブジェクトの同一性は、CPython の言語機能だけ
では証明できない）。したがって in-memory 系の防御は「preloaded module の
拒否」までで**終端**する。それ以上の防御（メモリ改竄検知・プロセス隔離等）
は本 runner のスコープ外とし、際限ない実装細部の追跡はしない。

**same-path preload の免除**（PR #306 レビュー第7巡 P2・見送り境界宣言）:
`_bind_pinned_modules` は「preload 済みだが `__file__` が期待パスと一致する」
場合は拒否しない（同一プロセス内で他のテストファイル等が既に正規ファイルを
import 済みなだけ、という区別。上記「守る/守らない」節で既述）。この免除は
**テストハーネス（pytest が全テストファイルを 1 プロセスへ集めて収集する）
との共存のための意図的な緩和**である——免除を撤廃すると、d4_runner.py を
複数回・複数経路から呼ぶテストスイート自体が preloaded_pinned_module で
落ちる。immune な経路として残るのは「long-lived プロセスで、検証が通った
**後**にソースファイルが in-place で書き換わり、`sys.modules` にはまだ
古いコードオブジェクトが residing している」ケース——`__file__` は変わらない
ので same-path 免除がこれを通してしまう。しかしこれは第3巡で宣言した
in-memory 系の境界（`.pyc` 差し替え・インタプリタ改竄・import 後のモジュール
属性改変と同種の「プロセス生存期間中の in-memory 改変」）の**範囲内**であり、
本 runner の脅威モデル**外**として扱う。本番経路は CLI 起動（`svprpe` 相当の
1 コマンド = fresh process）を前提とし、long-lived プロセスでの再入は
テストハーネスのみが行う運用だからである。したがってコード変更は行わない
（見送り）。

**render doc 改変系の終端**（PR #306 第4巡 P1）: render 群 JSON（`--render-doc`）
に対する改変検出も同じ理由で「個別フィールドの逐次検証」を打ち切り、
**doc バイト全体の digest 束縛**（`render_manifest.json` / `--render-manifest`）
を主防御にする。窓値 2 点の検証（`_verify_cell_window`。第3巡）はこの主防御の
**下の層**として残す（多層防御。manifest 未使用の生 8-0b probe 群 JSON を
`--render-doc` に直接渡す経路など、manifest 検査を経ない使い方が将来増えても
最低限の tamper-evidence を残すため）が、doc 全体が manifest の記録と一致すれば
窓値を含む全フィールドの整合は自動的に保証されるので、equal-shift のような
「窓値だけを個別に整合させた改竄」への対策を都度追加することはしない。

**manifest 自体の信頼アンカー**（PR #306 第5巡 P1）: 上記の doc 束縛は
「`--render-doc` のバイトが `--render-manifest` の記載と一致する」ことしか
保証しない — `render_manifest.json` 自体が render 完了後に差し替えられていたら
doc・manifest を**揃って**改竄する経路をすり抜ける。これを閉じるため、
`measure` は `--render-manifest-sha256`（`--render-manifest` と 1 対 1・同順で
対応する operator 供給の期待 sha256）を必須引数として要求し、doc 照合より
**前**に manifest ファイルの実バイト sha256 と照合する（不一致は即 abort）。
これにより **render 成果物の provenance 連鎖は、operator が render 完了時に
控えた manifest sha256 を信頼の根として終端する**（`--spec-sha256` と同型の
構造 — spec 自体も「operator が意図した版」を operator 供給値で束縛しており、
それより上流は問わない）。**operator 供給値自体の真正性**（控えの改竄・
operator のなりすまし・控えを渡す経路の汚染）は本 runner の脅威モデル**外**
とする（`--spec-sha256` と同じ前提 — この runner は「渡された期待値と実ファイル
の一致」だけを機械的に保証し、期待値をどう決めた・誰が渡したかは呼び出し側の
運用に委ねる）。

サブコマンド 2 つ:

- `render` — `s7_0b_probe.run_group` を直接呼び、1 群（話者 x 世代）ぶんの
  WAV + `input_meta`（測定窓）を書く。**測定は 1.0 のまま**（`run_group` の
  契約をそのまま使うため）で構わない — D4 が使うのは WAV と `input_meta` の
  みであり、1.0 測定値は消費しない（`d4_remeasure_spec_sha256` を束縛して
  レンダ出力に付す）。
- `measure` — `render` が書いた群 JSON（または同一 schema の 8-0b probe 群
  JSON）を読み、WAV を pin 照合してから `s7_b1_v12` の 1.2 選定候補で
  voicing 3 軸を測り直し、`d4_results.json` を書く。

fail-closed 起動ガード: 両サブコマンドとも、実行の**前**に本 spec
(`d4_remeasure_spec.json`) 自身と、その `pins` が指す 3 ファイル + セル定義
source の sha256 を実ファイルと照合する。1 つでも不一致なら `D4SpecMismatch`
で abort する（本番値を見て仕様を書き換える経路を構造的に閉じる）。加えて
`pins` のキー集合そのもの（欠落・余剰）と、D4 spec の `axes[].selected_candidate`
が凍結済み `trf_measurement_spec_1_2.json` の同軸 `selected_candidate` と逐語
一致することも検査する。

`--spec-sha256`（両サブコマンド必須）: operator が渡す「コミット済みのはずの
spec sha256」の期待値。実ファイルから計算した sha256 と一致しなければ abort
する — スクリプトの sha256 自己計算だけでは「operator が意図した版」までは
束縛できない（例えばコミット前の作業コピーを誤って指す事故）ため、期待値を
呼び出し側から明示的に渡させる。

`measure` は追加で、渡された render 群 JSON が D4 spec の登録内容（群 ID・
36 セルという規定数・cell_id 集合の欠落/重複なし）と一致することを検査する。
これは `d4_remeasure_spec_sha256` が None（= D4 render を経由しない生の 8-0b
probe 群 JSON）でも必ず通す。セル単位の測定失敗は `outcome: "error"` として
隔離し、他セルの測定は継続する（fail-closed = 全滅させない代わりに、1 件でも
エラーがあれば `d4_results.json` を書き切った上で非ゼロ終了する）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata as _md
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent                 # .../debt/d4
_DEBT_DIR = _HERE.parent                                 # .../debt
_FOUNDRY_DIR = _DEBT_DIR.parent                           # .../foundry
_RUN8_DIR = _FOUNDRY_DIR / "run8"
_RESULTS_S7_DIR = _FOUNDRY_DIR / "results_s7"
_S1_GATE_DIR = _FOUNDRY_DIR / "s1_gate"
_REPO_ROOT = _FOUNDRY_DIR.parents[1]

if str(_RUN8_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN8_DIR))

# --- pinned module 群（pre-bind 原則: PR #217 と同型・PR #306 レビュー第2巡
# P2） ------------------------------------------------------------------
#
# 以前は run8/ のモジュール群をここでモジュール top-level import していた。
# それだと「pin 検証」より**前**に実装コードが import＝実行されてしまい、
# 「検証した bytes」と「実際に走る実装」の同一性を import 時点では言えない
# （検証と import の間に何かが割り込める in-memory 窓が開いている）。
#
# 代わりに、実際の import は `_bind_pinned_modules()`（`load_and_verify_d4_spec`
# が pin 検証を**全て通過した後**にだけ呼ぶ）まで遅延する。検証前は
# `sys.modules` に一切現れない（`test_load_and_verify_does_not_import_
# pinned_modules_before_verification_passes` が回帰検査する）。
#
# numpy + librosa + soundfile はいずれも本体の必須依存であり pin 対象では
# ないため、この遅延の対象外（onnxruntime を要するのは
# `s7_0b_probe.load_gate_synth()` 内だけで、レンダ実行時にしか呼ばない）。
#
# 型チェッカ/読者向けの前方宣言（実行時は必ず `_bind_pinned_modules()` が
# 束縛する。束縛前に参照すると None のままの属性エラーで気づける）。
b1 = v12 = xm = s7_io = trf = probe0b = None

#: 束縛対象 = preload 検査対象の import 名（`sys.modules` のキーと一致する）。
#: `enumerate_repo_import_closure()` の定義後（本ファイル下方）で機械導出する
#: （PR #306 レビュー第5巡 P2）。ここでは前方宣言のみ — 実体は下記参照。
_PINNED_MODULE_NAMES: Tuple[str, ...]

#: このプロセスで `_bind_pinned_modules()` が一度でも成功したか（PR #306
#: レビュー第3巡 P2）。初回だけ「sys.modules がまだ pinned module 名で
#: 汚染されていないこと」を検査する — 2 回目以降の呼び出し（同一プロセス内で
#: `load_and_verify_d4_spec()` を複数回呼ぶ再入経路。テストスイートや、複数
#: 群を 1 プロセスでまとめて扱う将来の呼び出し側を含む）は、sys.modules に
#: 残っているのが**自分自身が前回束縛した**ものだと分かっているため誤検出
#: しない。Python の import キャッシュはプロセス内で同一モジュール名に常に
#: 同一オブジェクトを返す契約なので、初回に汚染が無かったことさえ確認できれば
#: 以降の再入は安全（in-memory 改変そのものは別途「脅威モデル境界」として
#: 本モジュール docstring で明文化し、ここでは検出しない）。
_pinned_modules_bound = False


def _bind_pinned_modules() -> None:
    """pin 検証を通過した**後にのみ**呼ぶ: pinned module 群を import して
    module globals（`b1`/`v12`/`xm`/`s7_io`/`trf`/`probe0b`）へ束縛する。

    **preloaded module の fail-closed 拒否**（PR #306 レビュー第3巡 P2）:
    このプロセスで初めて呼ばれるときに限り、束縛対象の名前が import 実行
    **前**の時点で既に `sys.modules` に存在し、かつその `__file__` が
    `run8/<name>.py` の期待パスと**違う**なら abort する
    （`reason=preloaded_pinned_module`）。検証は「ディスク上の現在のバイト列」
    に対して行っており、`sys.modules` に既にある得体の知れないモジュール
    オブジェクトがその検証済みバイト列から作られた保証は無い——検証より前の
    別経路（本 runner の制御が及ばない import・注入されたスタブ等）が先に
    持ち込んだ実装かもしれない。ただし `__file__` が期待パスと**一致**する
    場合は拒否しない: それは単に同一プロセス内の別のコード（同じ repo の
    他テスト等）が同じ正規ファイルを先に import していただけであり、
    Python の import キャッシュ機構がその**同一オブジェクト**を返すだけで
    脅威ではない（`d4_runner.py` 自身を複数回・複数経路から呼ぶ用途——
    pytest が全テストファイルを 1 プロセスへ集めて収集する等——を壊さない
    ための区別）。
    """
    global b1, v12, xm, s7_io, trf, probe0b, _pinned_modules_bound
    if not _pinned_modules_bound:
        suspicious = []
        for name in _PINNED_MODULE_NAMES:
            existing = sys.modules.get(name)
            if existing is None:
                continue
            expected_path = (_RUN8_DIR / f"{name}.py").resolve()
            existing_file = getattr(existing, "__file__", None)
            if existing_file is None or Path(existing_file).resolve() != expected_path:
                suspicious.append((name, existing_file))
        if suspicious:
            detail = ", ".join(f"{n}: __file__={f!r}" for n, f in suspicious)
            raise D4SpecMismatch(
                f"reason=preloaded_pinned_module: {detail} — 期待パス配下"
                f"（{_RUN8_DIR}）以外から既に sys.modules に存在していた。検証した "
                "bytes が実際に実行されるモジュールと同一である保証が無いため "
                "fail-closed で abort する（in-memory 系の防御はここまで — .pyc "
                "差し替え・インタプリタ改竄・import 後のモジュール属性改変は本"
                "モジュール docstring の脅威モデル節に記載のとおり対象外）"
            )
    b1 = importlib.import_module("s7_b1_calibration")
    v12 = importlib.import_module("s7_b1_v12")
    xm = importlib.import_module("s7_export_manifest")
    s7_io = importlib.import_module("s7_io")
    trf = importlib.import_module("s7_trf")
    probe0b = importlib.import_module("s7_0b_probe")
    _pinned_modules_bound = True


SPEC_SCHEMA = "vg-d4-remeasure-spec/0.4"
RENDER_SCHEMA = "vg-d4-render-group-result/0.1"
RESULTS_SCHEMA = "vg-d4-remeasure-results/0.1"
#: PR #306 レビュー第4巡 P1（方式 a・窓系ファミリー終端）: `cmd_render` が
#: 群 doc と同階層へ書く digest 束縛台帳。`cmd_measure` は `--render-manifest`
#: （複数可・必須）でこれを読み、`--render-doc` の実バイト sha256 と group_id
#: 単位で照合する。群単位の窓値検証（3巡・多層防御として残置）とは別に、
#: doc バイト**全体**を束縛することで「窓値だけを個別に整合させた改竄」を
#: 含む doc 全体の改変を一括で検出する（equal-shift 等、窓値検証の抜け穴を
#: 都度塞ぐ個別対応の打ち切り）。
RENDER_MANIFEST_SCHEMA = "vg-d4-render-manifest/0.1"
DEBT_REF = "VG-DEBT-004"

SPEC_PATH = _HERE / "d4_remeasure_spec.json"

#: D4 が測る 3 軸（voicing のみ）。`terminal_mel_persistence` は closeout §2-2
#: により対象外（spec の `axes_out_of_scope` に明記）。
D4_AXES: Tuple[str, ...] = (
    "excess_tail_voiced_ms",
    "release_after_score_boundary_ms",
    "tail_f0_persistence",
)


class D4SpecMismatch(RuntimeError):
    """D4 事前登録 spec 自身、または pin 対象ファイルが期待 sha256 と食い違った
    （fail-closed）。"""


class D4WindowMismatch(RuntimeError):
    """render 済み群 JSON の `input_meta`（測定窓）が、pinned cell 定義
    （`cell_definition_source`）/ pinned `trf_measurement_spec_1_2.json` から
    導出した期待値と食い違った（PR #306 レビュー第3巡 P1。doc 編集による
    窓差し替えの偽測定経路を閉塞する）。セル単位で隔離し、他セルの測定は継続
    する（fail-closed の failure_policy と同じ扱い）。"""


#: `pins.sources` が持つべきキー集合（厳密一致。欠落・余剰とも abort）。
#: v0.3（PR #306 レビュー第2巡 P1・ファミリー終端）で、hand-list の逐次追加を
#: 打ち切り、`d4_runner.py` を起点とした AST 推移 import 閉包の**機械列挙**に
#: 揃えた（`enumerate_repo_import_closure` / `NON_IMPORT_PIN_SOURCE_KEYS` 参照。
#: PR #216 の前例と同型）。`s7_io_sha256` / `s7_export_manifest_sha256` /
#: `s7_trf_sha256` / `s7_spec_sha256` は v0.2 まで手動で追加できていなかった
#: 依存（機械列挙で初めて判明した）。`d4_runner_sha256` は**自己参照**
#: （このモジュールが起動時に自分自身のバイト列を読み、spec の pin と照合
#: する）— 循環にはならない: spec 側は「runner のあるべき sha」を主張する
#: だけで runner の内容そのものを内包しないため、runner が spec を読んで
#: 自分の sha を確認する経路は素直に成立する。
EXPECTED_PIN_SOURCE_KEYS = frozenset({
    "trf_measurement_spec_1_2_sha256", "instrument_sha256", "render_harness_sha256",
    "s7_b1_v11_sha256", "s7_b1_calibration_sha256", "s7_0b_probe_sha256", "d4_runner_sha256",
    "s7_io_sha256", "s7_export_manifest_sha256", "s7_trf_sha256", "s7_spec_sha256",
})
#: `pins` トップレベルが持つべきキー集合（上記 11 キー + cell_definition_source +
#: sources 自身 + data_inputs（PR #306 レビュー第7巡 P1）。厳密一致。
EXPECTED_PIN_KEYS = EXPECTED_PIN_SOURCE_KEYS | {"cell_definition_source", "sources", "data_inputs"}

#: `pins.data_inputs` が持つべきキー集合（PR #306 レビュー第7巡 P1・データ入力
#: 閉包・厳密一致）。コード閉包（`pins.sources`）とは別に、`d4_runner.py` の
#: 実行経路（`cmd_render` / `cmd_measure`）が起動時に読む JSON データファイルを
#: 全数確認した結果——`cell_definition_source`（`s7_0b_probe_spec.json`）と
#: `trf_measurement_spec_1_2_sha256`（`trf_measurement_spec_1_2.json`）は既に
#: 別枠で pin 済みだが、それ以外に 8 点の未 pin データ入力が見つかった:
#:
#: - `s7_exporter_input_pins` — `cmd_render` → `xm.verify_export_manifest()` →
#:   `load_input_pins()` が読む（checkpoint/config の世代別 pin 台帳）
#: - `trf_measurement_spec_1_0` — `cmd_render` → `trf.load_frozen_measurement()`
#:   が読む（凍結済み 1.0 測定値。D4 の `measure` 自体は消費しないが、
#:   `render` 経路が実際に読む以上データ入力閉包の対象に含める）
#: - `s7_b1_candidate_space_1_0` / `s7_b1_calibration_set_1_0` /
#:   `s7_b1_selection_rule_1_0` — `cmd_measure` → `b1.verify_analysis_stack(
#:   b1.load_prereg())` が読む（1.0 の B-1 事前登録 3 点。analysis stack pin
#:   の宣言元）
#: - `s7_b1_candidate_space_1_2` / `s7_b1_calibration_set_1_2` /
#:   `s7_b1_selection_rule_1_2` — `cmd_measure` → `_resolve_axis_candidates()`
#:   → `v12.load_prereg_12()` が読む（1.2 の B-1 事前登録 3 点。候補空間の
#:   定義元）
EXPECTED_DATA_INPUT_KEYS = frozenset({
    "s7_exporter_input_pins", "trf_measurement_spec_1_0",
    "s7_b1_candidate_space_1_0", "s7_b1_calibration_set_1_0", "s7_b1_selection_rule_1_0",
    "s7_b1_candidate_space_1_2", "s7_b1_calibration_set_1_2", "s7_b1_selection_rule_1_2",
})

#: `pins.sources` のうち、AST の静的 import 解析では辿れない特別枠。
#: `render_harness_sha256`（`s1_gate/gate_synth.py`）は `s7_0b_probe.
#: load_gate_synth()` が `importlib.util.spec_from_file_location` で動的に
#: 読む（文字列の import 名を経由しないため機械列挙の対象外）。
#: `trf_measurement_spec_1_2_sha256` は python モジュールではなく凍結済み
#: JSON データファイルそのもの。いずれも `enumerate_repo_import_closure` との
#: 適合検査（下記）から除外し、手動管理のまま残す。
NON_IMPORT_PIN_SOURCE_KEYS = frozenset({"render_harness_sha256", "trf_measurement_spec_1_2_sha256"})


# --- fail-closed: 事前登録 spec の pin を実ファイルと照合 -------------------


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json_pure(path: Path) -> Tuple[Dict[str, Any], str, int]:
    """`s7_io.read_json_with_pin` と同じ契約（doc, sha256, バイト数）を、
    `s7_io` を import せず標準ライブラリだけで実装したもの。

    pin 検証がまだ完了していない段階（= pinned module をまだ import して
    いない段階）で使う（pre-bind 原則。PR #217 と同型）。検証を担う本関数
    自身が、検証対象のはずの `s7_io.py` に依存してはいけない — 依存すると
    「s7_io.py の bytes を検証する前に s7_io.py の実装を実行する」窓が開く。
    """
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8")), sha, len(raw)


# --- import 閉包の機械導出（PR #306 レビュー第2巡 P1・PR #216 の前例と同型） --


def _ast_import_edges(tree: ast.AST) -> FrozenSet[str]:
    """1 ファイルの AST から、repo 内モジュールへ辿りうる edge（トップレベル
    モジュール名）を集める。静的 `import X` / `from X import Y` に加え、
    `_bind_pinned_modules` のような `importlib.import_module("X")`（文字列
    リテラル引数）も検出する — pre-bind 原則（PR #217）で import が文字列
    経由になった箇所を、閉包列挙が見失わないため。"""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            is_import_module_call = (
                (isinstance(func, ast.Attribute) and func.attr == "import_module")
                or (isinstance(func, ast.Name) and func.id == "import_module")
            )
            if is_import_module_call and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    names.add(arg0.value.split(".")[0])
    return frozenset(names)


def enumerate_repo_import_closure(
    entry_files: Sequence[Path], search_dirs: Sequence[Path], repo_root: Path,
) -> FrozenSet[Path]:
    """`entry_files` を起点に、repo 内モジュールへの推移 import 閉包を AST で
    機械列挙する（PR #216 の前例と同型。hand-list の逐次追加を打ち切る）。

    標準ライブラリ・外部パッケージ（numpy・librosa 等）は `search_dirs` 配下に
    実ファイルが無いため自然に除外される。動的 import（`importlib.util.
    spec_from_file_location` 経由の gate_synth.py 等）は辿らない —
    `NON_IMPORT_PIN_SOURCE_KEYS` として手動管理する別枠になる。

    戻り値は `repo_root` 基準の相対 `Path` の集合（`entry_files` 自身を含む）。
    """
    def _resolve(name: str) -> Optional[Path]:
        for d in search_dirs:
            cand = Path(d) / f"{name}.py"
            if cand.is_file():
                return cand.resolve()
        return None

    repo_root = Path(repo_root).resolve()
    visited: set = set()
    queue = [Path(f).resolve() for f in entry_files]
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for name in _ast_import_edges(tree):
            resolved = _resolve(name)
            if resolved is not None and resolved not in visited:
                queue.append(resolved)
    return frozenset(p.relative_to(repo_root) for p in visited)


#: `_PINNED_MODULE_NAMES` の実体（PR #306 レビュー第5巡 P2・hand-list 廃止）。
#: 以前は 6 モジュールの hand-list だった。`_bind_pinned_modules()` が実際に
#: `importlib.import_module()` するのはこの 6 個の**直接**依存だけだが、preload
#: 検査（`sys.modules` に既に別バイトのものが居ないか）が同じ 6 個しか見ていない
#: と、それらが transitively import する `s7_b1_v11`（`s7_b1_v12` 経由）や
#: `s7_spec`（`s7_io`/`s7_0b_probe` 経由）が preload 汚染されていても見逃す ——
#: Python の import キャッシュは `import s7_b1_v11` を「`sys.modules["s7_b1_v11"]`
#: に何かあればそれを返す」だけなので、直接 import する側が検証済みでも
#: 推移依存側が汚染されていれば素通りする。
#:
#: `enumerate_repo_import_closure()`（機械列挙・PR #216 の前例と同型。すでに
#: `_verify_closure_matches_declared_pins` が pin 突合に使っている）は
#: d4_runner.py 自身を起点とする**完全な**推移閉包を返すため、これを再利用して
#: 「d4_runner.py 自身を除いた閉包内の全モジュール名」を preload 検査対象にする
#: — 直接 import する 6 個も含む上位集合になるので、取りこぼしが構造的に無くなる
#: （閉包は d4_runner.py および依存先のバイト列が変わらない限り不変なので、
#: モジュール import 時に一度だけ計算すれば足りる）。
_PINNED_MODULE_NAMES = tuple(sorted(
    p.stem
    for p in enumerate_repo_import_closure([Path(__file__)], [_HERE, _RUN8_DIR], _REPO_ROOT)
    if p != Path(__file__).resolve().relative_to(_REPO_ROOT)
))


def _verify_pins_key_shape(pins: Dict[str, Any]) -> None:
    """`pins` / `pins.sources` のキー集合を先に厳密検査する（値の照合より前）。
    欠落・余剰のどちらも spec の陳腐化・改竄の兆候として abort する。"""
    got_top = set(pins.keys())
    if got_top != EXPECTED_PIN_KEYS:
        raise D4SpecMismatch(
            f"pins のキー集合が期待と違う: missing={sorted(EXPECTED_PIN_KEYS - got_top)} "
            f"extra={sorted(got_top - EXPECTED_PIN_KEYS)}"
        )
    got_sources = set(pins.get("sources", {}).keys())
    if got_sources != EXPECTED_PIN_SOURCE_KEYS:
        raise D4SpecMismatch(
            f"pins.sources のキー集合が期待と違う: "
            f"missing={sorted(EXPECTED_PIN_SOURCE_KEYS - got_sources)} "
            f"extra={sorted(got_sources - EXPECTED_PIN_SOURCE_KEYS)}"
        )
    got_data_inputs = set(pins.get("data_inputs", {}).keys())
    if got_data_inputs != EXPECTED_DATA_INPUT_KEYS:
        raise D4SpecMismatch(
            f"pins.data_inputs のキー集合が期待と違う: "
            f"missing={sorted(EXPECTED_DATA_INPUT_KEYS - got_data_inputs)} "
            f"extra={sorted(got_data_inputs - EXPECTED_DATA_INPUT_KEYS)}"
        )


def _verify_data_inputs(data_inputs: Dict[str, Any]) -> None:
    """`pins.data_inputs`（PR #306 レビュー第7巡 P1・データ入力閉包）の各エントリ
    （`{"path": ..., "sha256": ...}`）を実ファイルと照合する。キー集合の厳密
    一致は `_verify_pins_key_shape` が先に済ませている（値の照合より前）。"""
    for key, entry in data_inputs.items():
        path = entry.get("path")
        want = entry.get("sha256")
        if not isinstance(path, str) or not path or not isinstance(want, str) or not want:
            raise D4SpecMismatch(f"pins.data_inputs.{key} に path/sha256 が欠けている")
        got = _sha_file(_REPO_ROOT / path)
        if got != want:
            raise D4SpecMismatch(
                f"{path}: sha256 {got} が spec pin data_inputs.{key}={want} と違う"
                "（凍結物が pin 後に変わったか、pin 自体が陳腐化している）"
            )


#: `pins.data_inputs` の各エントリの `path` を basename で引けるようにした逆引き。
#: 消費時 digest 照合（下記 2 関数）で、loader が返す `{filename: sha256}` /
#: モジュール属性のパスから対応する data_inputs key を引くのに使う。
def _data_input_keys_by_filename(data_inputs: Dict[str, Any]) -> Dict[str, str]:
    return {Path(entry["path"]).name: key for key, entry in data_inputs.items()}


def _verify_consumed_digests(
    data_inputs: Dict[str, Any], consumed: Dict[str, str], *, source: str,
) -> None:
    """**検証→消費 TOCTOU の閉塞・方式 (a)**（PR #306 レビュー第8巡 P2）:
    `load_and_verify_d4_spec` が起動時に照合した `pins.data_inputs` と、
    実際に測定へ使われる loader（`v12.load_prereg_12` / `b1.load_prereg`）が
    その場で読んだバイト列から作った digest を**消費直後に**再照合する。

    起動時検証と実消費（loader の `s7_io.read_json_with_pin`）は**別読み**
    であり、両者の間に時間窓が空く——起動時検証を通過した直後にファイルが
    差し替えられても、loader 側は新しいバイトを黙って読んでしまう
    （TOCTOU）。ここでは loader が返り値として**既に**運んでくる digest
    （`v12.load_prereg_12()` の `pins` / `b1.load_prereg()` の `Prereg.pins`）
    を再利用するので、追加の読み込みは発生しない（PR #217 の「memo 迂回
    再検証」と同型 — 検証は消費した実体そのものに対して行う）。

    `consumed` は `{basename: sha256}`。`data_inputs` に対応が無いファイル名
    は対象外として黙って無視する（loader が pin 対象外の周辺ファイルの
    digest も一緒に返す可能性を考慮し、無関係な不一致を誤検出しないため）。
    """
    by_filename = _data_input_keys_by_filename(data_inputs)
    for filename, got in consumed.items():
        key = by_filename.get(filename)
        if key is None:
            continue
        want = data_inputs[key]["sha256"]
        if got != want:
            raise D4SpecMismatch(
                f"{source}: 消費時に読んだ {filename} の sha256 {got} が "
                f"pins.data_inputs.{key}={want} と違う（起動時検証と実消費の間に "
                "差し替えられた疑い = 検証→消費 TOCTOU）"
            )


def _reverify_data_inputs_after_consumption(
    data_inputs: Dict[str, Any], keys: Sequence[str], *, source: str,
) -> None:
    """**検証→消費 TOCTOU の閉塞・方式 (b)**（PR #306 レビュー第8巡 P2）:
    loader（`trf.load_frozen_measurement` 内部の `b1.load_prereg()` 呼び出し・
    `xm.verify_export_manifest` 内部の `load_input_pins()` 呼び出し）が返り値
    として digest を運んでこない場合のフォールバック。対象の loader 呼び出し
    が**戻った直後**に該当ファイルを再度 hash し、`pins.data_inputs` と照合する。

    厳密には「loader がその場で読んだバイト列そのもの」との同一性ではなく
    「loader 呼び出しの前後どこかで実ファイルが変わっていないか」の近似（消費
    時点との厳密同一ではない）だが、窓は実用上ほぼ閉じる——TOCTOU 攻撃者が
    loader 呼び出しの実行時間内という極めて狭い区間だけを狙って差し替えて
    元に戻す、という経路のみが残る（後述のプロセス内改変の脅威モデル境界と
    同種の残余）。"""
    for key in keys:
        entry = data_inputs[key]
        got = _sha_file(_REPO_ROOT / entry["path"])
        want = entry["sha256"]
        if got != want:
            raise D4SpecMismatch(
                f"{source}: 消費直後に再 hash した {entry['path']} の sha256 {got} が "
                f"pins.data_inputs.{key}={want} と違う（起動時検証と実消費の間に "
                "差し替えられた疑い = 検証→消費 TOCTOU）"
            )


def _verify_closure_matches_declared_pins(sources: Dict[str, str]) -> None:
    """`pins.sources`（`NON_IMPORT_PIN_SOURCE_KEYS` を除く）の相対パス集合が、
    `d4_runner.py` を起点に AST で機械列挙した実際の import 閉包と**完全一致**
    することを検査する（PR #306 レビュー第2巡 P1・ファミリー終端）。

    取りこぼしの構造的防止: 将来 `d4_runner.py` やその依存が新しい repo 内
    モジュールを import するようになったら、spec 側の pin 追加を忘れている
    限りこの検査で必ず落ちる（CI テストだけでなく起動時にも効く）。
    """
    declared = {
        Path(rel_path) for key, rel_path in sources.items()
        if key not in NON_IMPORT_PIN_SOURCE_KEYS
    }
    closure = enumerate_repo_import_closure(
        [Path(__file__)], [_HERE, _RUN8_DIR], _REPO_ROOT,
    )
    if declared != closure:
        raise D4SpecMismatch(
            "pins.sources（render_harness / trf_measurement_spec_1_2 を除く）が、"
            "d4_runner.py を起点に AST 機械列挙した実際の import 閉包と違う: "
            f"missing={sorted(str(p) for p in closure - declared)} "
            f"extra={sorted(str(p) for p in declared - closure)}"
            "（依存が増減したら pins.sources の更新を忘れずに）"
        )


def load_and_verify_d4_spec(
    spec_path: Path = SPEC_PATH, *, expected_sha256: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """`d4_remeasure_spec.json` を読み、schema・軸集合・pins 全件を実ファイルと
    照合してから返す。**測定より前に必ず呼ぶ**（両サブコマンドの入口)。

    `expected_sha256` を渡すと、operator が明示した期待 spec sha256（CLI の
    `--spec-sha256`）と実ファイルの sha256 を照合する（不一致は abort）。
    省略時（テスト等）はこの照合をスキップする。

    **pre-bind 原則**（PR #217 と同型・PR #306 レビュー第2巡 P2）: 本関数は
    pinned module（`s7_b1_calibration` 等）を一切 import しない
    （`_read_json_pure` で完結する）。全検証を通過した**後にのみ**
    `_bind_pinned_modules()` を呼んで import する — 検証と import の間に
    他コードが割り込める窓を構造的に閉じる。
    """
    spec, spec_sha, _ = _read_json_pure(spec_path)
    if expected_sha256 is not None and spec_sha != expected_sha256:
        raise D4SpecMismatch(
            f"--spec-sha256 {expected_sha256!r} が {spec_path} の実 sha256 "
            f"{spec_sha!r} と違う（operator が束縛した期待版と現物が食い違う）"
        )
    if spec.get("schema") != SPEC_SCHEMA:
        raise D4SpecMismatch(f"schema {spec.get('schema')!r} != {SPEC_SCHEMA!r}")
    if spec.get("debt_ref") != DEBT_REF:
        raise D4SpecMismatch(f"debt_ref {spec.get('debt_ref')!r} != {DEBT_REF!r}")

    axes = spec.get("axes", {})
    if set(axes) != set(D4_AXES):
        raise D4SpecMismatch(f"axes {sorted(axes)} != {sorted(D4_AXES)}（voicing 3軸のみ）")

    pins = spec.get("pins", {})
    _verify_pins_key_shape(pins)
    sources = pins["sources"]
    for key, rel_path in sources.items():
        want = pins.get(key)
        if not isinstance(want, str) or not want:
            raise D4SpecMismatch(f"pins.{key} が欠けている")
        got = _sha_file(_REPO_ROOT / rel_path)
        if got != want:
            raise D4SpecMismatch(
                f"{rel_path}: sha256 {got} が spec pin {key}={want} と違う"
                "（凍結物が pin 後に変わったか、pin 自体が陳腐化している）"
            )

    cds = pins.get("cell_definition_source", {})
    cds_path = cds.get("path")
    cds_sha = cds.get("sha256")
    if not cds_path or not cds_sha:
        raise D4SpecMismatch("pins.cell_definition_source に path/sha256 が欠けている")
    got_cds = _sha_file(_REPO_ROOT / cds_path)
    if got_cds != cds_sha:
        raise D4SpecMismatch(
            f"{cds_path}: sha256 {got_cds} が spec pin cell_definition_source と違う"
        )

    _verify_data_inputs(pins["data_inputs"])

    _verify_closure_matches_declared_pins(sources)

    _verify_axes_match_frozen_spec_1_2(axes, _REPO_ROOT / sources["trf_measurement_spec_1_2_sha256"])

    # ここまで到達 = 全 pin が実ファイルと一致した。**ここで初めて** pinned
    # module を import する（pre-bind 原則。上記のどこかで raise していれば
    # この行には到達せず、`sys.modules` に一切現れない）。
    _bind_pinned_modules()

    return spec, spec_sha


def _verify_axes_match_frozen_spec_1_2(axes: Dict[str, Any], trf12_path: Path) -> None:
    """D4 spec の `axes[].selected_candidate` が、凍結済み
    `trf_measurement_spec_1_2.json` の同軸 `selected_candidate` と逐語一致する
    ことを検査する（不一致 = D4 spec 側の typo として abort。凍結物は読むだけ）。
    pre-bind 原則により `s7_io` ではなく `_read_json_pure` で読む
    （`load_and_verify_d4_spec` からしか呼ばれず、その時点ではまだ pinned
    module を import していないため）。
    """
    trf12, _, _ = _read_json_pure(trf12_path)
    for axis, cfg in axes.items():
        want = cfg.get("selected_candidate")
        got = trf12.get("axes", {}).get(axis, {}).get("selected_candidate")
        if got != want:
            raise D4SpecMismatch(
                f"axis {axis!r}: D4 spec の selected_candidate {want!r} が "
                f"凍結済み trf_measurement_spec_1_2.json の {got!r} と違う"
                "（D4 spec 側の typo の可能性）"
            )


def _load_cell_definition(spec: Dict[str, Any]) -> Dict[str, Any]:
    """D4 spec が pin する `cell_definition_source`（`s7_0b_probe_spec.json`）
    を読む。sha256 は `load_and_verify_d4_spec` が既に照合済みだが、呼び出し
    経路が増えても壊れないよう独立にも確認する。"""
    cds = spec["pins"]["cell_definition_source"]
    path = _REPO_ROOT / cds["path"]
    doc, sha, _ = _read_json_pure(path)
    if sha != cds["sha256"]:
        raise D4SpecMismatch(f"{path}: sha256 {sha} が spec pin と違う")
    return doc


def _verify_consumed_cell_definition_digest(spec: Dict[str, Any], got_sha256: str) -> None:
    """**検証→消費 TOCTOU 閉塞**（PR #306 レビュー第8巡の自主検出残件・先回り
    対応）: `cmd_render` が `probe0b.SPEC_PATH`（`s7_0b_probe_spec.json` =
    `pins.cell_definition_source`）を消費のために読んだ直後、その digest を
    起動時検証済みの pin と再照合する。

    `load_and_verify_d4_spec` の起動時検証と `cmd_render` 内の別読み
    （`s7_io.read_json_with_pin(probe0b.SPEC_PATH)`）の間には時間窓があり、
    `pins.data_inputs` の 8 点（第8巡 P2・`_verify_consumed_digests` /
    `_reverify_data_inputs_after_consumption`）と同じ理由でこの窓も閉じる
    必要がある——`cell_definition_source` は `data_inputs` とは別枠の pin
    （セル定義・群構成の正本）だが、消費時 digest 照合の対象という点では
    同じ TOCTOU ファミリーに属する（第8巡完了報告で境界外として明示した
    残件をここで閉じる）。`probe0b.run_group()`（実消費）より**前**に呼ぶ。
    """
    cds = spec["pins"]["cell_definition_source"]
    want = cds["sha256"]
    if got_sha256 != want:
        raise D4SpecMismatch(
            f"{cds['path']}: 消費時に読んだ sha256 {got_sha256} が "
            f"pins.cell_definition_source.sha256={want} と違う（起動時検証と実消費の間に "
            "差し替えられた疑い = 検証→消費 TOCTOU）"
        )


#: `score_boundary_s - note_onset_s` の許容誤差（秒）。float 丸め誤差のみを
#: 吸収する（`windows_from_command` の演算は frame_ms 由来の乗除算のみで、
#: 2026-08-22 実測 360 セル全数で 1e-9 未満の誤差だった。тamper 検出の感度を
#: 落とさないため十分小さく取る）。
_WINDOW_SPAN_EPS_S = 1e-6


def _load_window_expectations(spec: Dict[str, Any]) -> Tuple[Dict[str, Tuple[float, float]], float]:
    """pinned cell 定義 + pinned `trf_measurement_spec_1_2.json` から、セル毎の
    測定窓の期待値を導出する（PR #306 レビュー第3巡 P1・「方式 b」）。

    **導出できる範囲**: `score_boundary_s - note_onset_s == duration_beats *
    60 / tempo_bpm`（拍長由来の時間窓幅）と `tail_window_ms`（グローバル定数）
    の 2 点のみ。`note_onset_s` / `commanded_note_end_s` の絶対値は render 時の
    duration predictor 出力（`final_phone_dur`）に依存し、onnxruntime での
    モデル推論なしに独立再導出できない（P-ANCHOR セルは対象ノートより前の
    全フレーズ分のフレームを含むため、`target_note_index` の再導出も要る —
    調査の結果、`P-RI-MEDIAL` 系の診断セルも対象ノートが先頭でないことが
    実測で判明し、`diagnostic_score` 一律の単純な「先頭ノート」仮定は棄却
    した）。measure は CPU 音声解析のみに留める設計（render 側の
    onnxruntime 依存を持ち込まない）ため、この 2 点への限定は意図的な範囲
    選択である。2026-08-22 実測の render doc 360 セル全数で実測検証済み
    （両方とも 0 件不一致）。
    """
    cell_def = _load_cell_definition(spec)
    expectations = {
        str(c["cell_id"]): (float(c["duration_beats"]), float(c["tempo_bpm"]))
        for c in cell_def["cells"]
    }
    trf12_path = _REPO_ROOT / spec["pins"]["sources"]["trf_measurement_spec_1_2_sha256"]
    trf12, _, _ = _read_json_pure(trf12_path)
    expected_tail_window_ms = float(trf12["boundaries"]["tail_window_ms"])
    return expectations, expected_tail_window_ms


def _verify_cell_window(
    cid: str, meta: Dict[str, Any],
    expect: Optional[Tuple[float, float]], expected_tail_window_ms: float,
) -> None:
    """1 セルの `input_meta` を pinned 期待値と照合する。不一致は
    `D4WindowMismatch`（呼び出し側が捕まえて `status: window_mismatch` で
    隔離する）。`expect` が None（cell_id が pinned 定義に無い）は、
    `_verify_group_doc_registration` が既に cell_id 集合の厳密一致を検査
    済みのため通常到達しない — 到達したら素通しさせず明示的に abort する。
    """
    if expect is None:
        raise D4WindowMismatch(f"{cid}: pinned cell 定義に窓期待値が無い")
    exp_beats, exp_tempo = expect
    exp_span = exp_beats * 60.0 / exp_tempo
    got_span = float(meta["score_boundary_s"]) - float(meta["note_onset_s"])
    if abs(got_span - exp_span) > _WINDOW_SPAN_EPS_S:
        raise D4WindowMismatch(
            f"{cid}: score_boundary_s-note_onset_s={got_span:.9f} が pinned cell "
            f"定義（duration_beats={exp_beats}/tempo_bpm={exp_tempo}）から導出した"
            f"期待値 {exp_span:.9f} と食い違う（doc 編集による窓差し替えの疑い）"
        )
    got_tail = float(meta["tail_window_ms"])
    if abs(got_tail - expected_tail_window_ms) > 1e-9:
        raise D4WindowMismatch(
            f"{cid}: tail_window_ms={got_tail} が pinned trf_measurement_spec_1_2."
            f"json の boundaries.tail_window_ms={expected_tail_window_ms} と違う"
        )


def _resolve_axis_candidates(spec: Dict[str, Any]) -> Dict[str, "v12.Cand12"]:
    """D4 spec の `axes[].selected_candidate`（文字列）を、`s7_b1_v12` が実際に
    列挙する候補（`enumerate_candidates_12`）から candidate_id 一致で解決する
    （`s7_0b_remeasure_12.load_winners` と同じ方式。文字列から `Cand12` を
    独自に再構成しない — 候補空間の定義は常に `s7_b1_v12` 側が正）。

    **検証→消費 TOCTOU 閉塞・方式 (a)**（PR #306 レビュー第8巡 P2）: `v12.
    load_prereg_12()` が返す `pins`（実際に読んだ 1.2 事前登録 3 点の digest）
    を、起動時に検証済みの `pins.data_inputs` と再照合する。"""
    cs, _cal, _rule, consumed_pins = v12.load_prereg_12()
    _verify_consumed_digests(
        spec["pins"]["data_inputs"], consumed_pins, source="v12.load_prereg_12",
    )
    by_id = {c.candidate_id: c for c in v12.enumerate_candidates_12(cs)}
    out: Dict[str, "v12.Cand12"] = {}
    for axis, cfg in spec["axes"].items():
        cid = str(cfg["selected_candidate"])
        if cid not in by_id:
            raise D4SpecMismatch(
                f"axis {axis!r}: candidate_id {cid!r} が s7_b1_v12 の 1.2 候補"
                "空間（enumerate_candidates_12）に無い"
            )
        out[axis] = by_id[cid]
    return out


# --- runtime_stack（PR #306 レビュー指摘: render stack の実測を結果へ埋め込む） --


#: `runtime_stack` に記録するパッケージ。`s7_b1_real_render_manifest.json` の
#: `render_stack`（numpy/onnxruntime/soundfile/PyYAML/python）と同じ語彙にする
#: （2026-08-22 の D4 実測はこの記録先が無く、`d4_exec_report_2026-08-22.md`
#: 側にしか残せなかった。以後の実行分は結果 JSON 自身に機械可読で残す）。
_RUNTIME_STACK_PACKAGES: Tuple[str, ...] = ("numpy", "onnxruntime", "soundfile", "PyYAML")


def _runtime_stack() -> Dict[str, Optional[str]]:
    """実行時のパッケージ版 + python 版を返す。`onnxruntime` は render 時にしか
    要らない任意依存のため、未導入でも例外にせず `None` を記録する（measure 側
    には元々不要な依存だが、render/measure 双方で同一関数を使い語彙を揃える）。
    `PyYAML` はディストリビューション名（import 名は `yaml`）。"""
    out: Dict[str, Optional[str]] = {"python": platform.python_version()}
    for pkg in _RUNTIME_STACK_PACKAGES:
        try:
            out[pkg] = _md.version(pkg)
        except _md.PackageNotFoundError:
            out[pkg] = None
    return out


# --- 出力ガード（D0 器具と同型: 衝突拒否 + atomic write） -------------------


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    """`path` と同一ディレクトリの一時ファイルへ書いてから `os.replace` で
    atomic に置き換える。voice_genesis は src/ の `utils/atomic_io.py` を
    import しない契約（CLAUDE.md）のため、`scripts/check_checkpoint_finite.py`
    の `_atomic_write_text` と同型でローカルに最小実装する。"""
    s7_io.assert_json_finite(doc)
    content = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --- render: s7_0b_probe と同じ契約でセルをレンダ ---------------------------


def cmd_render(args: argparse.Namespace) -> int:
    d4_spec, d4_spec_sha = load_and_verify_d4_spec(expected_sha256=args.spec_sha256)
    group_ids = {g["group_id"] for g in d4_spec["groups"]}
    group_id = f"{args.generation}_{args.speaker}"
    if group_id not in group_ids:
        raise D4SpecMismatch(
            f"{group_id!r} は D4 spec の groups に無い（有効: {sorted(group_ids)}）"
        )

    # 8-0b probe と**同じ**事前登録 + verify（凍結物は読むだけ）。
    probe_spec, probe_spec_sha, _ = s7_io.read_json_with_pin(probe0b.SPEC_PATH)
    # 検証→消費 TOCTOU 閉塞（PR #306 レビュー第8巡の自主検出残件・先回り対応）:
    # `probe0b.run_group()`（実消費）より前に、いま読んだ digest を起動時検証
    # 済みの `pins.cell_definition_source` と再照合する。
    _verify_consumed_cell_definition_digest(d4_spec, probe_spec_sha)
    probe0b.verify_spec(probe_spec)
    if args.speaker not in probe_spec["expansion"]["generations"][args.generation]["speakers"]:
        raise probe0b.ProbeSpecMismatch(
            f"{args.generation} に話者 {args.speaker} は事前登録されていない"
        )

    acoustic_dir, stem = Path(args.acoustic_dir), args.acoustic_stem
    export_binding = xm.verify_export_manifest(
        Path(args.export_manifest),
        generation=str(args.generation),
        artifacts={
            "acoustic_onnx": acoustic_dir / f"{stem}.onnx",
            "acoustic_dsconfig": acoustic_dir / "dsconfig.yaml",
            "acoustic_phonemes_json": acoustic_dir / f"{stem}.phonemes.json",
            "speaker_embed": acoustic_dir / f"{stem}.{args.speaker}.emb",
        },
    )
    print(
        f"| export binding verified: {args.generation} <- ckpt "
        f"{export_binding['source_checkpoint_sha256'][:16]}"
    )
    # 検証→消費 TOCTOU 閉塞・方式 (b)（PR #306 レビュー第8巡 P2）: `xm.
    # verify_export_manifest()` は内部で `load_input_pins()` 経由 `s7_exporter_
    # input_pins.json` を読むが、その digest を返り値に運んでこない（option (a)
    # 不可）。呼び出し直後に再 hash して起動時検証済みの pin と照合する。
    _reverify_data_inputs_after_consumption(
        d4_spec["pins"]["data_inputs"], ["s7_exporter_input_pins"],
        source="xm.verify_export_manifest",
    )

    # `run_group` の測定は凍結済み 1.0 spec のまま呼ぶ（render の契約を 8-0b
    # probe と完全に一致させるため）。D4 が消費するのは WAV / `input_meta` の
    # みで、ここで出た 1.0 測定値は `measure` サブコマンドでは使わない。
    frozen = trf.load_frozen_measurement()
    # 検証→消費 TOCTOU 閉塞（PR #306 レビュー第8巡 P2）: `trf.load_frozen_
    # measurement()` の返り値 `spec_sha256` は実際に読んだ `trf_measurement_
    # spec.json`[1.0] の digest なので方式 (a) で直接照合できる。一方、その
    # 内部で呼ばれる `b1.load_prereg()`（1.0 の B-1 事前登録 3 点）の digest は
    # 返り値に運ばれてこないため、方式 (b)（消費直後の再 hash）で補う。
    _verify_consumed_digests(
        d4_spec["pins"]["data_inputs"], {trf.TRF_SPEC_PATH.name: frozen.spec_sha256},
        source="trf.load_frozen_measurement",
    )
    _reverify_data_inputs_after_consumption(
        d4_spec["pins"]["data_inputs"],
        ["s7_b1_candidate_space_1_0", "s7_b1_calibration_set_1_0", "s7_b1_selection_rule_1_0"],
        source="trf.load_frozen_measurement (内部の b1.load_prereg())",
    )
    gate_synth = probe0b.load_gate_synth()

    out_path = Path(args.result_out)
    # render_manifest は out_path と同階層・stem 由来の名前（PR #306 第4巡 P1）。
    manifest_path = out_path.parent / f"{out_path.stem}_render_manifest.json"
    s7_io.reject_output_collision(
        [out_path, manifest_path, Path(args.out_dir)],
        [
            SPEC_PATH, probe0b.SPEC_PATH, trf.TRF_SPEC_PATH, Path(args.export_manifest),
            xm.INPUT_PINS_PATH, probe0b.GATE_SYNTH_PATH,
            acoustic_dir / f"{stem}.onnx", acoustic_dir / "dsconfig.yaml",
            acoustic_dir / f"{stem}.phonemes.json", acoustic_dir / f"{stem}.{args.speaker}.emb",
            Path(args.canon_model_dir), Path(args.vocoder_dir), Path(args.canon_phonemes_txt),
        ],
    )
    doc = probe0b.run_group(
        gate_synth, probe_spec, frozen, probe_spec_sha, args.generation, args.speaker,
        acoustic_dir, stem, Path(args.canon_model_dir), Path(args.vocoder_dir),
        Path(args.canon_phonemes_txt), Path(args.out_dir),
    )
    doc["export_binding"] = export_binding
    doc["d4_schema"] = RENDER_SCHEMA
    doc["d4_remeasure_spec_sha256"] = d4_spec_sha
    doc["d4_remeasure_spec_path"] = str(SPEC_PATH.relative_to(_REPO_ROOT))
    doc["runtime_stack"] = _runtime_stack()
    _atomic_write_json(out_path, doc)
    # render_manifest.json（PR #306 第4巡 P1・方式 a）: 群 doc の実バイト
    # sha256 を group_id 単位で記録する。measure 側はこれを --render-manifest
    # で読み、--render-doc の実バイトと照合する（doc 全体の digest 束縛 =
    # 窓値だけを個別に整合させた改竄を含む doc 全体の改変を一括検出する）。
    manifest_doc = {
        "schema": RENDER_MANIFEST_SCHEMA,
        "groups": {
            group_id: {
                "render_doc_sha256": _sha_file(out_path),
                "path": out_path.name,
            },
        },
    }
    _atomic_write_json(manifest_path, manifest_doc)
    print(
        f"| {args.generation}/{args.speaker}: {doc['n_rendered']} rendered / "
        f"{doc['n_dropped']} dropped -> {out_path} (manifest: {manifest_path})"
    )
    return 0


# --- measure: WAV 群へ 1.2 の voicing 3 軸を適用 -----------------------------


def _measure_cell_axes(
    stim: "b1.Stimulus", axis_candidates: Dict[str, "v12.Cand12"],
) -> Dict[str, float]:
    """1 セルぶんの 3 軸を測る。`cache` は**このセル専用**（呼び出し側が毎セル
    新しい dict を渡す）— 目的は同一セル内で複数軸が同じ candidate_id（例:
    excess_tail_voiced_ms と tail_f0_persistence が同じ hop10 候補を使う）を
    共有するときの重複計算を避けることだけであり、セルをまたいで使い回すと
    2 セル目以降が 1 セル目の軸値を再利用する致命バグになる（セルフレビュー
    #1・実音源で再現: 300ms voiced tail のセル A の値が 0.9s カットのセル B に
    そのまま記帳された）。"""
    cache: Dict[str, Dict[str, float]] = {}
    out: Dict[str, float] = {}
    for axis, cand in axis_candidates.items():
        if cand.candidate_id not in cache:
            cache[cand.candidate_id] = v12.measure_candidate_12(cand, stim)
        out[axis] = cache[cand.candidate_id][axis]
    return out


def _verify_group_doc_registration(
    doc: Dict[str, Any], render_doc_path: Path,
    valid_group_ids: FrozenSet[str], expected_cell_ids: FrozenSet[str],
) -> None:
    """render 群 JSON が D4 spec の事前登録内容と一致することを検査する。

    `d4_remeasure_spec_sha256` が None（= D4 render を経由しない生の 8-0b
    probe 群 JSON をそのまま `--render-doc` に渡した場合）でも**必ず**この
    検査を通す — spec 束縛の有無で緩めると、由来不明の群 JSON がすり抜ける。
    """
    generation, speaker = str(doc.get("generation")), str(doc.get("speaker"))
    group_id = f"{generation}_{speaker}"
    if group_id not in valid_group_ids:
        raise D4SpecMismatch(
            f"{render_doc_path}: 群 {group_id!r} は D4 spec の groups に無い"
            f"（有効: {sorted(valid_group_ids)}）"
        )
    cells = doc.get("cells", [])
    ids = [str(c["cell_id"]) for c in cells]
    if len(ids) != len(expected_cell_ids):
        raise D4SpecMismatch(
            f"{render_doc_path}: セル数 {len(ids)} が事前登録の規定 "
            f"{len(expected_cell_ids)} と違う"
        )
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise D4SpecMismatch(f"{render_doc_path}: 重複した cell_id がある: {dupes}")
    got = set(ids)
    if got != expected_cell_ids:
        raise D4SpecMismatch(
            f"{render_doc_path}: cell_id 集合が事前登録と違う "
            f"(missing={sorted(expected_cell_ids - got)}, extra={sorted(got - expected_cell_ids)})"
        )


def _measure_group(
    render_doc_path: Path, d4_spec_sha: str, axis_candidates: Dict[str, "v12.Cand12"],
    valid_group_ids: FrozenSet[str], expected_cell_ids: FrozenSet[str],
    window_expectations: Dict[str, Tuple[float, float]], expected_tail_window_ms: float,
    manifest_shas: Dict[str, str],
) -> Dict[str, Any]:
    doc, doc_sha, _ = s7_io.read_json_with_pin(render_doc_path)
    # render_manifest digest 束縛（PR #306 第4巡 P1・方式 a・主防御）: doc
    # バイト**全体**を group_id 単位で照合する。個別の窓値検証（3巡・下記の
    # `_verify_cell_window`）より前に検査する — byte digest が一致すれば
    # 窓値もその他フィールドも自動的に一致するため、こちらが先に落ちるのが
    # 自然な順序（多層防御: manifest が主防御・窓値検証はその下の層）。
    manifest_group_id = f"{doc.get('generation')}_{doc.get('speaker')}"
    manifest_sha = manifest_shas.get(manifest_group_id)
    if manifest_sha is None:
        raise D4SpecMismatch(
            f"{render_doc_path}: 群 {manifest_group_id!r} が --render-manifest に"
            "記載されていない（render_manifest.json に無い doc は測らない）"
        )
    if manifest_sha != doc_sha:
        raise D4SpecMismatch(
            f"{render_doc_path}: 実 sha256 {doc_sha} が --render-manifest の記載 "
            f"{manifest_sha} と違う（render 後に doc が改変された疑い）"
        )
    _verify_group_doc_registration(doc, render_doc_path, valid_group_ids, expected_cell_ids)
    bound_sha = doc.get("d4_remeasure_spec_sha256")
    if bound_sha is not None and bound_sha != d4_spec_sha:
        raise D4SpecMismatch(
            f"{render_doc_path}: レンダ時の D4 spec sha {bound_sha} が"
            f"現在の {d4_spec_sha} と違う"
        )
    generation, speaker = str(doc["generation"]), str(doc["speaker"])
    out_dir = Path(str(doc["out_dir"]))

    cells_out: Dict[str, Any] = {}
    n_measured = n_missing = n_error = 0
    for cell in doc["cells"]:
        cid = str(cell["cell_id"])
        if cell.get("outcome") != "rendered":
            cells_out[cid] = {
                "outcome": "missing",
                "reason": cell.get("status", "not_rendered"),
                "error": cell.get("error"),
            }
            n_missing += 1
            continue
        try:
            wav_path = s7_io.child_path(out_dir, str(cell["wav"]))
            y, sr = s7_io.read_wav_with_pins(
                wav_path, cell.get("wav_sha256"), cell.get("samples_sha256")
            )
            meta = cell["input_meta"]
            # 測定窓の pinned 定義束縛（PR #306 レビュー第3巡 P1）: doc 編集で
            # input_meta の窓値だけを差し替える偽測定経路を閉塞する。
            _verify_cell_window(cid, meta, window_expectations.get(cid), expected_tail_window_ms)
            stim = b1.Stimulus(
                stim_id=cid, family=str(cell.get("probe", "")), samples=y, sr=sr,
                note_onset_s=float(meta["note_onset_s"]),
                commanded_note_end_s=float(meta["commanded_note_end_s"]),
                score_boundary_s=float(meta["score_boundary_s"]),
                tail_window_ms=float(meta["tail_window_ms"]),
            )
            axes = _measure_cell_axes(stim, axis_candidates)
        except D4WindowMismatch as exc:
            cells_out[cid] = {
                "outcome": "error", "status": "window_mismatch",
                "reason": "window_mismatch",
                "error": str(exc),
            }
            n_error += 1
            continue
        except Exception as exc:  # noqa: BLE001 — fail-closed: 隔離して全セル記帳を続ける
            # KeyboardInterrupt / SystemExit は Exception のサブクラスではない
            # ため、ここでは捕まらず素通りする（意図どおり）。
            cells_out[cid] = {
                "outcome": "error", "status": "error",
                "reason": type(exc).__name__,
                "error": f"{type(exc).__name__}: {exc}",
            }
            n_error += 1
            continue
        cells_out[cid] = {
            "outcome": "measured",
            "wav_sha256": cell["wav_sha256"],
            "samples_sha256": cell["samples_sha256"],
            "axes": axes,
        }
        n_measured += 1

    # render doc 側の runtime_stack（render 実行時のパッケージ版）を群結果へ
    # 逐語転記する（PR #306 レビュー第2巡 P2）。measure 側の runtime_stack
    # （`d4_results.json` トップレベル・cmd_measure が付す）とは**別環境の
    # provenance**なので混同せず両方残す — render は CPU onnxruntime、
    # measure は ANALYSIS_STACK_PIN 側の別プロセスで走ることが多く、両者の
    # パッケージ版が食い違いうる。PR #306 第1巡以前に render された旧形式の
    # 群 JSON は `runtime_stack` を持たないため、その場合は null + 注記を残す
    # （欠落を「未計測」ではなく「旧形式だから無い」と区別できるようにする）。
    render_runtime_stack = doc.get("runtime_stack")
    render_runtime_stack_note = (
        None if render_runtime_stack is not None else
        "render 済み群 JSON に runtime_stack が無い（PR #306 第1巡以前の render "
        "出力 = 旧形式）。render 実行時のパッケージ版は記帳されていない。"
    )

    return {
        "generation": generation, "speaker": speaker,
        "render_doc_path": str(render_doc_path),
        "render_doc_sha256": doc_sha,
        "n_cells": len(doc["cells"]),
        "n_measured": n_measured, "n_missing": n_missing, "n_error": n_error,
        "materials_sha256": {
            "model_sha256": doc.get("model_sha256"),
            "aux_sha256": doc.get("aux_sha256"),
            "export_binding": doc.get("export_binding"),
        },
        "render_runtime_stack": render_runtime_stack,
        "render_runtime_stack_note": render_runtime_stack_note,
        "cells": cells_out,
    }


def cmd_measure(args: argparse.Namespace) -> int:
    d4_spec, d4_spec_sha = load_and_verify_d4_spec(expected_sha256=args.spec_sha256)
    axis_candidates = _resolve_axis_candidates(d4_spec)

    valid_group_ids = frozenset(g["group_id"] for g in d4_spec["groups"])
    cell_def = _load_cell_definition(d4_spec)
    expected_cell_ids = frozenset(str(c["cell_id"]) for c in cell_def["cells"])
    window_expectations, expected_tail_window_ms = _load_window_expectations(d4_spec)

    out_path = Path(args.out)
    render_docs = [Path(p) for p in args.render_doc]
    manifest_paths = [Path(p) for p in args.render_manifest]
    manifest_expected_shas = list(args.render_manifest_sha256)
    if len(manifest_paths) != len(manifest_expected_shas):
        raise D4SpecMismatch(
            f"--render-manifest {len(manifest_paths)} 件と --render-manifest-sha256 "
            f"{len(manifest_expected_shas)} 件の数が違う（1 対 1・同順で対応させる）"
        )
    # 出力衝突検査は analysis stack 検証より**前**に行う（PR #306 レビュー指摘:
    # 衝突だけを検査したい呼び出しが、無関係な ANALYSIS_STACK_PIN 不一致
    # （実行環境のパッケージ版）で先に落ちていた。凍結物破壊の防止は最優先の
    # fail-closed であり、環境のパッケージ版検査より先に評価するのが正しい
    # 順序 — analysis stack 検証自体は緩めない・そのまま維持する）。
    s7_io.reject_output_collision([out_path], [SPEC_PATH, *render_docs, *manifest_paths])

    # render_manifest 読み込み（PR #306 第4巡 P1・方式 a、第5巡 P1・信頼アンカー
    # 化）: `--render-manifest` は必須・複数指定可（render は 1 群 = 1 呼び出しの
    # ため、通常は群の数だけ渡す）。まず manifest ファイル自体の実バイト sha256 を
    # `--render-manifest-sha256`（operator 供給の期待値・同順対応）と照合する —
    # これが doc-vs-manifest 照合（`_measure_group` 内）より**前**に行う信頼の根
    # （`--spec-sha256` と同型）。通過後に group_id 単位で digest を集約する
    # （重複 group_id は abort）。
    manifest_shas: Dict[str, str] = {}
    for manifest_path, expected_manifest_sha in zip(manifest_paths, manifest_expected_shas):
        manifest_doc, manifest_sha, _ = s7_io.read_json_with_pin(manifest_path)
        if manifest_sha != expected_manifest_sha:
            raise D4SpecMismatch(
                f"{manifest_path}: 実 sha256 {manifest_sha} が --render-manifest-sha256 "
                f"の指定値 {expected_manifest_sha!r} と違う（manifest 自体の信頼の根は "
                "operator 供給 sha — render 完了後の manifest 差し替えを検出する。"
                "doc-vs-manifest 照合より前に abort する）"
            )
        if manifest_doc.get("schema") != RENDER_MANIFEST_SCHEMA:
            raise D4SpecMismatch(
                f"{manifest_path}: schema {manifest_doc.get('schema')!r} != "
                f"{RENDER_MANIFEST_SCHEMA!r}"
            )
        for gid, entry in (manifest_doc.get("groups") or {}).items():
            if gid in manifest_shas:
                raise D4SpecMismatch(
                    f"群 {gid!r} が複数の --render-manifest に重複して現れている"
                )
            manifest_shas[str(gid)] = str(entry["render_doc_sha256"])

    # 検証→消費 TOCTOU 閉塞・方式 (a)（PR #306 レビュー第8巡 P2）: `b1.load_prereg()`
    # が返す `Prereg.pins`（実際に読んだ 1.0 事前登録 3 点の digest）を、起動時に
    # 検証済みの `pins.data_inputs` と再照合する。
    _prereg = b1.load_prereg()
    _verify_consumed_digests(
        d4_spec["pins"]["data_inputs"], _prereg.pins, source="b1.load_prereg",
    )
    analysis_stack = b1.verify_analysis_stack(_prereg)

    groups: Dict[str, Any] = {}
    for render_doc_path in render_docs:
        group_doc = _measure_group(
            render_doc_path, d4_spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            window_expectations, expected_tail_window_ms, manifest_shas,
        )
        group_id = f"{group_doc['generation']}_{group_doc['speaker']}"
        if group_id in groups:
            raise D4SpecMismatch(f"群 {group_id!r} が複数の --render-doc に現れている")
        groups[group_id] = group_doc

    n_total_cells = sum(g["n_cells"] for g in groups.values())
    n_total_measured = sum(g["n_measured"] for g in groups.values())
    n_total_error = sum(g["n_error"] for g in groups.values())

    # 完全性検査（PR #306 レビュー指摘 #4）: 事前登録された 10 群すべてが
    # `--render-doc` に揃っており、かつ全群が「規定セル数ぶん measured
    # （missing=0・error=0）」であることを既定の「完了」条件にする。満たさない
    # 場合も結果は書き切る（failure_policy の「隔離して全セル記帳する」は
    # 維持する）が、`complete: false` を明記して exit を非ゼロにし、部分実行が
    # 「静かな成功」に見えることを防ぐ。`--allow-partial` は abort/書き込み拒否
    # を解除する意味は持たない（既定でも書き切る）— 意図的な部分実行であることを
    # `partial: true` で記帳するためのものであり、exit は不完全なら常に非ゼロ。
    groups_complete = frozenset(groups) == valid_group_ids
    cells_complete = all(
        g["n_measured"] == g["n_cells"] and g["n_missing"] == 0 and g["n_error"] == 0
        for g in groups.values()
    )
    complete = groups_complete and cells_complete
    allow_partial = bool(getattr(args, "allow_partial", False))

    doc = {
        "schema": RESULTS_SCHEMA,
        "debt_ref": DEBT_REF,
        "generated_by": "voice_genesis/foundry/debt/d4/d4_runner.py",
        "d4_remeasure_spec_sha256": d4_spec_sha,
        "d4_remeasure_spec_path": str(SPEC_PATH.relative_to(_REPO_ROOT)),
        "trf_measurement_spec_1_2_sha256": d4_spec["pins"]["trf_measurement_spec_1_2_sha256"],
        "instrument_sha256": d4_spec["pins"]["instrument_sha256"],
        "candidate_ids": {axis: cand.candidate_id for axis, cand in axis_candidates.items()},
        "analysis_stack": analysis_stack,
        "runtime_stack": _runtime_stack(),
        "n_groups": len(groups),
        "n_total_cells": n_total_cells,
        "n_total_measured": n_total_measured,
        "n_total_error": n_total_error,
        "complete": complete,
        "groups": groups,
    }
    if allow_partial:
        doc["partial"] = not complete
    # failure_policy「落ちたセルは隔離して全セル記帳する」の後半 = 結果は必ず
    # 書き切る。ただしエラーが 1 件でも、群/セルが事前登録の規定数に満たなくても
    # 「静かな成功」を騙らせないため exit を非ゼロにする（呼び出し側 / CI が
    # エラー混入・部分実行を見落とさない）。
    _atomic_write_json(out_path, doc)
    status = "complete" if complete else ("partial (--allow-partial)" if allow_partial else "INCOMPLETE")
    print(
        f"| wrote {out_path} ({len(groups)} groups, "
        f"{n_total_measured}/{n_total_cells} cells measured, "
        f"{n_total_error} errored, {status})"
    )
    return 0 if complete else 1


# --- CLI ---------------------------------------------------------------


def _add_render_parser(sub: "argparse._SubParsersAction") -> None:
    ap = sub.add_parser(
        "render", help="s7_0b_probe.py と同じ契約で 1 群（話者 x 世代）をレンダする"
    )
    ap.add_argument("--generation", required=True, choices=["run5", "run6", "run7"])
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--acoustic-dir", required=True)
    ap.add_argument("--acoustic-stem", required=True)
    ap.add_argument("--export-manifest", required=True)
    ap.add_argument("--canon-model-dir", required=True)
    ap.add_argument("--vocoder-dir", required=True)
    ap.add_argument("--canon-phonemes-txt", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--result-out", required=True)
    ap.add_argument(
        "--spec-sha256", required=True,
        help="operator が束縛する d4_remeasure_spec.json の期待 sha256（コミット済みの"
             "はずの版）。実ファイルの sha256 と一致しなければ abort する",
    )
    ap.set_defaults(func=cmd_render)


def _add_measure_parser(sub: "argparse._SubParsersAction") -> None:
    ap = sub.add_parser(
        "measure", help="render 済み群 JSON へ 1.2 の voicing 3 軸を適用する"
    )
    ap.add_argument(
        "--render-doc", action="append", required=True, dest="render_doc",
        help="render サブコマンド（または s7_0b_probe.py）が書いた群 JSON。複数指定可",
    )
    ap.add_argument(
        "--render-manifest", action="append", required=True, dest="render_manifest",
        help="render サブコマンドが書いた render_manifest.json（group_id ごとの doc "
             "sha256 台帳）。複数指定可・必須。各 --render-doc の実バイト sha256 を "
             "ここに記載された値と group_id 単位で照合し、不一致・未記載は abort する"
             "（PR #306 第4巡 P1・doc 全体の digest 束縛が主防御）",
    )
    ap.add_argument(
        "--render-manifest-sha256", action="append", required=True,
        dest="render_manifest_sha256",
        help="operator が束縛する各 --render-manifest の期待 sha256（同順で1対1に"
             "対応）。実ファイルの sha256 と一致しなければ abort する — manifest 自体"
             "が render 完了後に差し替えられていないかを doc 照合より前に検査する"
             "信頼の根（--spec-sha256 と同型。PR #306 第5巡 P1）",
    )
    ap.add_argument("--out", required=True, help="d4_results.json の出力先")
    ap.add_argument(
        "--spec-sha256", required=True,
        help="operator が束縛する d4_remeasure_spec.json の期待 sha256（コミット済みの"
             "はずの版）。実ファイルの sha256 と一致しなければ abort する",
    )
    ap.add_argument(
        "--allow-partial", action="store_true", dest="allow_partial",
        help="事前登録の10群・規定セル数に満たない部分実行を明示的に許可する意図を"
             "結果へ記帳する（`partial: true`）。既定でも部分実行は abort せず結果を"
             "書き切るため exit の非ゼロ化は変わらない — 意図的な部分実行であることの"
             "記帳だけが違う",
    )
    ap.set_defaults(func=cmd_measure)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    _add_render_parser(sub)
    _add_measure_parser(sub)
    args = ap.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
