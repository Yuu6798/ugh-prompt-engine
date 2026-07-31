"""PreservationContract: IdentityManifest の anchor と ArrangementSpec の保持方針を
突き合わせ、決定論的な正規化済み契約を構築する（AR2-2）。

`ArrangementSpec.preservation.identity_anchors`（anchor id -> `AnchorPreservation`）は
「宣言」であり、`IdentityManifest`（anchor id -> artifact + hash + domain + required）は
「事実」である。本モジュールはこの両者を cross-validate し、`PreservationContract`
（入力 hash + anchor artifact hash 込みの検証済み転記）を組み立てる。

正規化は並べ替え・省略補完ではない: contract の anchors は manifest の anchor 順を
そのまま保持し、policy が省略された非 required anchor は contract に含めない
（省略 = free 等の推測補完はしない）。hash は呼び出し側が明示注入する値のみを使い、
本モジュール自身がファイルを読んだり hash を計算したりすることはない。

CLI 統合・capability・adherence 観測は本モジュールのスコープ外（AR2-2 Design Memo）。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.arrange.identity import AnchorDomain, IdentityManifest
from svp_rpe.arrange.models import AllowedTransformation, ArrangementSpec, PreservationMode
from svp_rpe.arrange.resolver import ArrangementError

_SHA256_PATTERN = r"^[0-9a-f]{64}$"

# AR2-2/AR2-3: domain 別に許容される変形語彙の明示定数（勝手に増やさない。将来の
# 語彙追加は Design Memo 経由）。lyrics / rhythm / motif は変形語彙が未定義のため
# 空集合とする — 語彙なきドメインへの elastic 宣言は契約の空洞化になるため、
# `build_preservation_contract` はこれらのドメインで elastic を一律拒否する
# （hard か free のみ許可）。
# structure の section_insertion/section_omission/section_repetition は AR2-3
# （design_memo_ar2_3.md）で追加した — #193 の実 form 実測が示した挿入・欠落・
# 反復の 3 変形（`AllowedTransformation` のコメント参照）。既存の
# intro_extension（特定の楽曲的意図を持つ intro 延長という個別カテゴリ）と
# instrumental_break（同じく個別カテゴリの間奏挿入）は、より一般的な form
# レベルのカテゴリである section_insertion 等と共存する — 前者を後者の別名や
# 上位互換として扱って削除しない（意図の粒度が異なる別語彙のため）。
DOMAIN_ALLOWED_TRANSFORMS: dict[AnchorDomain, frozenset[str]] = {
    "melody": frozenset({"octave_displacement", "ornamentation", "timing_warp"}),
    "harmony": frozenset({"chord_extensions", "functional_substitution"}),
    "structure": frozenset(
        {
            "intro_extension",
            "instrumental_break",
            "section_insertion",
            "section_omission",
            "section_repetition",
        }
    ),
    "lyrics": frozenset(),
    "rhythm": frozenset(),
    "motif": frozenset(),
}

# M4 (Design Memo M4, DD-7): 軸単位の保持方針（`axis_policy`）が指定できる
# domain とその軸語彙。`DOMAIN_ALLOWED_TRANSFORMS` と異なり、軸語彙がまだ
# 定義されていない domain はキー自体を持たない（`dict.get(domain)` が
# `None` を返す＝「この domain には axis_policy を宣言できない」を意味する。
# `DOMAIN_ALLOWED_TRANSFORMS` の「空集合で明示」流儀とあえて非対称にしている
# のは、axis_policy が M4 で新設されたばかりの experimental 機構であり、
# 将来の domain 追加が「新しい軸語彙を定義した Design Memo が来たときだけ
# キーを足す」という積極的な opt-in であるべきため（空集合を先回りで
# 埋めると「この domain は検討済みで axis_policy 非対応と確定した」という
# 誤った既定値を意味してしまう）。M4 は melody のみを配線する
# （`docs/DESIGN_M4_recast_melody_anchor.md` §6: 歌詞/和声 anchor への拡張は
# 本設計のスコープ外だが、配線パターンは melody 専用にハードコードしない）。
DOMAIN_AXIS_VOCAB: dict[AnchorDomain, frozenset[str]] = {
    "melody": frozenset({"contour", "interval", "rhythm"}),
}


def _validate_axis_policy(
    axis_policy: Dict[str, str],
    *,
    domain: AnchorDomain,
    anchor_id: str,
    error_cls: Type[Exception],
) -> None:
    """`axis_policy` の 3 条件（Design Memo M4 DD-7）を検証する。共有ヘルパー:
    `ContractAnchor._validate_invariants`（読み戻し経路の safety net、
    `ValueError`）と `build_preservation_contract`（build 経路、文脈の濃い
    `PreservationContractError`）の両方から同じ判定ロジックを呼ぶ
    （`DOMAIN_ALLOWED_TRANSFORMS` の elastic-vocab チェックと同型の二重化）。

    (a) domain が `DOMAIN_AXIS_VOCAB` に語彙を持たない、または軸名が
        その語彙の部分集合でない → 拒否（無語彙 domain への指定は fail-closed）。
    (b) 非空であること。
    (c) hard または elastic が最低 1 軸あること（全 free は「何も約束しない
        anchor」であり契約として空洞化するため拒否する）。
    """
    if not axis_policy:
        raise error_cls(
            f"ContractAnchor '{anchor_id}': axis_policy must not be empty "
            "(omit the field entirely to declare no axis_policy)"
        )
    axis_vocab = DOMAIN_AXIS_VOCAB.get(domain)
    if not axis_vocab:
        raise error_cls(
            f"ContractAnchor '{anchor_id}' (domain={domain!r}): axis_policy is not "
            "supported for this domain (no axis vocabulary registered in DOMAIN_AXIS_VOCAB)"
        )
    unknown_axes = sorted(set(axis_policy) - axis_vocab)
    if unknown_axes:
        raise error_cls(
            f"ContractAnchor '{anchor_id}' (domain={domain!r}): axis_policy references "
            f"axis(es) outside the domain's vocabulary {sorted(axis_vocab)}: {unknown_axes}"
        )
    if not any(mode in ("hard", "elastic") for mode in axis_policy.values()):
        raise error_cls(
            f"ContractAnchor '{anchor_id}': axis_policy must declare at least one 'hard' or "
            "'elastic' axis (an all-'free' policy would promise nothing)"
        )


class PreservationContractError(ArrangementError):
    """PreservationContract の cross-validation（policy vs manifest）に関するエラー。"""


class ContractModel(BaseModel):
    """contract 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class InputHash(ContractModel):
    """契約が束ねる入力 1 件分の sha256 宣言（呼び出し側が明示注入する値のみ）。"""

    sha256: str = Field(pattern=_SHA256_PATTERN)


class ContractInputs(ContractModel):
    identity_manifest: InputHash
    arrangement_spec: InputHash


class ContractAnchor(ContractModel):
    """1 anchor 分の正規化済み保持方針（policy + manifest 記載の artifact hash）。

    build 時の不変条件（mode/allow 整合・domain 語彙）を schema level でも強制する:
    JSON から `model_validate` で読み戻す経路では `build_preservation_contract` の
    cross-validation を通らないため、この validator が「検証済み契約」の safety net
    となる（build 側の cross-validation は文脈の濃い `PreservationContractError` を
    出す役割で残しており、build 経路での二重チェックは harmless）。
    """

    anchor_id: str
    domain: AnchorDomain
    mode: PreservationMode
    allow: List[AllowedTransformation] = []
    tolerance_profile: Optional[str] = None
    artifact: str
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    # M4 (DD-7): additive・opt-in の軸単位保持方針。`None`（既定）は
    # 「axis_policy 宣言なし」= 現行挙動（`_observe_melody` LCS・本会計）を
    # 完全維持する（M4 experimental 経路への opt-in を一切トリガーしない）。
    axis_policy: Optional[Dict[str, PreservationMode]] = None

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ContractAnchor":
        if self.mode in ("hard", "free") and self.allow:
            reason = (
                "hard forbids all transformations"
                if self.mode == "hard"
                else "free does not constrain transformations by enumeration"
            )
            raise ValueError(
                f"ContractAnchor: mode={self.mode!r} must have an empty 'allow' list "
                f"({reason}), got {self.allow!r}"
            )
        if self.mode == "elastic" and not self.allow:
            raise ValueError(
                "ContractAnchor: mode='elastic' requires at least one entry in "
                "'allow' (an empty allow list would make the contract vacuous)"
            )
        allowed_vocab = DOMAIN_ALLOWED_TRANSFORMS[self.domain]
        disallowed = sorted(set(self.allow) - allowed_vocab)
        if disallowed:
            raise ValueError(
                f"ContractAnchor '{self.anchor_id}' (domain={self.domain!r}): "
                f"transformation(s) outside the domain's allowed vocabulary "
                f"{sorted(allowed_vocab)}: {disallowed}"
            )
        if self.axis_policy is not None:
            _validate_axis_policy(
                self.axis_policy,
                domain=self.domain,
                anchor_id=self.anchor_id,
                error_cls=ValueError,
            )
        return self


class PreservationContract(ContractModel):
    """work 単位の PreservationContract。

    `anchor_id` の一意性を schema level で強制する: builder 経由では
    `IdentityManifest` の重複 anchor id 拒否により発生し得ないが、JSON から
    `model_validate` で読み戻す経路では同一 anchor に矛盾する policy / hash を
    重複エントリとして持ち込めてしまうため、`ContractAnchor` の invariant
    validator と同じ「読み戻し経路の safety net」としてここで拒否する
    （`IdentityManifest._validate_unique_anchor_ids` と同型）。
    """

    schema_version: Literal["preservation-contract/0.1"] = "preservation-contract/0.1"
    work_id: str
    inputs: ContractInputs
    anchors: List[ContractAnchor]

    @model_validator(mode="after")
    def _validate_unique_anchor_ids(self) -> "PreservationContract":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for anchor in self.anchors:
            if anchor.anchor_id in seen:
                duplicates.add(anchor.anchor_id)
            seen.add(anchor.anchor_id)
        if duplicates:
            raise ValueError(
                f"duplicate anchor id(s) in preservation contract: "
                f"{', '.join(sorted(duplicates))}"
            )
        return self


def build_preservation_contract(
    manifest: IdentityManifest,
    spec: ArrangementSpec,
    *,
    manifest_sha256: str,
    spec_sha256: str,
) -> PreservationContract:
    """`manifest` の anchor 事実と `spec.preservation.identity_anchors` の宣言を
    cross-validate し、決定論的な `PreservationContract` を構築する。

    `manifest_sha256` / `spec_sha256` は呼び出し側が明示計算して渡す値であり
    （本関数はファイル読み・hash 計算を一切行わない）、schema 側で sha256 形式を
    検証する。

    検証順: (1) policy が manifest に存在しない anchor id を指していないか
    (2) `required: true` の anchor すべてに policy があるか (3) elastic の
    `allow` が anchor の domain 語彙内に収まっているか、の順に一括チェックし、
    いずれかに違反すれば `PreservationContractError` を送出する（message に
    work_id と anchor id を含める）。

    policy が省略された非 required anchor は contract の `anchors` に含めない
    （required は網羅チェックで強制されるため、省略できるのは非 required の
    み — 省略 = free 等への推測補完はしない）。
    """

    work_id = manifest.meta.work_id
    policies = spec.preservation.identity_anchors or {}
    manifest_anchor_ids = {anchor.id for anchor in manifest.anchors}

    unknown_ids = sorted(set(policies) - manifest_anchor_ids)
    if unknown_ids:
        raise PreservationContractError(
            f"preservation contract '{work_id}': identity_anchors policy references "
            f"anchor id(s) not present in the identity manifest: {', '.join(unknown_ids)}"
        )

    missing_required = sorted(
        anchor.id for anchor in manifest.anchors if anchor.required and anchor.id not in policies
    )
    if missing_required:
        raise PreservationContractError(
            f"preservation contract '{work_id}': required anchor(s) have no preservation "
            f"policy in identity_anchors: {', '.join(missing_required)}"
        )

    contract_anchors: List[ContractAnchor] = []
    for anchor in manifest.anchors:
        policy = policies.get(anchor.id)
        if policy is None:
            continue

        if policy.mode == "elastic":
            allowed_vocab = DOMAIN_ALLOWED_TRANSFORMS[anchor.domain]
            disallowed = sorted(set(policy.allow) - allowed_vocab)
            if disallowed:
                raise PreservationContractError(
                    f"preservation contract '{work_id}': anchor '{anchor.id}' "
                    f"(domain={anchor.domain!r}) declares elastic transformation(s) "
                    f"outside the domain's allowed vocabulary {sorted(allowed_vocab)}: "
                    f"{disallowed}"
                )

        # M4 (DD-7): axis_policy は domain 語彙の cross-validation のみここで
        # 行う（非空・hard/elastic 最低 1 軸は `AnchorPreservation` 自身の
        # validator が authoring 時点で既に強制済み）。`_validate_axis_policy`
        # は 3 条件を一括判定するが、ここに到達する時点で (b)/(c) は既に真
        # なので実質 (a) のみが効く — 判定ロジックを 1 箇所に保つため関数は
        # 分割せずそのまま呼ぶ。
        if policy.axis_policy is not None:
            _validate_axis_policy(
                policy.axis_policy,
                domain=anchor.domain,
                anchor_id=anchor.id,
                error_cls=PreservationContractError,
            )

        contract_anchors.append(
            ContractAnchor(
                anchor_id=anchor.id,
                domain=anchor.domain,
                mode=policy.mode,
                allow=list(policy.allow),
                tolerance_profile=policy.tolerance_profile,
                artifact=anchor.artifact,
                artifact_sha256=anchor.sha256,
                axis_policy=dict(policy.axis_policy) if policy.axis_policy is not None else None,
            )
        )

    return PreservationContract(
        schema_version="preservation-contract/0.1",
        work_id=work_id,
        inputs=ContractInputs(
            identity_manifest=InputHash(sha256=manifest_sha256),
            arrangement_spec=InputHash(sha256=spec_sha256),
        ),
        anchors=contract_anchors,
    )
