"""melody/routing.py — 入力種別 → 抽出経路の選択（M1）。

設計 §4.2 table 5 準拠。入力種別ラベル（M1 では fixture のメタデータで与える。
自動判定は M1 の非目標）で分岐し、その入力帯で試すべき抽出経路の候補を返す。

M1 は「どの経路がどの入力帯で `sufficient` になるか」の表を採るのが目的なので、
routing は**候補経路の列挙**に徹し、どれが成立するかの判定は
`observability.assess_observability` に委ねる。旋律不在（和音パッド）は
`applies=False` の経路を 1 本だけ返し、抽出器を適用せず `not_observed` へ落とす。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

__all__ = [
    "InputKind",
    "MelodyRoute",
    "INPUT_KINDS",
    "select_routes",
]

# 入力種別ラベル（fixture メタデータで与える）。
InputKind = str
INPUT_KINDS: tuple[str, ...] = (
    "vocal_track",
    "clear_lead",
    "full_mix",
    "chord_pad_no_melody",
    # M2e §6.3（加算的な新キー）: フルミックスを分離を通さず直接抽出器に当てる帯。
    # 既存 4 キーの route 列は 1 本も変えない（下記 `_ROUTES` のコメント参照）。
    "full_mix_direct_probe",
)

# 前処理・抽出器の語彙（provenance ラベルにそのまま使う）。
_PRE_NONE = "none"
_PRE_DEMUCS = "demucs_vocals"
_EXT_PYIN = "pyin"
_EXT_BASIC_PITCH = "basic_pitch"
_EXT_CREPE = "crepe"
_EXT_MELODIA = "melodia"
_EXT_NONE = "none"


@dataclass(frozen=True)
class MelodyRoute:
    """1 本の抽出経路（前処理 + 抽出器）。

    Attributes
    ----------
    name
        経路ラベル（例 ``demucs_vocals_then_crepe``）。`MelodyObservation.route`
        に転記される。
    preprocessing
        ``none`` または ``demucs_vocals``（Demucs でボーカル stem を分離）。
    extractor
        ``pyin`` / ``basic_pitch`` / ``crepe`` / ``melodia`` / ``none``。
        ``none`` は抽出器を適用しない（`applies=False` と対）。
    assist
        補助抽出器（一致時のみ採用する等）。無ければ空文字列。
    applies
        ``False`` の経路は旋律不在入力に対する短絡で、抽出せず not_observed へ
        落とすことを表す。
    """

    name: str
    preprocessing: str
    extractor: str
    assist: str = ""
    applies: bool = True

    @property
    def requires_separation(self) -> bool:
        return self.preprocessing == _PRE_DEMUCS


# 設計 §4.2 table 5 準拠の経路表。pyin(既存)/basic-pitch(既存) をベースラインに
# 含め、Demucs→CREPE と Melodia の 2 経路を新規に配線する（§4.3）。
_ROUTES: Dict[str, List[MelodyRoute]] = {
    "vocal_track": [
        MelodyRoute("pyin_direct", _PRE_NONE, _EXT_PYIN),
        MelodyRoute("demucs_vocals_then_pyin", _PRE_DEMUCS, _EXT_PYIN),
        MelodyRoute("demucs_vocals_then_crepe", _PRE_DEMUCS, _EXT_CREPE),
        MelodyRoute("demucs_vocals_then_melodia", _PRE_DEMUCS, _EXT_MELODIA),
    ],
    "clear_lead": [
        MelodyRoute("pyin_direct", _PRE_NONE, _EXT_PYIN),
        MelodyRoute("melodia_direct", _PRE_NONE, _EXT_MELODIA),
        MelodyRoute("crepe_direct", _PRE_NONE, _EXT_CREPE),
    ],
    "full_mix": [
        MelodyRoute("demucs_vocals_then_crepe", _PRE_DEMUCS, _EXT_CREPE),
        MelodyRoute("melodia_direct", _PRE_NONE, _EXT_MELODIA),
        MelodyRoute(
            "basic_pitch_direct", _PRE_NONE, _EXT_BASIC_PITCH, assist=_EXT_MELODIA
        ),
    ],
    "chord_pad_no_melody": [
        MelodyRoute("not_applicable", _PRE_NONE, _EXT_NONE, applies=False),
        # 負の対照の診断経路: 旋律不在入力に pyin を実際に当て、ゲートが
        # insufficient で弾くことを committed ハーネス（CI 安全な pyin 経路）でも
        # 実証する。routing 短絡（not_applicable）だけだと抽出器の false positive
        # 耐性が harness 出力に現れず隠れるため、明示的な負の対照経路を持つ
        # （設計 §4.1 / Codex 指摘）。実運用の旋律不在入力でもゲートで弾かれる
        # ことを示す belt-and-suspenders でもある。
        MelodyRoute("pyin_negative_control", _PRE_NONE, _EXT_PYIN),
    ],
    # M2e（`docs/DESIGN_M2e_vremix_real_bed.md` §6.3）: V-remix 帯の direct アーム。
    # `_ROUTES["full_mix"]` に `crepe_direct` は入っていないため direct アームを
    # `input_kind: "full_mix"` では表現できず、`"clear_lead"` を名乗らせると**素材の
    # 宣言が虚偽になる**（入力は明白にフルミックスである。`V_direct` が clear_lead を
    # 名乗れたのは実際に clear lead だったから）。よって「フルミックスを、分離を
    # 通さず直接抽出器に当てる」という測定対象そのものを表す**新キーを加算**する。
    #
    # **既存キーへの route 追加は引き続き禁止**（`full_mix` に `crepe_direct` を足す案は
    # M1 / M4 の選択挙動を変えうるため却下・設計 §6.3）。加算的な新キーのみ許す。
    "full_mix_direct_probe": [
        MelodyRoute("crepe_direct", _PRE_NONE, _EXT_CREPE),
    ],
}


def select_routes(input_kind: InputKind) -> List[MelodyRoute]:
    """`input_kind` に対応する抽出経路候補を返す。

    未知の入力種別は fail-fast（fixture メタデータの typo を握りつぶさない）。
    """
    if input_kind not in _ROUTES:
        raise ValueError(
            f"unknown input_kind {input_kind!r}; expected one of {list(INPUT_KINDS)}"
        )
    return list(_ROUTES[input_kind])
