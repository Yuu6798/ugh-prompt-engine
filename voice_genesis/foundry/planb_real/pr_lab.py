"""planb_real/pr_lab.py — 音素ラベルの寛容な読み取り（実コーパス構造は未検分）。

実コーパスのラベル書式を決め打ちしない。読めた事実（時間単位の推定根拠・
full-context か mono か・wav 尺との整合）を必ず戻り値へ載せ、
食い違いは呼び出し側が fail-closed に扱えるようにする（instruction §16
「corpus structure 予想外 → BLOCKED」）。

対応:
- mono lab:  `<start> <end> <phone>`（HTS 慣行の 100ns 整数、または秒の浮動小数）
- full-context lab: 3 列目が `a^b-c+d=e...` 形式なら中心音素 `c` を取り出す
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# full-context ラベルの中心音素: `... ^ prev - CURRENT + next = ...`
_FULLCTX = re.compile(r"^[^\s]*\^?[^\s]*-([A-Za-z@:_]+)\+")

#: フレーズ境界になる無音ラベル。**`cl` を含めない**（下記 ADJUNCT 参照）。
BOUNDARY_PHONES = frozenset({"sil", "pau", "sp", "silB", "silE", "br", ""})

#: 境界ではないが母音でもない付随ラベル。終端判定では読み飛ばす。
#:
#: - `cl`  : 促音の閉鎖区間。無音だが**語中**であり、フレーズ境界ではない
#:           （実測: リツ DB で `cl` を境界扱いすると terminal /ri/ が 107 件に
#:            水増しされ、正しく扱うと 95 件になる = 12 件が促音由来の偽陽性）
#: - `Edge`/`GlottalStop`: ENUNU 追加ラベル（エッジ声・声門閉鎖）。終端の直後に
#:           付くことがあり、境界扱いしないと本来の終端母音が terminal と
#:           判定されなくなる
ADJUNCT_PHONES = frozenset({"cl", "Edge", "GlottalStop"})

#: 後方互換の別名（無音とみなす集合 = 境界 + 促音閉鎖）
SILENCE_PHONES = BOUNDARY_PHONES | {"cl"}

#: 撥音の表記。既定は HTS 日本語慣行の `N` のみ（頭子音 `n` と混同しないため）。
MORAIC_NASAL_ALIASES = frozenset({"N"})

#: 無声化母音（リツ DB readme (9): 無声化した母音は大文字 AIUEO）。
#: F0 が測れないため probe から除外する（除外数は census が記録する）。
DEVOICED_VOWELS = frozenset({"A", "I", "U", "E", "O"})


@dataclass(frozen=True)
class Phone:
    start_s: float
    end_s: float
    label: str

    @property
    def duration_s(self) -> float:
        return float(self.end_s - self.start_s)

    @property
    def is_silence(self) -> bool:
        return self.label in SILENCE_PHONES

    @property
    def is_boundary(self) -> bool:
        return self.label in BOUNDARY_PHONES

    @property
    def is_adjunct(self) -> bool:
        return self.label in ADJUNCT_PHONES

    @property
    def is_devoiced_vowel(self) -> bool:
        return self.label in DEVOICED_VOWELS


@dataclass
class LabFile:
    path: str
    phones: List[Phone]
    time_unit: str                 # "100ns" / "seconds" / "unknown"
    style: str                     # "mono" / "full_context"
    parse_warnings: List[str] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return float(self.phones[-1].end_s) if self.phones else 0.0


def _detect_unit(rows: List[Tuple[float, float, str]]) -> str:
    if not rows:
        return "unknown"
    last_end = max(r[1] for r in rows)
    # 歌唱 1 ファイルは長くても数百秒。100ns 単位なら 10^7 倍のオーダーになる。
    if last_end >= 1e5:
        return "100ns"
    if last_end <= 1e4:
        return "seconds"
    return "unknown"


def read_lab(path: Path) -> LabFile:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    rows: List[Tuple[float, float, str]] = []
    warnings: List[str] = []
    style = "mono"
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            warnings.append(f"L{lineno}: 列が 3 未満のため無視 ({line[:40]!r})")
            continue
        try:
            a, b = float(parts[0]), float(parts[1])
        except ValueError:
            warnings.append(f"L{lineno}: 時間列が数値でないため無視")
            continue
        raw = " ".join(parts[2:])
        m = _FULLCTX.match(raw)
        if m:
            style = "full_context"
            label = m.group(1)
        else:
            label = parts[2]
        rows.append((a, b, label))

    unit = _detect_unit(rows)
    scale = 1e-7 if unit == "100ns" else 1.0
    phones = [Phone(start_s=a * scale, end_s=b * scale, label=lab) for a, b, lab in rows]
    return LabFile(path=str(path), phones=phones, time_unit=unit, style=style,
                   parse_warnings=warnings)


def phrase_final_indices(phones: List[Phone]) -> List[int]:
    """フレーズ末の有音音素 index。

    付随ラベル（促音閉鎖・エッジ声・声門閉鎖）は読み飛ばしてから境界を見る。
    促音 `cl` を境界扱いすると語中モーラが terminal に化けるため
    （実測でリツ DB の terminal /ri/ が 12 件過剰になった）、
    境界集合と付随集合を分けている。
    """
    out = []
    n = len(phones)
    for i, p in enumerate(phones):
        if p.is_boundary or p.is_adjunct:
            continue
        j = i + 1
        while j < n and phones[j].is_adjunct:
            j += 1
        if j >= n or phones[j].is_boundary:
            out.append(i)
    return out


def find_bigram(phones: List[Phone], first: str, second: str) -> List[int]:
    """(first, second) の連続を探し、second の index を返す。"""
    out = []
    for i in range(len(phones) - 1):
        if phones[i].label == first and phones[i + 1].label == second:
            out.append(i + 1)
    return out


def find_phone(phones: List[Phone], label: str) -> List[int]:
    return [i for i, p in enumerate(phones) if p.label == label]


def normalize_vowel(label: str) -> Optional[str]:
    """母音（+ 撥音）記号へ寄せる。判別できなければ None。

    **小文字 `n` を撥音へ寄せない**: 日本語 HTS 慣行では頭子音の /n/ が `n`、
    撥音が `N` であり、両者を混ぜると「terminal /N/」probe が頭子音を拾う。
    表記が異なるコーパスに当たった場合は census の phoneme vocabulary を見て
    `MORAIC_NASAL_ALIASES` を明示的に拡張すること（推測で寄せない）。
    """
    if label in ("a", "i", "u", "e", "o"):
        return label
    if label in MORAIC_NASAL_ALIASES:
        return "N"
    return None
