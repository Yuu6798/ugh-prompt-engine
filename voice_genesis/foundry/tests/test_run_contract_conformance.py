"""test_run_contract_conformance.py — Run Contract 機械層の fail-closed 検査
（Phase D0・DEBT_ADJUDICATION_v1.1.md 裁定4。2026-08-22 セルフレビュー修正5/6/7/8
+ Codex bot レビュー #7 で改訂: gate の pre_run/post_run 分離・PINNED フィールド
列の schema 派生化・sha256 改竄検出テストの実体化・schema 制約の明示検査）。

境界宣言: 本ファイルが照合するのは projection に記録された sha256 の**形式**
（hex64 pattern）および design_doc の実バイトとの一致のみ。dataset/checkpoint
実体（repo に非同梱）の sha256 と projection 値との一致は実行環境側の pin
照合ツール（例: `scripts/check_checkpoint_finite.py --pins`）の責務であり、
本テストの対象外。

`debt/RUN_CONTRACT_SCHEMA_v1.json` が定義する projection 形状に対し、
`debt/*.contract_projection.json`（glob 発見・pytest parametrize。Codex
第10巡 10-2 — Run 8 固定パスから一般化。将来 run の projection ファイルにも
本ファイルの全検査が自動的に適用される）が実際に fail-closed で振る舞う
ことを検査する。検査ロジックは schema の意味論を Python で再実装した
もの（外部 jsonschema ライブラリは前提にしない = schema 自身のコメント
方針）。

CLOSED 状態への改竄を前提とする negative test 群（closeout_ref 系）は、
汎用 parametrize された `projection` フィクスチャ（将来 gate=OPEN/BLOCKED
の projection も含みうる）とは独立して、実際に CLOSED を宣言している
Run 8 の projection に固定する専用 `run8_projection` フィクスチャを使う
（Codex 第10巡 10-2: Run 8 固有テストとして明示的に分離する方式を選択 —
「そのプロジェクションが CLOSED を宣言している場合の条件付き検査」への
一般化と二択だったが、tampering 系は「CLOSED を騙る」ことが検査の前提
そのものであり、非 CLOSED の future projection に対して意味のある negative
test にならないため分離を選んだ。CLOSED 宣言時の正常系検証
`test_closeout_ref_exists_when_gate_is_closed` は逆に全 projection へ
一般化している — `_check_closeout_ref` 自身が CLOSED でなければ no-op する
ため、条件付き検査として素直に成立する）。

検査内容:
(a) projection の design_doc_sha256 が実ファイルの sha256 と一致
(b) schema 必須フィールドが projection に全て存在
(c) single_intervention.count == 1（かつ intervention が実質宣言されている
    こと。Codex 第3巡 3-1）
(d) runbook_may_override_design == false
(e) main_training_gate == "CLOSED" は closeout 記録による終端宣言として
    sticky（pre_run/post_run の充足状況に関わらず優先し、pin の充足で
    OPEN/BLOCKED へ自動的に戻らない。CLOSED でないとき、pre_run フィールド
    が1つでも未 PINNED（または post_run フィールドが BLOCKED）であれば
    main_training_gate == "BLOCKED"（Codex 第6巡 6-2）
(f) main_training_gate == "OPEN" は 全 pre_run フィールドが実質 PINNED
    （status PINNED かつ value/source が非 null）かつ 全 post_run フィールドが
    BLOCKED でない（PENDING までは許容）ときのみ許される
    （現状データは main_training_gate == "CLOSED"。closeout 記録による
    終端宣言で、pre_run にはなお BLOCKED/PENDING が残る）
(g) pinned_field 形 object / single_intervention の許容キー外のフィールドが
    無いこと、design_doc_sha256 の hex64 pattern、claim_strength_target.value
    の禁止記号（"C0"〜"C3" 等）非混入、main_training_gate の許容値
    （{"OPEN", "BLOCKED", "CLOSED"}）
(h) RUN_CONTRACT_SCHEMA_v1.json を再帰的に歩く汎用バリデータで projection
    全体を一括検証する（Codex 第5巡採用 P2: (a)-(g) の個別スポットチェック
    の継ぎ足しを止め、schema 制約の全数強制へファミリー終端。§3-4）
(i) main_training_gate == "CLOSED" のとき closeout_ref が repo 内の実在
    ファイルを指すこと。CLOSED は pin 充足の合成 projection でも sticky
    であること（Codex 第6巡 6-2）

Codex 第5巡見送り（P1: pre-run pin の実 artifact 照合）— 境界宣言:
projection は宣言の repo 側形状検査に留める。pin と実際に消費される
artifact（checkpoint 実体等）の束縛は実行時 bootstrap の fail-closed の
責務（run5-7 の PIN_COMMIT/manifest 照合で実装済みの既存分界。run8 は
PR-1/PR-3 で同配線）。conformance test 領域はレビュー3巡目につき本節で
終端する。

Codex 第6巡（2026-08-22）データ正確性訂正: 別セッションの PR #303 により
Run 8 は実行済みで `results_s7/` に成果が正典化された
（`s7_run8_closeout.md`: Gate 1 = UNDETERMINED / Run 8 = CLOSED /
B-1・B-2 は境界つきで凍結済み）。本 conformance test はこの正典に合わせて
main_training_gate の終端状態 CLOSED を追加した（schema 側の変更は
`RUN_CONTRACT_SCHEMA_v1.json`、projection 側の変更は
`DESIGN_S7_run8.contract_projection.json` を参照）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

DEBT_DIR = Path(__file__).resolve().parent.parent / "debt"
SCHEMA_PATH = DEBT_DIR / "RUN_CONTRACT_SCHEMA_v1.json"
RUN8_PROJECTION_PATH = DEBT_DIR / "DESIGN_S7_run8.contract_projection.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _discover_projection_paths() -> List[Path]:
    """`debt/` 配下の `*.contract_projection.json` 全件を発見する
    （Codex 第10巡 10-2: Run 8 固定パスから一般化）。"""
    return sorted(DEBT_DIR.glob("*.contract_projection.json"))


# collection 時点で1回だけ発見する（pytest.fixture の params は collection
# 時点で確定している必要があるため、モジュールレベルで評価する）。
PROJECTION_PATHS: List[Path] = _discover_projection_paths()


def test_discovers_at_least_one_contract_projection_file() -> None:
    """(a) Codex 第10巡 10-2: 発見 0 件は fail する（suite の空洞化防止）。
    `projection` フィクスチャは `PROJECTION_PATHS` へ parametrize される
    ため、discovery が 0 件だと依存する全テストが「fail」でも「skip」でも
    ない「黙って 0 個収集される」状態になる。この canary は parametrize に
    依存せず discovery 件数を直接検査することで、そのサイレントな空洞化を
    確実に検出する。"""
    assert PROJECTION_PATHS, (
        "voice_genesis/foundry/debt/*.contract_projection.json が1件も"
        f"見つかりません: {DEBT_DIR}"
    )


@pytest.fixture(scope="module")
def schema() -> Dict[str, Any]:
    assert SCHEMA_PATH.exists(), f"schema not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=PROJECTION_PATHS, ids=lambda p: p.name)
def projection_path(request: pytest.FixtureRequest) -> Path:
    return request.param


@pytest.fixture(scope="module")
def projection(projection_path: Path) -> Dict[str, Any]:
    """発見された `*.contract_projection.json` 全件へ parametrize される
    （Codex 第10巡 10-2）。このフィクスチャに依存するテストは各 projection
    ファイルごとに1回ずつ実行される。"""
    assert projection_path.exists(), f"projection not found: {projection_path}"
    return json.loads(projection_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def run8_projection() -> Dict[str, Any]:
    """Run 8 固有テスト専用（Codex 第10巡 10-2）。CLOSED 状態への改竄を
    前提とする negative test（closeout_ref 系）は、実際に CLOSED を宣言
    している Run 8 の projection に固定して実行する（汎用 parametrize
    された `projection` は将来 gate=OPEN/BLOCKED の projection も含み
    うるため、それらに対しては「CLOSED を騙る」検査自体が意味を持たない）。
    """
    assert RUN8_PROJECTION_PATH.exists(), f"projection not found: {RUN8_PROJECTION_PATH}"
    return json.loads(RUN8_PROJECTION_PATH.read_text(encoding="utf-8"))


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --- schema 派生ヘルパー（修正6: PINNED_FIELD_NAMES 手書きリストの廃止） -----
#
# 「pinned_field 形（value/source/status を持つ object）のフィールド一覧」と
# 「x-gate-class（pre_run/post_run）ごとのフィールド一覧」を、どちらも schema
# の properties から動的に導出する。schema にフィールドが増減すれば、この
# ファイルのコードを一切変更せずに以後のテストが自動追随する。


def _resolve_property_schema(schema: Dict[str, Any], prop_schema: Dict[str, Any]) -> Dict[str, Any]:
    """properties[name] の schema を返す。$ref があれば definitions から解決する
    （x-gate-class 等のフィールド固有キーは $ref の兄弟キーとして properties[name]
    自体に付与されているため、呼び出し側は解決前の prop_schema も参照できる）。"""
    ref = prop_schema.get("$ref")
    if ref is None:
        return prop_schema
    assert ref.startswith("#/definitions/"), f"unsupported $ref target: {ref}"
    return schema["definitions"][ref.split("/")[-1]]


def _pinned_field_names(schema: Dict[str, Any]) -> List[str]:
    """{value, source, status} を required に持つ object 型フィールド名一覧
    （$ref 経由の pinned_field 群 + インライン定義の claim_strength_target）。"""
    names: List[str] = []
    for name, prop_schema in schema["properties"].items():
        resolved = _resolve_property_schema(schema, prop_schema)
        if set(resolved.get("required", [])) == {"value", "source", "status"}:
            names.append(name)
    return names


def _gate_class_field_names(schema: Dict[str, Any], gate_class: str) -> List[str]:
    """x-gate-class == gate_class （"pre_run" / "post_run"）が付与された
    フィールド名一覧を properties から動的に導出する。"""
    return [
        name
        for name, prop_schema in schema["properties"].items()
        if prop_schema.get("x-gate-class") == gate_class
    ]


def _sha256_named_field_names(schema: Dict[str, Any]) -> List[str]:
    """フィールド名が '_sha256' で終わる schema properties 名一覧
    （design_doc_sha256 のような単純 string 欄と、dataset_manifest_sha256 等の
    pinned_field 型欄の両方を含む。Codex bot レビュー #7）。"""
    return [name for name in schema["properties"] if name.endswith("_sha256")]


def _is_meaningfully_present(value: Any) -> bool:
    """value が None でなく、文字列の場合は strip 後非空であることを要求する
    （Codex 第9巡 9-1: 空文字・空白のみの value/source は「値が入っている」を
    名乗るだけで実質は未記入と同じであり、null 検査だけでは通ってしまって
    いた述語を完結させる）。非文字列の正当な falsey 値（0 / False / [] 等）は
    None チェックのみで足り、strip 判定の対象外。"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _is_effectively_pinned(field: Any) -> bool:
    """status が "PINNED" を名乗っていても value/source が null、または
    空文字・空白のみの文字列なら実質未 PIN として扱う（Codex bot レビュー #7
    「PINNED の実質検証」+ Codex 第9巡 9-1 で文字列の空/空白判定を追加）。
    gate 判定は status 文字列だけでなくこの関数の結果を使う。"""
    if not isinstance(field, dict):
        return False
    return (
        field.get("status") == "PINNED"
        and _is_meaningfully_present(field.get("value"))
        and _is_meaningfully_present(field.get("source"))
    )


def _is_single_intervention_substantively_declared(field: Any) -> bool:
    """single_intervention の実質検証（Codex 第3巡 3-1）: `declared: true` を
    名乗るだけで `intervention` が null/空文字/欠落なら、`_is_effectively_pinned`
    が PINNED を名乗るだけで value/source が null なフィールドを未 PIN 扱いに
    するのと同じ理由で、実質未宣言として扱う。"""
    if not isinstance(field, dict):
        return False
    if field.get("declared") is not True or field.get("count") != 1:
        return False
    intervention = field.get("intervention")
    return isinstance(intervention, str) and intervention.strip() != ""


def _is_pre_run_field_satisfied(name: str, field: Any) -> bool:
    """pre_run フィールドが gate OPEN の要件を満たすか判定する。
    single_intervention のみ {value,source,status} 形でなく
    {declared,intervention,count} 形なので個別分岐する。"""
    if not isinstance(field, dict):
        return False
    if name == "single_intervention":
        return _is_single_intervention_substantively_declared(field)
    return _is_effectively_pinned(field)


def _unsatisfied_pre_run_fields(schema: Dict[str, Any], projection: Dict[str, Any]) -> List[str]:
    unsatisfied: List[str] = []
    for name in _gate_class_field_names(schema, "pre_run"):
        field = projection.get(name)
        if not _is_pre_run_field_satisfied(name, field):
            unsatisfied.append(name)
    return unsatisfied


def _blocked_post_run_fields(schema: Dict[str, Any], projection: Dict[str, Any]) -> List[str]:
    """post_run フィールドは PENDING までは許容するが、欠落 or status ==
    BLOCKED は gate OPEN を妨げる（run record closure 側の要件）。"""
    blocked: List[str] = []
    for name in _gate_class_field_names(schema, "post_run"):
        field = projection.get(name)
        if not isinstance(field, dict) or field.get("status") not in ("PINNED", "PENDING"):
            blocked.append(name)
    return blocked


def _expected_gate(schema: Dict[str, Any], projection: Dict[str, Any]) -> str:
    """schema の x-gate-class 意味論を素直に実装した参照ロジック:
    main_training_gate が既に "CLOSED"（closeout 記録による終端宣言）なら、
    pre_run/post_run の充足状況に関わらず CLOSED を返す（sticky。Codex 第6巡
    6-2: pin の充足によって OPEN/BLOCKED へ自動的に戻ることはない — CLOSED
    を解除できるのは closeout 記録を書き換える User 裁定のみで、この参照
    ロジックの管轄外）。CLOSED でなければ、全 pre_run フィールドが実質
    PINNED、かつ全 post_run フィールドが BLOCKED でなければ OPEN、それ
    以外は BLOCKED。"""
    if projection.get("main_training_gate") == "CLOSED":
        return "CLOSED"
    if _unsatisfied_pre_run_fields(schema, projection) or _blocked_post_run_fields(schema, projection):
        return "BLOCKED"
    return "OPEN"


# --- (a) design_doc_sha256 が実ファイルと一致 ---------------------------


def _check_design_doc_sha256(projection: Dict[str, Any], repo_root: Path) -> None:
    """design_doc_sha256 と実ファイル sha256 の照合ロジック本体。
    `test_design_doc_sha256_matches_actual_file` /
    `test_design_doc_sha256_mismatch_is_detected` の両方から呼ばれる共有関数
    （修正7: テスト自体を空虚にしない — 実際にこの関数へ食わせて検査する）。

    Codex 第2巡 P2: `projection["design_doc"]` は信頼できない文字列である
    ため、repo 外ファイルを読み込ませる経路（絶対パス指定 / `..` による
    親ディレクトリ越境）を読み込み前に拒否する。resolve 後のパスが
    `repo_root` 配下であることも検証してから hash する。
    """
    design_doc_rel = projection["design_doc"]
    design_doc_rel_path = Path(design_doc_rel)
    assert not design_doc_rel_path.is_absolute(), (
        f"design_doc は repo 相対パスのみ許容します（絶対パスは拒否）: {design_doc_rel!r}"
    )
    assert ".." not in design_doc_rel_path.parts, (
        f"design_doc に親ディレクトリ越境（'..'）は許容しません: {design_doc_rel!r}"
    )

    repo_root_resolved = repo_root.resolve()
    design_doc_path = (repo_root / design_doc_rel_path).resolve()
    assert design_doc_path.is_relative_to(repo_root_resolved), (
        "design_doc の解決後パスが repo_root 配下ではありません: "
        f"{design_doc_path} (repo_root={repo_root_resolved})"
    )

    assert design_doc_path.exists(), f"design_doc not found: {design_doc_path}"
    actual_sha256 = _sha256_of_file(design_doc_path)
    assert actual_sha256 == projection["design_doc_sha256"], (
        "DESIGN 文書が改変されたのに projection の design_doc_sha256 が"
        f"追随していません: actual={actual_sha256} projection={projection['design_doc_sha256']}"
    )


def test_design_doc_sha256_matches_actual_file(projection: Dict[str, Any]) -> None:
    _check_design_doc_sha256(projection, REPO_ROOT)


def test_design_doc_sha256_mismatch_is_detected(projection: Dict[str, Any]) -> None:
    """sha256 照合ロジック自体が改変検出できることを、(a) 実ファイル + 正しい
    sha → pass、(b) 改竄 sha ('0'*64) を入れた projection dict → 照合関数が
    不一致を検出して fail、の両方向を実際に `_check_design_doc_sha256` へ
    食わせて検査する（修正7: 従来は sha を比較せずアサートするだけの空虚な
    テストだった）。"""
    # (a) 正しい projection はそのまま通る。
    _check_design_doc_sha256(projection, REPO_ROOT)

    # (b) design_doc_sha256 を改竄した projection dict は検出されて fail する。
    tampered = dict(projection)
    tampered["design_doc_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _check_design_doc_sha256(tampered, REPO_ROOT)


def test_check_design_doc_sha256_rejects_absolute_path(projection: Dict[str, Any]) -> None:
    """Codex 第2巡 P2 負例: design_doc が絶対パスの projection は repo 外
    参照とみなし読み込み前に即 fail する。"""
    tampered = dict(projection)
    tampered["design_doc"] = "/etc/passwd"
    with pytest.raises(AssertionError):
        _check_design_doc_sha256(tampered, REPO_ROOT)


def test_check_design_doc_sha256_rejects_parent_directory_escape(projection: Dict[str, Any]) -> None:
    """Codex 第2巡 P2 負例: design_doc が '..' で repo_root の外へ越境する
    projection は読み込み前に即 fail する。"""
    tampered = dict(projection)
    tampered["design_doc"] = "../../../../../../../../etc/passwd"
    with pytest.raises(AssertionError):
        _check_design_doc_sha256(tampered, REPO_ROOT)


# --- (b) schema 必須フィールドが projection に全て存在 -------------------


def test_all_schema_required_fields_present_in_projection(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    required = schema["required"]
    missing = [k for k in required if k not in projection]
    assert not missing, f"projection missing required fields: {missing}"


def test_projection_has_no_fields_outside_schema(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    allowed = set(schema["properties"].keys())
    extra = [k for k in projection if k not in allowed]
    assert not extra, f"projection has fields not declared in schema: {extra}"


def test_pinned_field_objects_have_required_subkeys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    for name in _pinned_field_names(schema):
        field = projection[name]
        assert isinstance(field, dict), f"{name} must be an object"
        for subkey in ("value", "source", "status"):
            assert subkey in field, f"{name} missing subkey {subkey!r}"
        assert field["status"] in ("PINNED", "PENDING", "BLOCKED"), (
            f"{name}.status has invalid value {field['status']!r}"
        )


# --- (c) single_intervention.count == 1 ----------------------------------


def test_single_intervention_count_is_one(projection: Dict[str, Any]) -> None:
    single_intervention = projection["single_intervention"]
    assert single_intervention["declared"] is True
    assert single_intervention["count"] == 1


def test_single_intervention_intervention_is_substantively_declared(
    projection: Dict[str, Any],
) -> None:
    """Codex 第3巡 3-1: declared==true を名乗るなら intervention は実際に
    何を単一介入としたかを記述する非空文字列であること。"""
    assert _is_single_intervention_substantively_declared(projection["single_intervention"])


def test_schema_single_intervention_intervention_requires_nonempty_string(
    schema: Dict[str, Any],
) -> None:
    """Codex 第3巡 3-1: schema 側で intervention に type/minLength 制約が
    入っていること。"""
    intervention_schema = schema["properties"]["single_intervention"]["properties"]["intervention"]
    assert intervention_schema.get("type") == "string"
    assert intervention_schema.get("minLength", 0) >= 1


def test_single_intervention_rejects_null_empty_or_missing_intervention() -> None:
    """Codex 第3巡 3-1 負例: declared==true でも intervention が null / 空文字
    （空白のみ含む）/ 欠落なら実質未宣言として拒否される。"""
    base = {"declared": True, "intervention": "some real intervention", "count": 1}
    assert _is_single_intervention_substantively_declared(base)

    null_intervention = dict(base)
    null_intervention["intervention"] = None
    assert not _is_single_intervention_substantively_declared(null_intervention)

    empty_intervention = dict(base)
    empty_intervention["intervention"] = ""
    assert not _is_single_intervention_substantively_declared(empty_intervention)

    whitespace_only_intervention = dict(base)
    whitespace_only_intervention["intervention"] = "   "
    assert not _is_single_intervention_substantively_declared(whitespace_only_intervention)

    missing_intervention = {"declared": True, "count": 1}
    assert not _is_single_intervention_substantively_declared(missing_intervention)


def test_gate_treats_single_intervention_with_empty_intervention_as_unsatisfied() -> None:
    """Codex 第3巡 3-1: gate 判定へ実際に配線されていること
    （declared==true・count==1 だが intervention が空文字の pre_run フィールドは
    gate を BLOCKED にする）。"""
    schema = {
        "properties": {
            "single_intervention": {"x-gate-class": "pre_run"},
        },
        "definitions": {},
    }
    fake_projection = {
        "single_intervention": {"declared": True, "intervention": "", "count": 1},
    }
    assert _expected_gate(schema, fake_projection) == "BLOCKED"
    assert _unsatisfied_pre_run_fields(schema, fake_projection) == ["single_intervention"]


# --- (d) runbook_may_override_design == false -----------------------------


def test_runbook_may_not_override_design(projection: Dict[str, Any]) -> None:
    assert projection["runbook_may_override_design"] is False


# --- (e)/(f) main_training_gate の fail-closed ロジック (pre_run/post_run) -


def test_main_training_gate_is_closed_via_closeout_sticky_override(
    schema: Dict[str, Any], run8_projection: Dict[str, Any]
) -> None:
    """Codex 第6巡 6-2（第10巡 10-2 で run8_projection 専用へ分離 — Run 8
    固有のデータ状態を前提とする検査のため）: 現状データ（run8-B 準備状態）は
    main_training_gate == "CLOSED"（`results_s7/s7_run8_closeout.md` の
    User 裁定による終端宣言）。pre_run/post_run の一部フィールド
    （dataset_manifest_sha256 / config_sha256 / fixed_probe_set 等）は
    依然として未充足のままだが、CLOSED はそれらの状態に関わらず優先される
    （sticky）。もし CLOSED でなければ BLOCKED になっていたはずの未充足
    フィールドが実在することとあわせて確認する（旧
    test_main_training_gate_is_blocked_when_pre_run_or_post_run_unsatisfied
    の後継 — main_training_gate が BLOCKED から CLOSED へ変わったため
    改名・改訂）。"""
    unsatisfied_pre = _unsatisfied_pre_run_fields(schema, run8_projection)
    blocked_post = _blocked_post_run_fields(schema, run8_projection)
    assert unsatisfied_pre or blocked_post, (
        "この検査は『CLOSED でなければ BLOCKED 側を通っていたはず』ことを"
        "実証する目的のため、少なくとも1つの未充足 pre_run または BLOCKED な"
        "post_run フィールドが存在するはずです"
    )
    assert run8_projection["main_training_gate"] == "CLOSED", (
        f"main_training_gate が {run8_projection['main_training_gate']!r} です。"
        "closeout 記録による終端宣言のため CLOSED であるべきです。"
    )


def test_main_training_gate_matches_reference_logic(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    assert projection["main_training_gate"] == _expected_gate(schema, projection)


def test_gate_logic_flags_open_only_when_all_pre_run_pinned_and_post_run_not_blocked() -> None:
    """参照ロジック (_expected_gate) が
    (全 pre_run PINNED かつ 全 post_run 非 BLOCKED) のときのみ OPEN を返す
    ことを、schema の x-gate-class から動的導出した最小の合成 schema/projection
    で検証する（実データに依存しない純粋な論理検査。修正5）。"""
    schema = {
        "properties": {
            "single_intervention": {"x-gate-class": "pre_run"},
            "baseline_run": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "measurement_spec_version": {
                "$ref": "#/definitions/pinned_field",
                "x-gate-class": "pre_run",
            },
            "checkpoint_sha256": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
            "cost_record": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    base_projection = {
        "single_intervention": {"declared": True, "intervention": "x", "count": 1},
        "baseline_run": {"value": "x", "source": "y", "status": "PINNED"},
        "measurement_spec_version": {"value": "x", "source": "y", "status": "PINNED"},
        # post_run は PENDING までなら gate OPEN を妨げない。
        "checkpoint_sha256": {"value": None, "source": None, "status": "PENDING"},
        "cost_record": {"value": None, "source": None, "status": "PENDING"},
    }
    assert _expected_gate(schema, base_projection) == "OPEN"

    # pre_run が1つでも PENDING/BLOCKED なら BLOCKED。
    one_pre_run_pending = json.loads(json.dumps(base_projection))
    one_pre_run_pending["measurement_spec_version"] = {
        "value": None,
        "source": None,
        "status": "PENDING",
    }
    assert _expected_gate(schema, one_pre_run_pending) == "BLOCKED"

    # post_run が BLOCKED なら（pre_run が全 PINNED でも）BLOCKED。
    one_post_run_blocked = json.loads(json.dumps(base_projection))
    one_post_run_blocked["cost_record"] = {"value": None, "source": None, "status": "BLOCKED"}
    assert _expected_gate(schema, one_post_run_blocked) == "BLOCKED"

    # 必須欄の欠落は BLOCKED（pre_run/post_run とも）。
    missing_pre_run = json.loads(json.dumps(base_projection))
    del missing_pre_run["baseline_run"]
    assert _expected_gate(schema, missing_pre_run) == "BLOCKED"

    missing_post_run = json.loads(json.dumps(base_projection))
    del missing_post_run["checkpoint_sha256"]
    assert _expected_gate(schema, missing_post_run) == "BLOCKED"


def test_open_gate_requires_all_pre_run_pinned_and_no_post_run_blocked() -> None:
    """main_training_gate == OPEN を宣言している projection は、
    _unsatisfied_pre_run_fields / _blocked_post_run_fields が両方とも
    空であることを要求する（(f) の直接検査）。"""
    schema = {
        "properties": {
            "baseline_run": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "checkpoint_sha256": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    fake_open_projection = {
        "baseline_run": {"value": "x", "source": "y", "status": "PINNED"},
        "checkpoint_sha256": {"value": None, "source": None, "status": "PENDING"},
        "main_training_gate": "OPEN",
    }
    assert _unsatisfied_pre_run_fields(schema, fake_open_projection) == []
    assert _blocked_post_run_fields(schema, fake_open_projection) == []


def test_gate_closed_is_sticky_even_when_all_pre_run_fields_are_pinned() -> None:
    """Codex 第6巡 6-2(b): main_training_gate が既に "CLOSED"（closeout 記録
    による終端宣言）なら、pre_run 全フィールドを実質 PINNED に書き換えた
    合成 projection でも参照ロジックは CLOSED を返す（pin の充足によって
    OPEN へ自動的に再開しない — sticky）。"""
    schema = {
        "properties": {
            "single_intervention": {"x-gate-class": "pre_run"},
            "baseline_run": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "checkpoint_sha256": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    all_pinned_fields = {
        "single_intervention": {"declared": True, "intervention": "x", "count": 1},
        "baseline_run": {"value": "x", "source": "y", "status": "PINNED"},
        "checkpoint_sha256": {"value": "x", "source": "y", "status": "PINNED"},
    }

    # main_training_gate が CLOSED を宣言している場合は、全 pre_run/post_run
    # が満たされていても CLOSED のまま（sticky）。
    closed_projection = dict(all_pinned_fields)
    closed_projection["main_training_gate"] = "CLOSED"
    assert _expected_gate(schema, closed_projection) == "CLOSED"

    # 比較対象: main_training_gate フィールド自体が存在しない（= CLOSED を
    # 宣言していない）場合、同じ pin 充足状況なら通常どおり OPEN になる
    # （sticky が「pin が全部揃っているから」ではなく「CLOSED を宣言して
    # いるから」で発動していることの対照実験）。
    not_closed_projection = dict(all_pinned_fields)
    assert _expected_gate(schema, not_closed_projection) == "OPEN"


# --- 修正6: 動的導出そのものの単体検査（schema へのフィールド追加が自動追随）--


def test_adding_new_pre_run_schema_field_changes_gate_result_without_code_change() -> None:
    """PINNED_FIELD_NAMES 手書きリストを廃止した効果の実証: schema の
    properties にフィールドを1つ足すだけで、このファイルのコードを一切
    変更せずに _expected_gate の判定が追随することを検査する。"""
    base_schema = {
        "properties": {
            "a": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    projection_missing_new_field = {"a": {"value": "x", "source": "y", "status": "PINNED"}}
    assert _expected_gate(base_schema, projection_missing_new_field) == "OPEN"

    # 架空の新規フィールド "b" を pre_run として schema に追加する。
    schema_with_new_field = json.loads(json.dumps(base_schema))
    schema_with_new_field["properties"]["b"] = {
        "$ref": "#/definitions/pinned_field",
        "x-gate-class": "pre_run",
    }
    # projection 側は "b" を追加していない（= 未 PINNED 扱い）ので BLOCKED に転じる。
    assert _expected_gate(schema_with_new_field, projection_missing_new_field) == "BLOCKED"

    # projection 側も "b" を実質 PINNED で足せば OPEN に戻る。
    projection_with_new_field = dict(projection_missing_new_field)
    projection_with_new_field["b"] = {"value": "x", "source": "y", "status": "PINNED"}
    assert _expected_gate(schema_with_new_field, projection_with_new_field) == "OPEN"


# --- Codex bot レビュー #7: PINNED の実質検証 -------------------------------


def test_effectively_pinned_rejects_pinned_status_with_null_value_or_source() -> None:
    assert not _is_effectively_pinned({"status": "PINNED", "value": None, "source": "x"})
    assert not _is_effectively_pinned({"status": "PINNED", "value": "x", "source": None})
    assert not _is_effectively_pinned({"status": "PINNED", "value": None, "source": None})
    assert _is_effectively_pinned({"status": "PINNED", "value": "x", "source": "y"})
    # PENDING/BLOCKED は value/source の有無に関わらず実質 PIN とは認めない。
    assert not _is_effectively_pinned({"status": "PENDING", "value": "x", "source": "y"})


def test_effectively_pinned_rejects_empty_or_whitespace_only_strings() -> None:
    """Codex 第9巡 9-1 負例: value/source が空文字・空白のみの文字列なら、
    null ではなくても実質未 PIN として拒否される。"""
    assert not _is_effectively_pinned({"status": "PINNED", "value": "", "source": "x"})
    assert not _is_effectively_pinned({"status": "PINNED", "value": "x", "source": "   "})
    assert not _is_effectively_pinned({"status": "PINNED", "value": "\t\n", "source": "x"})
    assert _is_effectively_pinned({"status": "PINNED", "value": " x ", "source": "y"})


def test_is_meaningfully_present_allows_non_string_falsey_values() -> None:
    """Codex 第9巡 9-1: 非文字列の正当な falsey 値（0 / False / [] 等）は
    None チェックのみで足り、strip 判定の対象外として有効に扱われる。"""
    assert _is_meaningfully_present(0)
    assert _is_meaningfully_present(False)
    assert _is_meaningfully_present([])
    assert _is_meaningfully_present({})
    assert not _is_meaningfully_present(None)
    assert not _is_meaningfully_present("")
    assert not _is_meaningfully_present("   ")


def test_gate_treats_pinned_status_with_null_value_as_unpinned() -> None:
    """status: "PINNED" を名乗るだけで value/source が null なフィールドは
    gate 判定上 PINNED として扱わない（実質検証。Codex bot レビュー #7 —
    status 文字列だけを見ていた旧ロジックだと素通りしてしまう欠陥の再現）。"""
    schema = {
        "properties": {
            "a": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    fake_pinned_projection = {"a": {"status": "PINNED", "value": None, "source": None}}
    assert _expected_gate(schema, fake_pinned_projection) == "BLOCKED"
    assert _unsatisfied_pre_run_fields(schema, fake_pinned_projection) == ["a"]


def test_pinned_sha256_named_fields_match_hex64_pattern(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """PINNED 状態の *_sha256 pinned_field は value が ^[0-9a-f]{64}$ に一致する
    こと。design_doc_sha256（常に必須の平文字列欄）も同じ pattern を満たす
    こと（Codex bot レビュー #7）。境界宣言はモジュール docstring 冒頭を参照
    （実バイト照合ではなく形式検査のみ）。"""
    checked_any = False
    for name in _sha256_named_field_names(schema):
        checked_any = True
        if name == "design_doc_sha256":
            assert SHA256_HEX_PATTERN.fullmatch(projection[name]), (
                f"{name} が hex64 pattern に一致しません: {projection[name]!r}"
            )
            continue
        field = projection.get(name)
        if isinstance(field, dict) and field.get("status") == "PINNED":
            assert isinstance(field.get("value"), str) and SHA256_HEX_PATTERN.fullmatch(
                field["value"]
            ), f"{name} は PINNED なのに value が sha256 hex64 形式ではありません: {field.get('value')!r}"
    assert checked_any, "schema に *_sha256 フィールドが1つも見つかりませんでした"


def test_sha256_pattern_violation_is_detected() -> None:
    """sha256 hex64 pattern 照合ロジックが不正値を実際に検出できることの
    負例テスト。"""
    assert not SHA256_HEX_PATTERN.fullmatch("not-a-valid-sha")
    assert not SHA256_HEX_PATTERN.fullmatch("A" * 64)  # 大文字は不可
    assert not SHA256_HEX_PATTERN.fullmatch("0" * 63)  # 桁数不足
    assert SHA256_HEX_PATTERN.fullmatch("0" * 64)


# --- 修正8: 未強制の schema 制約をテストへ実装 ------------------------------


def test_schema_pinned_field_definition_declares_expected_allowed_keys(
    schema: Dict[str, Any],
) -> None:
    """(a) pinned_field 定義の許容キーが {value, source, status, reason} の
    みであること（schema 側の additionalProperties: false）。"""
    pinned_field_def = schema["definitions"]["pinned_field"]
    assert pinned_field_def.get("additionalProperties") is False
    assert set(pinned_field_def["properties"].keys()) == {"value", "source", "status", "reason"}


def test_pinned_field_shaped_projection_entries_reject_extra_keys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(a) 実データ + 負例: pinned_field 形の projection エントリが許容キー外
    を持てば検出できること。"""
    allowed = set(schema["definitions"]["pinned_field"]["properties"].keys())
    for name in _pinned_field_names(schema):
        extra = set(projection[name].keys()) - allowed
        assert not extra, f"{name} has keys outside {allowed}: {extra}"

    tampered = dict(projection["baseline_run"])
    tampered["unexpected_extra_key"] = "should not be allowed"
    assert set(tampered.keys()) - allowed == {"unexpected_extra_key"}


def test_schema_single_intervention_declares_expected_allowed_keys(schema: Dict[str, Any]) -> None:
    """(b) single_intervention の許容キーが {declared, intervention, count}
    のみであること。"""
    si_schema = schema["properties"]["single_intervention"]
    assert si_schema.get("additionalProperties") is False
    assert set(si_schema["properties"].keys()) == {"declared", "intervention", "count"}


def test_single_intervention_projection_rejects_extra_keys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(b) 実データ + 負例。"""
    allowed = set(schema["properties"]["single_intervention"]["properties"].keys())
    extra = set(projection["single_intervention"].keys()) - allowed
    assert not extra, f"single_intervention has keys outside {allowed}: {extra}"

    tampered = dict(projection["single_intervention"])
    tampered["unexpected_extra_key"] = "x"
    assert set(tampered.keys()) - allowed == {"unexpected_extra_key"}


def test_schema_design_doc_sha256_has_hex64_pattern(schema: Dict[str, Any]) -> None:
    """(c) design_doc_sha256 の schema pattern が ^[0-9a-f]{64}$ であること。"""
    assert schema["properties"]["design_doc_sha256"]["pattern"] == r"^[0-9a-f]{64}$"


def test_schema_claim_strength_target_value_enum_excludes_forbidden_symbols(
    schema: Dict[str, Any],
) -> None:
    """(d) claim_strength_target.value の許容値が
    {null, descriptive, suggestive, moderate, strong} のみで、"C0"〜"C3" 等の
    禁止記号（DEBT_ADJUDICATION_v1.1.md 裁定1: causal_evidence_strength 語彙は
    C0-C3 記号を禁止）が含まれないこと。"""
    allowed_values = schema["properties"]["claim_strength_target"]["properties"]["value"]["enum"]
    assert set(allowed_values) == {"descriptive", "suggestive", "moderate", "strong", None}
    forbidden_symbols = ["C0", "C1", "C2", "C3"]
    for symbol in forbidden_symbols:
        assert symbol not in allowed_values, f"forbidden symbol {symbol!r} must not be a valid enum value"


def test_claim_strength_target_projection_value_is_within_allowed_enum(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(d) 実データが許容 enum 内であること。"""
    allowed_values = set(schema["properties"]["claim_strength_target"]["properties"]["value"]["enum"])
    assert projection["claim_strength_target"]["value"] in allowed_values


def test_schema_main_training_gate_enum_is_open_blocked_closed_only(schema: Dict[str, Any]) -> None:
    """(e) main_training_gate の schema enum が {"OPEN", "BLOCKED", "CLOSED"}
    のみであること（Codex 第6巡 6-2: 終端状態 CLOSED を追加）。"""
    assert set(schema["properties"]["main_training_gate"]["enum"]) == {"OPEN", "BLOCKED", "CLOSED"}


def test_main_training_gate_projection_value_is_open_blocked_or_closed(
    projection: Dict[str, Any],
) -> None:
    """(e) 実データが許容値内であること。"""
    assert projection["main_training_gate"] in {"OPEN", "BLOCKED", "CLOSED"}


# --- (i) closeout_ref（Codex 第6巡 6-2(a)）----------------------------------


def _check_closeout_ref(projection: Dict[str, Any], repo_root: Path) -> None:
    """main_training_gate == "CLOSED" のとき closeout_ref が repo 内の実在
    ファイルを指すことを検証する。CLOSED でない projection には no-op
    （closeout_ref は CLOSED 以外では省略可）。design_doc の repo外束縛拒否
    （修正2/P2 = `_check_design_doc_sha256`）と同じ絶対パス/親ディレクトリ
    越境拒否を適用する。

    Codex 第7巡 7-2 でバイト束縛 + 内容照合の二層を追加しファミリー終端する
    （偽 CLOSE 経路の閉塞）:
    (a) closeout_ref_sha256 が closeout_ref 実ファイルの sha256 と一致する
        こと（design_doc_sha256 と同機構。closeout 記録の改変を検出する）。
    (b) closeout 本文に終端裁定マーカー（run_id から動的に導出した
        `Run <N>\\s*=\\s*CLOSED`）と projection.reason の文字列が実在する
        こと（sha256 一致だけでは「別内容だが同じ参照先パスを指すファイル」
        を検出できないため、内容そのものが終端裁定を裏付けることも検査する）。
    """
    if projection.get("main_training_gate") != "CLOSED":
        return

    closeout_ref = projection.get("closeout_ref")
    assert isinstance(closeout_ref, str) and closeout_ref, (
        "main_training_gate == CLOSED のとき closeout_ref が必須です"
    )
    closeout_ref_path = Path(closeout_ref)
    assert not closeout_ref_path.is_absolute(), (
        f"closeout_ref は repo 相対パスのみ許容します（絶対パスは拒否）: {closeout_ref!r}"
    )
    assert ".." not in closeout_ref_path.parts, (
        f"closeout_ref に親ディレクトリ越境（'..'）は許容しません: {closeout_ref!r}"
    )

    repo_root_resolved = repo_root.resolve()
    resolved = (repo_root / closeout_ref_path).resolve()
    assert resolved.is_relative_to(repo_root_resolved), (
        f"closeout_ref の解決後パスが repo_root 配下ではありません: {resolved}"
    )
    assert resolved.exists(), f"closeout_ref が指すファイルが存在しません: {resolved}"

    # (a) バイト束縛: closeout_ref_sha256 が実ファイルと一致すること。
    closeout_ref_sha256 = projection.get("closeout_ref_sha256")
    assert isinstance(closeout_ref_sha256, str) and closeout_ref_sha256, (
        "main_training_gate == CLOSED のとき closeout_ref_sha256 が必須です"
    )
    actual_sha256 = _sha256_of_file(resolved)
    assert actual_sha256 == closeout_ref_sha256, (
        "closeout 記録が改変されたのに projection の closeout_ref_sha256 が"
        f"追随していません: actual={actual_sha256} projection={closeout_ref_sha256}"
    )

    # (b) 内容照合: 終端裁定マーカーと reason の文字列が本文に実在すること。
    body = resolved.read_text(encoding="utf-8")
    run_id = projection.get("run_id", "")
    run_number_match = re.search(r"run(\d+)", str(run_id), re.IGNORECASE)
    assert run_number_match, (
        f"run_id {run_id!r} から run 番号を抽出できません（終端裁定マーカー照合に必要）"
    )
    run_label = f"Run {run_number_match.group(1)}"
    marker_pattern = re.compile(re.escape(run_label) + r"\s*=\s*CLOSED")
    assert marker_pattern.search(body), (
        f"closeout 本文に終端裁定マーカー（pattern={marker_pattern.pattern!r}）が"
        f"見つかりません: {resolved}"
    )

    reason = projection.get("reason")
    assert isinstance(reason, str) and reason, (
        "main_training_gate == CLOSED のとき reason が必須です"
    )
    assert reason in body, (
        f"closeout 本文に projection.reason（{reason!r}）が見つかりません: {resolved}"
    )


def test_closeout_ref_exists_when_gate_is_closed(projection: Dict[str, Any]) -> None:
    """(i) Codex 第10巡 10-2 で全 projection への条件付き検査に一般化
    （旧: 現状データが CLOSED であることを前提に決め打ちしていた）。
    `_check_closeout_ref` 自身が main_training_gate != "CLOSED" のとき
    no-op するため、この呼び出しは「CLOSED を宣言している projection では
    closeout_ref が実在の repo 内ファイルを指すこと」を、CLOSED でない
    projection に対しては何もしない形で、discovered された全 projection に
    対して一括で適用する。"""
    _check_closeout_ref(projection, REPO_ROOT)


def test_check_closeout_ref_is_noop_when_gate_not_closed() -> None:
    """(i) CLOSED でない projection には closeout_ref 検査そのものが
    適用されない（省略可であることの直接確認）。"""
    non_closed_projection = {"main_training_gate": "BLOCKED"}
    _check_closeout_ref(non_closed_projection, REPO_ROOT)  # 例外を投げないことの確認


# --- Run 8 固有の closeout_ref negative test 群 -----------------------------
#
# 以下は「main_training_gate を CLOSED に据えたまま特定フィールドを改竄する」
# ことが検査の前提であり、汎用 parametrize された `projection`（将来
# gate=OPEN/BLOCKED の projection も含みうる）に対しては意味を持たない
# （非 CLOSED の projection では改竄しても `_check_closeout_ref` が
# no-op するだけで、pytest.raises(AssertionError) が失敗してしまう）。
# そのため Codex 第10巡 10-2 で `run8_projection`（実際に CLOSED を宣言
# している Run 8 固定）へ明示的に分離した。


def test_check_closeout_ref_rejects_missing_ref_when_gate_closed(
    run8_projection: Dict[str, Any],
) -> None:
    """(i) 負例: CLOSED なのに closeout_ref が欠落していれば検出される。"""
    tampered = dict(run8_projection)
    del tampered["closeout_ref"]
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_nonexistent_path(run8_projection: Dict[str, Any]) -> None:
    """(i) 負例: closeout_ref が存在しないファイルを指せば検出される。"""
    tampered = dict(run8_projection)
    tampered["closeout_ref"] = "voice_genesis/foundry/results_s7/does_not_exist.md"
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_absolute_path(run8_projection: Dict[str, Any]) -> None:
    """(i) 負例: closeout_ref が絶対パスなら検出される（design_doc と同型の
    repo外束縛拒否）。"""
    tampered = dict(run8_projection)
    tampered["closeout_ref"] = "/etc/passwd"
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_sha256_mismatch(run8_projection: Dict[str, Any]) -> None:
    """(i)(a) Codex 第7巡 7-2 負例: closeout_ref_sha256 が実ファイルと
    不一致なら検出される。"""
    tampered = dict(run8_projection)
    tampered["closeout_ref_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_ref_swapped_to_different_file(
    run8_projection: Dict[str, Any],
) -> None:
    """(i)(a) Codex 第7巡 7-2 負例: closeout_ref を別の実在ファイルへ
    差し替えると、そのファイルの実 sha256 が pin と一致しないため検出
    される（バイト束縛の実効性 — パス存在検査だけでは通ってしまう）。"""
    tampered = dict(run8_projection)
    tampered["closeout_ref"] = "voice_genesis/foundry/results_s7/s7_reproducibility_finding.md"
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_fabricated_reason(run8_projection: Dict[str, Any]) -> None:
    """(i)(b) Codex 第7巡 7-2 負例: reason を closeout 本文に存在しない
    文字列へ差し替えると内容照合で検出される（sha256 一致だけでは
    reason の捏造を検出できないため、内容照合が必要な理由の実証）。"""
    tampered = dict(run8_projection)
    tampered["reason"] = "FABRICATED_REASON_NOT_IN_CLOSEOUT_BODY"
    with pytest.raises(AssertionError):
        _check_closeout_ref(tampered, REPO_ROOT)


def test_check_closeout_ref_rejects_missing_terminal_marker_in_body() -> None:
    """(i)(b) Codex 第7巡 7-2 負例: closeout 本文に終端裁定マーカー
    （`Run <N> = CLOSED`）が存在しなければ検出される（マーカー抽出ロジック
    自体の健全性を、実 closeout ファイルに依存しない合成データで確認）。"""
    marker_pattern = re.compile(re.escape("Run 8") + r"\s*=\s*CLOSED")
    body_without_marker = "Run 8 は依然として議論中であり、まだ結論していない。"
    assert marker_pattern.search(body_without_marker) is None


# --- (h) 汎用スキーマバリデータ（Codex 第5巡採用 P2） -----------------------
#
# 上の (a)-(g) は個別制約のスポットチェックの積み重ねだった。これを止め、
# RUN_CONTRACT_SCHEMA_v1.json を再帰的に歩く小型汎用バリデータ（外部
# jsonschema 依存なし・純 Python）で projection 全体を一括検証する。
# 対応キーワード: type / const / enum / pattern / required / properties /
# additionalProperties / minLength / $ref（#/definitions/ 内参照の解決）/
# items（あれば）。
#
# 未対応キーワードに遭遇したら黙って無視せず fail する
# （`_UNSUPPORTED_KEYWORD_MARKER` 経由で AssertionError）。これにより、
# 将来 schema に新しい制約キーワード（例: maxLength, oneOf, if/then/else）
# が追加されたのに本バリデータが追随し忘れた場合に、検証漏れが偽 green の
# まま埋もれることを防ぐ。
#
# `description` / `title` / `x-gate-class` / `$schema` / `$id` / `definitions`
# は instance を検証しない既知のメタキーワードとして明示的に許可する
# （実 schema 全体に遍在するドキュメンテーション/自己記述/vendor extension
# キーワードであり、これらまで「未対応」扱いにすると実 schema に対して
# バリデータが即座に動かなくなってしまうため）。
#
# 既知の未実装範囲（本 schema では未使用のため対象外。境界宣言）:
# additionalProperties がスキーマ object 値を取る形（拡張プロパティ自体を
# 別 schema で検証する JSON Schema の一形態）はサポートしない
# （本 schema は真偽値の false しか使わない）。

_KNOWN_METADATA_KEYWORDS = {"description", "title", "x-gate-class", "$schema", "$id", "definitions"}
_SUPPORTED_VALIDATION_KEYWORDS = {
    "type",
    "const",
    "enum",
    "pattern",
    "required",
    "properties",
    "additionalProperties",
    "minLength",
    "$ref",
    "items",
}
_RECOGNIZED_SCHEMA_KEYWORDS = _SUPPORTED_VALIDATION_KEYWORDS | _KNOWN_METADATA_KEYWORDS


def _resolve_definitions_ref(root_schema: Dict[str, Any], ref: str) -> Dict[str, Any]:
    assert ref.startswith("#/definitions/"), f"unsupported $ref target: {ref!r}"
    return root_schema["definitions"][ref.split("/")[-1]]


def _instance_matches_json_type(instance: Any, type_spec: Any) -> bool:
    type_names = type_spec if isinstance(type_spec, list) else [type_spec]
    for type_name in type_names:
        if type_name == "object" and isinstance(instance, dict):
            return True
        if type_name == "string" and isinstance(instance, str):
            return True
        if type_name == "array" and isinstance(instance, list):
            return True
        if type_name == "boolean" and isinstance(instance, bool):
            return True
        # bool は int のサブクラスなので integer/number からは明示的に除外する。
        if type_name == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
            return True
        if (
            type_name == "number"
            and isinstance(instance, (int, float))
            and not isinstance(instance, bool)
        ):
            return True
        if type_name == "null" and instance is None:
            return True
    return False


def _validate_instance(
    root_schema: Dict[str, Any],
    schema_node: Dict[str, Any],
    instance: Any,
    path: str,
    errors: List[str],
) -> None:
    """`schema_node` に対して `instance` を再帰的に検証し、違反メッセージを
    `errors` へ蓄積する（instance データの不正は fail ではなく errors への
    蓄積 — 呼び出し元がまとめて assert する）。schema_node 自体が未対応の
    キーワードを含む場合はデータの正誤に関わらず即座に AssertionError で
    fail する（バリデータ・schema 間の追随漏れは instance の問題ではない
    ため、区別してハード失敗させる）。
    """
    unknown_keywords = set(schema_node.keys()) - _RECOGNIZED_SCHEMA_KEYWORDS
    assert not unknown_keywords, (
        f"{path}: バリデータが対応していない schema キーワードがあります: "
        f"{sorted(unknown_keywords)}（RUN_CONTRACT_SCHEMA_v1.json に新しい制約"
        "キーワードを追加したら、test_run_contract_conformance.py の"
        "_validate_instance にも対応を追加すること）"
    )

    if "$ref" in schema_node:
        resolved = _resolve_definitions_ref(root_schema, schema_node["$ref"])
        _validate_instance(root_schema, resolved, instance, path, errors)
        # $ref の兄弟キー（x-gate-class/description 等）は instance を検証
        # しないメタキーワードのみを許容する契約（本 schema はそれ以外を
        # $ref と併用しない）。$ref 自体の検証はここで完了。
        return

    if "const" in schema_node and instance != schema_node["const"]:
        errors.append(f"{path}: expected const {schema_node['const']!r}, got {instance!r}")

    if "enum" in schema_node and instance not in schema_node["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema_node['enum']!r}")

    if "type" in schema_node and not _instance_matches_json_type(instance, schema_node["type"]):
        errors.append(
            f"{path}: expected type {schema_node['type']!r}, got "
            f"{type(instance).__name__} ({instance!r})"
        )

    if "pattern" in schema_node:
        if not isinstance(instance, str) or re.search(schema_node["pattern"], instance) is None:
            errors.append(
                f"{path}: value {instance!r} does not match pattern {schema_node['pattern']!r}"
            )

    if "minLength" in schema_node:
        if not isinstance(instance, str) or len(instance) < schema_node["minLength"]:
            errors.append(
                f"{path}: value {instance!r} shorter than minLength {schema_node['minLength']}"
            )

    if "required" in schema_node:
        if isinstance(instance, dict):
            for required_key in schema_node["required"]:
                if required_key not in instance:
                    errors.append(f"{path}: missing required key {required_key!r}")
        else:
            errors.append(f"{path}: 'required' constraint applied to non-object instance {instance!r}")

    if "properties" in schema_node and isinstance(instance, dict):
        for prop_name, prop_schema in schema_node["properties"].items():
            if prop_name in instance:
                _validate_instance(
                    root_schema, prop_schema, instance[prop_name], f"{path}.{prop_name}", errors
                )

    if schema_node.get("additionalProperties") is False and isinstance(instance, dict):
        allowed = set(schema_node.get("properties", {}).keys())
        extra = set(instance.keys()) - allowed
        if extra:
            errors.append(f"{path}: unexpected additional properties {sorted(extra)}")

    if "items" in schema_node and isinstance(instance, list):
        for idx, item in enumerate(instance):
            _validate_instance(root_schema, schema_node["items"], item, f"{path}[{idx}]", errors)


def _schema_validation_errors(root_schema: Dict[str, Any], instance: Any) -> List[str]:
    """`root_schema` に対する `instance` の全違反メッセージ一覧を返す
    （空リスト = 完全準拠）。schema_node 自体が未対応キーワードを含む場合は
    ここで AssertionError が伝播する（`_validate_instance` 参照）。"""
    errors: List[str] = []
    _validate_instance(root_schema, root_schema, instance, "$", errors)
    return errors


def test_projection_validates_fully_against_schema_generic_validator(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(h) Codex 第5巡採用 P2: 個別スポットチェックの積み重ねを止め、schema
    全体に対する汎用バリデータで projection を一括検証する。(a)-(g) の
    個別テストは重複のまま残す（削除不要。冗長でも実害はない）。"""
    errors = _schema_validation_errors(schema, projection)
    assert not errors, "projection が schema 制約に違反しています:\n" + "\n".join(errors)


def test_generic_validator_detects_tampered_schema_const(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(h) 負例: `schema` フィールド（const 制約）の改変が検出されること。"""
    tampered = dict(projection)
    tampered["schema"] = "wrong-schema-id/999"
    errors = _schema_validation_errors(schema, tampered)
    assert any(e.startswith("$.schema:") for e in errors), errors


def test_generic_validator_detects_non_string_run_id(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(h) 負例: `run_id`（type: string 制約）が非文字列なら検出されること。"""
    tampered = dict(projection)
    tampered["run_id"] = 12345
    errors = _schema_validation_errors(schema, tampered)
    assert any(e.startswith("$.run_id:") for e in errors), errors


def test_generic_validator_detects_missing_required_field(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(h) 負例: 必須フィールドの欠落が検出されること。"""
    tampered = dict(projection)
    del tampered["baseline_run"]
    errors = _schema_validation_errors(schema, tampered)
    assert any("baseline_run" in e for e in errors), errors


def test_generic_validator_accepts_valid_synthetic_instance() -> None:
    """(h) 汎用バリデータ自体の健全性: $ref 解決・properties 再帰・
    additionalProperties・minLength・enum・pattern・items が実際に正しい
    instance を通すことを、schema 実データに依存しない最小合成例で確認する。"""
    mini_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": "^[0-9a-f]{4}$"},
            "label": {"type": "string", "minLength": 1},
            "tags": {"type": "array", "items": {"type": "string"}},
            "pinned": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "status": {"type": "string", "enum": ["OPEN", "BLOCKED"]},
        },
        "required": ["id", "label", "pinned", "status"],
        "additionalProperties": False,
        "definitions": {
            "pinned_field": {
                "type": "object",
                "properties": {"value": {}, "status": {"type": "string"}},
                "required": ["value", "status"],
                "additionalProperties": False,
            }
        },
    }
    valid_instance = {
        "id": "abcd",
        "label": "x",
        "tags": ["a", "b"],
        "pinned": {"value": "x", "status": "PINNED"},
        "status": "OPEN",
    }
    assert _schema_validation_errors(mini_schema, valid_instance) == []

    invalid_instance = dict(valid_instance)
    invalid_instance["id"] = "not-hex"
    invalid_instance["tags"] = ["a", 1]
    invalid_instance["extra"] = "should not be allowed"
    del invalid_instance["label"]
    errors = _schema_validation_errors(mini_schema, invalid_instance)
    assert any(e.startswith("$.id:") for e in errors)
    assert any(e.startswith("$.tags[1]:") for e in errors)
    assert any("extra" in e for e in errors)
    assert any("label" in e for e in errors)


def test_generic_validator_fails_on_unsupported_schema_keyword() -> None:
    """(h) バリデータの未対応キーワードに遭遇したら黙って無視せず fail する
    ことを、架空の未対応キーワード（maxLength）を含む合成 schema で検証する
    （将来 RUN_CONTRACT_SCHEMA_v1.json に新しい制約キーワードが増えたのに
    バリデータが追随し忘れた場合の偽 green 防止）。"""
    schema_with_unsupported_keyword: Dict[str, Any] = {
        "type": "object",
        "properties": {"name": {"type": "string", "maxLength": 10}},
        "required": ["name"],
        "additionalProperties": False,
        "definitions": {},
    }
    with pytest.raises(AssertionError, match="maxLength"):
        _schema_validation_errors(schema_with_unsupported_keyword, {"name": "x"})
