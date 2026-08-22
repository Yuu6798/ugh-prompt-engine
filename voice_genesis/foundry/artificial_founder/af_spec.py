"""af_spec.py — AF-P0 の凍結定義とロード層（設計書 §6 / §8 / §11 / §21）。

**凍結定義とロードだけを置く。** 音響処理・測定・判定は置かない（`af_source` /
`af_filter` / `af_expression` / `af_measure` / `af_gates` の責務）。

`af_measure` はこのモジュールを import してよいが、**AF0 の設計値を含む
`FounderGenome` を渡してはならない**（設計書 §14 の測定器分離。`af_measure` 側に
読み取り tripwire を置いて機械的に保証する）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from af_schema import SpecError, is_safe_name, require_valid_founder_spec, validate_criteria  # noqa: F401,E501  (再輸出)

HERE = Path(__file__).resolve().parent

#: 削除を伴う書き出しで **絶対に消してはならない** ツリー（パッケージ / リポジトリ）。
PROTECTED_TREE_ROOTS: Tuple[Path, ...] = (HERE, HERE.parents[2])


class OutputCollisionError(ValueError):
    """出力先が保護対象（入力・パッケージ・リポジトリ）と重なっている。"""


def output_snapshot_path(out_dir: Path) -> Path:
    """`p0_run.prepare_output_tree` が直前ツリーを退避する先。**唯一の定義**。

    削除ガードと実装が別々にこの名前を綴ると、片方だけ変えた瞬間に保護が外れる。
    両者はこの関数を通す。
    """
    out = Path(out_dir)
    return out.parent / f".{out.name}.previous"


def reject_output_collision(out_dir: Path, protected: Sequence[Path]) -> None:
    """**削除前に** 出力先が保護対象を消す構成を拒否する。

    AF-P0 で出力先を消してから書く writer は 3 つある:
    `p0_run.prepare_output_tree` / `af_utau.write_body` (`--compile-only` 経由) /
    `convert_founder.convert_from_genome`。いずれも `rmtree` するので、
    `--out .` や `founder_specs/`、パッケージ / リポジトリのルートを渡すと
    spec 本体やリポジトリ内容を消してから失敗する。出力先が保護対象と同一、
    あるいは保護対象を **内包** する場合は着手前に止める（既定の
    `results/AF0` はパッケージ配下だが何も内包しないので通る）。

    `prepare_output_tree` が実際に触る派生パス（スナップショット）も同じ規則で
    検査する。派生パス名は `output_snapshot_path` **1 箇所** から取る。
    """
    out = Path(out_dir).resolve()
    candidates = [out, output_snapshot_path(out)]
    for prot in protected:
        try:
            target = Path(prot).resolve()
        except OSError:  # pragma: no cover
            continue
        for cand in candidates:
            if cand == target or cand in target.parents:
                raise OutputCollisionError(
                    f"--out {out} would delete a protected path ({target}); "
                    "choose an output directory that neither is nor contains an input, "
                    "the package directory, or the repository root")

P0_SCHEMA = "voicegenesis-artificial-founder-p0/1.1"
CRITERIA_SCHEMA = "voicegenesis-artificial-founder-criteria/1.1"
CONTROLS_SCHEMA = "voicegenesis-artificial-founder-controls/1.1"
PROBES_SCHEMA = "voicegenesis-artificial-founder-probes/1.1"
FREEZE_SCHEMA = "voicegenesis-artificial-founder-freeze/0.1"
MANIFEST_SCHEMA = "voicegenesis-artificial-founder-body/0.1"
UNIT_TRUTH_SCHEMA = "voicegenesis-artificial-founder-unit-truth/0.1"

EXPERIMENT_ID = "AF-P0"

#: §24 exit code。
EXIT_CODES: Dict[str, int] = {"PASS": 0, "NOT_ESTABLISHED": 1, "BLOCKED": 3, "FAILED": 4}

#: §20 の 4 verdict 以外を作らない。
VERDICTS: Tuple[str, ...] = ("PASS", "NOT_ESTABLISHED", "BLOCKED", "FAILED")


class AFStop(Exception):
    """P0 の停止条件（§20 BLOCKED / FAILED）。原因・影響・最小修正案だけを持つ。"""

    def __init__(self, cause: str, impact: str, minimal_fix: str,
                 status: str = "BLOCKED", reason_code: str = "") -> None:
        super().__init__(cause)
        if status not in ("BLOCKED", "FAILED"):
            raise ValueError(f"AFStop.status must be BLOCKED or FAILED, got {status!r}")
        self.cause, self.impact, self.minimal_fix = cause, impact, minimal_fix
        self.status = status
        self.reason_code = reason_code or status

    def as_dict(self) -> Dict[str, str]:
        return {"status": self.status, "reason_code": self.reason_code, "cause": self.cause,
                "impact": self.impact, "minimal_fix": self.minimal_fix}


# ---------------------------------------------------------------------------
# §11 インベントリ: かなエイリアス -> (ASCII ファイル名, onset クラス, 母音)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AliasUnit:
    alias: str          # oto.ini のエイリアス（かな）
    stem: str           # WAV ファイル名（ASCII, 拡張子なし）
    onset: str          # "none" / "k" / "s" / "sh" / "n" / "r"
    vowel: str          # "a" / "i" / "u" / "e" / "o"

    @property
    def wav_filename(self) -> str:
        return f"{self.stem}.wav"

    @property
    def has_onset(self) -> bool:
        return self.onset != "none"

    @property
    def phonemes(self) -> Tuple[str, ...]:
        """§13 DiffSinger 変換用の音素列（Founder Genome から決定論導出）。"""
        return (self.vowel,) if self.onset == "none" else (self.onset, self.vowel)


_ALIAS_ROWS: Tuple[Tuple[str, str, str, str], ...] = (
    ("あ", "a", "none", "a"), ("い", "i", "none", "i"), ("う", "u", "none", "u"),
    ("え", "e", "none", "e"), ("お", "o", "none", "o"),
    ("か", "ka", "k", "a"), ("き", "ki", "k", "i"), ("く", "ku", "k", "u"),
    ("け", "ke", "k", "e"), ("こ", "ko", "k", "o"),
    ("さ", "sa", "s", "a"), ("し", "shi", "sh", "i"), ("す", "su", "s", "u"),
    ("せ", "se", "s", "e"), ("そ", "so", "s", "o"),
    ("な", "na", "n", "a"), ("に", "ni", "n", "i"), ("ぬ", "nu", "n", "u"),
    ("ね", "ne", "n", "e"), ("の", "no", "n", "o"),
    ("ら", "ra", "r", "a"), ("り", "ri", "r", "i"), ("る", "ru", "r", "u"),
    ("れ", "re", "r", "e"), ("ろ", "ro", "r", "o"),
)

ALIAS_UNITS: Tuple[AliasUnit, ...] = tuple(AliasUnit(*row) for row in _ALIAS_ROWS)
ALIAS_BY_KANA: Dict[str, AliasUnit] = {u.alias: u for u in ALIAS_UNITS}
ALIAS_BY_STEM: Dict[str, AliasUnit] = {u.stem: u for u in ALIAS_UNITS}


def units_for_inventory(aliases: Sequence[str]) -> Tuple[AliasUnit, ...]:
    """`inventory.aliases` の順序を保ったまま `AliasUnit` へ解決する（未知は fail-closed）。"""
    out: List[AliasUnit] = []
    unknown = [a for a in aliases if a not in ALIAS_BY_KANA]
    if unknown:
        raise SpecError([f"root.inventory.aliases: unsupported alias(es) {unknown}"])
    for a in aliases:
        out.append(ALIAS_BY_KANA[a])
    return tuple(out)


# ---------------------------------------------------------------------------
# Founder Genome（凍結値オブジェクト）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FounderGenome:
    """`founder_specs/AF0.json` の凍結ビュー。**唯一の正典入力**（§6）。"""

    raw: Mapping[str, Any]
    canonical_json: str
    sha256: str

    # --- generator ---
    founder_id: str
    phenotype_codename: str
    sample_rate_hz: int
    canonical_pitch_hz: float
    harmonic_count: int
    harmonic_rolloff_power: float
    global_seed: int
    harmonic_phase_scheme: str
    breath_noise_db_rel_sustain: float

    # --- HL-alpha ---
    hl_odd_multiplier: float
    hl_even_multiplier: float

    # --- identity ---
    vowels: Mapping[str, Tuple[float, float, float]]
    vowel_formant_bandwidths_hz: Tuple[float, float, float]
    ar_alpha_center_hz: float
    ar_alpha_bandwidth_hz: float
    ar_alpha_gain_db: float
    ar_beta_center_hz: float
    ar_beta_bandwidth_hz: float
    beta_alpha_energy_ratio: float

    # --- performance ---
    core_f0_hz: float
    articulation_ms: float
    r_onset_ms: float
    i_vowel_ms: float
    r_onset_share: float
    attack_ms: float
    sustain_dbfs: float
    main_taper_ms: float
    release_curve: str

    # --- AG-alpha ---
    terminal_window_ms: float
    terminal_f0_delta_cents: float
    ar_alpha_afterglow_extra_ms: float
    terminal_zero_hold_ms: float

    # --- body ---
    pitch_dir: str
    lead_zero_ms: float
    tail_zero_ms: float
    units: Tuple[AliasUnit, ...]

    @property
    def onset_ms(self) -> float:
        """子音 alias の onset 長。§2 の 20/80 を全 CV unit へ一様適用する。"""
        return self.r_onset_ms

    @property
    def vowel_body_ms(self) -> float:
        return self.i_vowel_ms


def genome_from_dict(spec: Mapping[str, Any]) -> FounderGenome:
    """検査済み dict から `FounderGenome` を組む（検査は呼び出し側で済ませる）。"""
    require_valid_founder_spec(dict(spec))
    canon = canonical_json(spec)
    gen = spec["generator"]
    hl = spec["founder_source_traits"]["HL-alpha"]
    idsig = spec["identity_signature"]
    res = idsig["founder_resonances"]
    pg = spec["performance_genes"]
    ag = spec["founder_expression_traits"]["AG-alpha"]
    body = spec["body"]
    return FounderGenome(
        raw=json.loads(canon),
        canonical_json=canon,
        sha256=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        founder_id=spec["founder_id"],
        phenotype_codename=spec["phenotype_codename"],
        sample_rate_hz=int(gen["sample_rate_hz"]),
        canonical_pitch_hz=float(gen["canonical_pitch_hz"]),
        harmonic_count=int(gen["harmonic_count"]),
        harmonic_rolloff_power=float(gen["harmonic_rolloff_power"]),
        global_seed=int(gen["global_seed"]),
        harmonic_phase_scheme=str(gen["harmonic_phase_scheme"]),
        breath_noise_db_rel_sustain=float(gen["breath_noise_db_rel_sustain"]),
        hl_odd_multiplier=float(hl["odd_multiplier"]),
        hl_even_multiplier=float(hl["even_multiplier"]),
        vowels={k: (float(v[0]), float(v[1]), float(v[2])) for k, v in idsig["vowels"].items()},
        vowel_formant_bandwidths_hz=tuple(
            float(b) for b in idsig["vowel_formant_bandwidths_hz"]),  # type: ignore[arg-type]
        ar_alpha_center_hz=float(res["AR-alpha"]["center_hz"]),
        ar_alpha_bandwidth_hz=float(res["AR-alpha"]["bandwidth_hz"]),
        ar_alpha_gain_db=float(res["AR-alpha"]["gain_db"]),
        ar_beta_center_hz=float(res["AR-beta"]["center_hz"]),
        ar_beta_bandwidth_hz=float(res["AR-beta"]["bandwidth_hz"]),
        beta_alpha_energy_ratio=float(res["AR-beta"]["beta_alpha_energy_ratio"]),
        core_f0_hz=float(pg["f0"]["core_f0_hz"]),
        articulation_ms=float(pg["duration"]["articulation_ms"]),
        r_onset_ms=float(pg["duration"]["r_onset_ms"]),
        i_vowel_ms=float(pg["duration"]["i_vowel_ms"]),
        r_onset_share=float(pg["duration"]["r_onset_share"]),
        attack_ms=float(pg["energy"]["attack_ms"]),
        sustain_dbfs=float(pg["energy"]["sustain_dbfs"]),
        main_taper_ms=float(pg["release"]["main_taper_ms"]),
        release_curve=str(pg["release"]["curve"]),
        terminal_window_ms=float(ag["terminal_window_ms"]),
        terminal_f0_delta_cents=float(ag["terminal_f0_delta_cents"]),
        ar_alpha_afterglow_extra_ms=float(ag["ar_alpha_afterglow_extra_ms"]),
        terminal_zero_hold_ms=float(ag["terminal_zero_hold_ms"]),
        pitch_dir=str(body["pitch_dirs"][0]),
        lead_zero_ms=float(body["lead_zero_ms"]),
        tail_zero_ms=float(body["tail_zero_ms"]),
        units=units_for_inventory(spec["inventory"]["aliases"]),
    )


def load_genome(path: str | Path) -> FounderGenome:
    """`founder_specs/AF0.json` を読み、fail-closed 検査を通して凍結ビューを返す。"""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    require_valid_founder_spec(spec)
    return genome_from_dict(spec)


# ---------------------------------------------------------------------------
# §26 / §28 の決定論シリアライズ
# ---------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """JSON canonicalization（§27 で固定する要素の 1 つ）。

    `sort_keys=True` / `ensure_ascii=False` / セパレータ固定 / 末尾改行なし。
    ハッシュ対象はこの関数を必ず経由する。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_json(path: str | Path, obj: Any) -> str:
    """canonical JSON を UTF-8 + 末尾改行 1 個で書き、その sha256 を返す。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(obj) + "\n"
    p.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: str | Path, exclude_names: Sequence[str] = ()) -> List[Tuple[str, str]]:
    """ディレクトリ配下の (POSIX 相対パス, sha256) を**パス昇順**で返す（§27 file order）。"""
    root = Path(root)
    ex = set(exclude_names)
    rows: List[Tuple[str, str]] = []
    for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
        if not p.is_file() or p.name in ex:
            continue
        rows.append((p.relative_to(root).as_posix(), sha256_file(p)))
    return rows


def sha256sums_text(rows: Sequence[Tuple[str, str]]) -> str:
    """`SHA256SUMS.txt` の本文（`<sha>  <path>` 行、パス昇順、LF 終端）。"""
    return "".join(f"{sha}  {rel}\n" for rel, sha in rows)


def aggregate_digest(rows: Sequence[Tuple[str, str]]) -> str:
    """(path, sha) 集合の集約ダイジェスト（パスを結合するので rename も検知する）。"""
    h = hashlib.sha256()
    for rel, sha in rows:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(sha.encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# criteria / controls / probes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Criteria:
    raw: Mapping[str, Any]
    sha256: str

    @property
    def metric_definitions(self) -> Mapping[str, Any]:
        """`af_measure` へ渡してよい唯一の部分木（答えを含まない測定定義）。"""
        return self.raw["metric_definitions"]

    @property
    def body_gates(self) -> Mapping[str, Any]:
        return self.raw["body_gates"]

    @property
    def reexpression_gates(self) -> Mapping[str, Any]:
        return self.raw["reexpression_gates"]

    @property
    def ingestion(self) -> Mapping[str, Any]:
        return self.raw["ingestion"]


def _load_pinned(path: str | Path, expect_schema: str) -> Tuple[Dict[str, Any], str]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    obj = json.loads(text)
    if obj.get("schema") != expect_schema:
        raise SpecError([f"{p.name}: schema must be {expect_schema!r}, got {obj.get('schema')!r}"])
    return obj, hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_criteria(path: str | Path) -> Criteria:
    """criteria を読み、**AF-P0 の凍結契約と一致すること**まで検査する。

    pin されたハッシュは「どの文書だったか」を残すだけで「正しい文書か」は
    保証しない。同じ schema の別文書を渡せば §18 の許容値を無効化して偽の
    PASS を作れるので、identity と閾値の一致を fail-closed で確かめる
    （`af_schema.validate_criteria`）。
    """
    obj, sha = _load_pinned(path, CRITERIA_SCHEMA)
    for key in ("metric_definitions", "body_gates", "reexpression_gates", "ingestion"):
        if key not in obj:
            raise SpecError([f"criteria: missing '{key}'"])
    errors = validate_criteria(obj)
    if errors:
        raise SpecError(errors)
    return Criteria(raw=obj, sha256=sha)


def load_controls(path: str | Path) -> Tuple[Dict[str, Any], str]:
    obj, sha = _load_pinned(path, CONTROLS_SCHEMA)
    if not obj.get("controls"):
        raise SpecError(["controls: empty control set (§17 requires all families)"])
    return obj, sha


#: probe が参照してよい alias（Body の ASCII ファイル名幹）。
#:
#: probe の alias は `wav_dir / f"{stem}.wav"` の **読み取りパス**にも
#: `spectrum_{stem}.json` の **書き出しパス**にもなる。自由文字列のままだと
#: `../../../etc/passwd` のような値で staging の外を読み書きできる。
def frozen_probe_aliases() -> Tuple[str, ...]:
    return tuple(u.stem for u in ALIAS_UNITS)


def load_probes(path: str | Path) -> Tuple[Dict[str, Any], str]:
    """probes を読み、alias が凍結された Body の unit 名だけであることを検査する。"""
    obj, sha = _load_pinned(path, PROBES_SCHEMA)
    allowed = set(frozen_probe_aliases())
    errors: List[str] = []
    for section, value in obj.items():
        if not isinstance(value, dict):
            continue
        aliases = value.get("aliases")
        if aliases is None:
            continue
        if not isinstance(aliases, list):
            errors.append(f"probes.{section}.aliases: expected list")
            continue
        for alias in aliases:
            if not is_safe_name(alias):
                errors.append(
                    f"probes.{section}.aliases: {alias!r} is not a single-component name")
            elif alias not in allowed:
                errors.append(
                    f"probes.{section}.aliases: {alias!r} is not an AF-P0 body unit")
    if errors:
        raise SpecError(errors)
    return obj, sha


def apply_patch(spec: Mapping[str, Any], patch: Mapping[str, Any]) -> Dict[str, Any]:
    """dotted path の上書きを施した **deep copy** を返す（§17 control fixture 用）。

    存在しないパスは作らない（typo を黙って通さない）。
    """
    out = json.loads(canonical_json(spec))
    for dotted, value in patch.items():
        parts = dotted.split(".")
        node: Any = out
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise SpecError([f"control patch: unknown path {dotted!r}"])
            node = node[part]
        if not isinstance(node, dict) or parts[-1] not in node:
            raise SpecError([f"control patch: unknown path {dotted!r}"])
        node[parts[-1]] = value
    return out


# ---------------------------------------------------------------------------
# §9.2 決定論 PRNG のシード導出
# ---------------------------------------------------------------------------
def unit_seed(founder_id: str, alias: str, component: str, global_seed: int = 0) -> int:
    """`SHA256(founder_id + "/" + alias + "/" + component)` から 64bit シードを導出する。

    OS entropy を一切使わない（§9.2）。`global_seed` は系列全体のオフセットとして
    ドメイン文字列へ織り込む（値が変われば全 unit の noise が変わる）。
    """
    material = f"{founder_id}/{alias}/{component}/{global_seed}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    seed = int.from_bytes(digest[:8], "big")
    return seed or 0x9E3779B97F4A7C15  # xorshift は 0 を不動点にするため退避


# ---------------------------------------------------------------------------
# §11 unit タイムライン（設計値。measure 側からは参照しない）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnitTimeline:
    """1 unit のサンプル境界。すべて `int` サンプルで凍結する（丸めを 1 箇所に集約）。

    ```text
    [0, lead)          digital zero
    [lead, onset_end)  子音 onset（母音 unit では長さ 0）
    [onset_end, art_end) 母音本体（末尾 terminal_window で F0 が下降）
    [art_end, main_end)  main half-cosine taper (main_taper_ms)
    [main_end, ag_end)   AR-alpha afterglow のみ（main は digital zero）
    [ag_end, total)      digital zero
    ```
    """

    sr: int
    lead: int
    onset_end: int
    art_end: int
    main_end: int
    ag_end: int
    total: int
    terminal_start: int

    @property
    def onset_len(self) -> int:
        return self.onset_end - self.lead

    @property
    def vowel_len(self) -> int:
        return self.art_end - self.onset_end

    def ms(self, n_samples: int) -> float:
        return 1000.0 * n_samples / self.sr


def _ms_to_samples(ms: float, sr: int) -> int:
    return int(round(ms * sr / 1000.0))


def timeline_for(genome: FounderGenome, unit: AliasUnit) -> UnitTimeline:
    sr = genome.sample_rate_hz
    lead = _ms_to_samples(genome.lead_zero_ms, sr)
    onset = _ms_to_samples(genome.onset_ms, sr) if unit.has_onset else 0
    art = _ms_to_samples(genome.articulation_ms, sr)
    taper = _ms_to_samples(genome.main_taper_ms, sr)
    extra = _ms_to_samples(genome.ar_alpha_afterglow_extra_ms, sr)
    tail = _ms_to_samples(genome.tail_zero_ms, sr)
    onset_end = lead + onset
    art_end = lead + art
    main_end = art_end + taper
    ag_end = main_end + extra
    total = ag_end + tail
    terminal_start = art_end - _ms_to_samples(genome.terminal_window_ms, sr)
    return UnitTimeline(sr=sr, lead=lead, onset_end=onset_end, art_end=art_end,
                        main_end=main_end, ag_end=ag_end, total=total,
                        terminal_start=terminal_start)


def cents(ratio: float) -> float:
    return 1200.0 * math.log2(ratio)


def cents_between(measured_hz: float, target_hz: float) -> float:
    if measured_hz <= 0.0 or target_hz <= 0.0:
        return math.inf
    return cents(measured_hz / target_hz)


def db_to_amp(db: float) -> float:
    return 10.0 ** (db / 20.0)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def round_floats(obj: Any, ndigits: int = 6) -> Any:
    """報告用に float を丸める（§Coding Conventions の「小数 3–4 桁」を JSON 出力へ拡張）。"""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, ndigits) for v in obj]
    return obj


def optional_float(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return float(value)
