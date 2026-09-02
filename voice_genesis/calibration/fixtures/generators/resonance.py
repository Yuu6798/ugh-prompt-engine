"""RESONANCE_GT generator（設計正本 §4.2）: 宣言 pole/Q resonator を broadband
励起（white noise）に適用し、broadband floor に対する declared prominence
（dB）を実現する。truth = center/bandwidth/prominence。M3（formant）とは
異なる出力 construct を宣言（§4.2）— broadband 励起上の単一ピークであり、
harmonic pulse train 励起の pole/zero 構造（FORMANT_GT）とは生成経路が異なる。

prominence 実現則（Codex レビュー 2026-09-01 P2 改訂）: truth は
「LOCAL SPECTRAL PEAK PROMINENCE（宣言 center における spectral floor から
の dB）」であり、`peak_rms / floor_rms`（全帯域 RMS 比）ではない。同じ RMS
比でも `resonance_bandwidth_hz` が異なれば、その RMS が center 近傍にどれだけ
集中するかが変わるため、旧実装（RMS 比のみでスケール）は帯域幅が異なる行で
同じ declared prominence を宣言していても実現値が食い違っていた。

本実装は以下の決定論的な **one-shot 補正パス**でこれを是正する（反復探索
ではない）:

1. 従来どおり `peak_rms / floor_rms = 10**(prominence_db/20)` を満たす
   unit-pass の gain で peak 成分をスケールする。
2. `scipy.signal.welch`（固定 `nperseg`。データに依存しないため決定論を
   壊さない）で PSD を測り、(a) 宣言 center 近傍（`center_hz` ±
   `resonance_bandwidth_hz`/2）の peak レベルを **混合信号**上で、
   (b) spectral floor レベルを **floor 成分単独**上で測定する。

   floor を「混合信号上で center 近傍を除外した帯域」から測ろうとする素朴な
   実装は、このモジュールが使う 2-pole `iirpeak` resonator の skirt
   （roll-off）が Q（`= center_hz / resonance_bandwidth_hz`。本 registry の
   グリッドでは最大 70 に達する高 Q）に対して緩やかなため機能しない
   — 実測では center から ±32*bandwidth 離れた窓でも、真の noise floor より
   なお 5–20dB 高い（`resonance_bandwidth_hz` が小さいほど悪化する。実測は
   本ファイルのレビュー記録を参照）。floor 成分は加算前の broadband white
   noise そのもの（設計上スペクトル的にフラットで center に依存しない）
   であるため、その PSD を直接測ることが「skirt に汚染されない spectral
   floor」の最も直接的かつ頑健な参照になる。
3. 実現された prominence（`peak_db - floor_db`）と宣言値との不足分
   （shortfall, dB）を計算し、`10**(shortfall/20)` を peak 成分へ追加で
   一括乗算する（測定 → 補正の 1 パスのみ。確率的な繰り返し探索なし）。

**declared SNR nuisance noise の折り込み**（Codex レビュー 2026-09-01 P1）:
confound 行に declared `noise_snr_db` が設定されている場合、`common.
finalize()` は通常この noise を本補正パスの**後**（gain・context 適用後）に
加える。そのため較正時に想定した floor と最終 PCM の floor が食い違い、
noise 軸の confound 行では実現 prominence が declared 値を下回っていた。
本実装は `_nuisance_noise_component()` でこの noise を解析的に事前生成し、
floor 測定（上記 2(b)）より前に折り込む。gain 適用は mixed signal 全体への
単一スカラー倍（peak 正規化 + 一定倍率）であり相対 dB 比を保存するため、この
pre-gain 折り込みで最終 PCM 上の宣言 SNR・宣言 prominence の両方が整合する
（`render()` 側は `common.finalize()` の noise 適用を skip させ、二重適用を
防ぐ。`[UNDERSPEC-CAL-C15]`）。

**nuisance noise の基準信号は CORRECTED clean signal**（Codex レビュー
2026-09-01 P1 #2）: nuisance の振幅は「宣言 SNR = clean signal power /
noise power」から逆算するため、基準に使う "clean signal" は実際に出荷される
peak 成分（= one-shot prominence 補正後の `scaled_peak * correction`）でな
ければならない。しかし correction 自身は floor（= nuisance を含む）を測定し
て決まるため、素朴には「correction を決めるには nuisance が要り、nuisance
を決めるには correction が要る」循環になる。本実装はこれを 2 パスの決定論的
手続きで断ち切る（反復探索ではなく固定 2 パス。**one-shot** の思想を維持）:

1. **Pass 1（nuisance-free で correction を確定）**: nuisance を一切折り込ま
   ない「純粋な」`floor + scaled_peak`（context 適用後）で prominence を測定
   し、`correction` を求める。
2. **nuisance を CORRECTED clean signal 基準で導出**: `_nuisance_noise_
   component()` の基準信号を `floor + scaled_peak * correction`（pass 1 で
   確定した corrected peak）に差し替える。これにより宣言 SNR は実際に出荷
   される peak 成分の実パワーを基準に計算され、`correction != 1` の行でも
   realized SNR が declared SNR から乖離しなくなる（旧実装は pre-correction
   の `floor + scaled_peak` を基準にしており、この乖離が finding の本体
   だった）。
3. **Pass 2（re-verify）**: nuisance を折り込んだ `combined_floor` は local
   spectral floor を僅かに押し上げうる（nuisance 自身が floor のパワーへ
   加算されるため）。そのため同じ one-shot 補正をもう一度だけ、今度は
   `combined_floor + corrected_peak` を基準に適用し、prominence を再検証
   する（pass 1 と同じ手続きを nuisance 込みでもう一度呼ぶだけであり、
   correction が再び nuisance に依存する循環を生まない — pass 2 の
   `correction2` は nuisance の再計算をトリガーしない）。

**残差誤差と U_GT への寄与**: pass 2 の `correction2` は「nuisance 折り込みに
よる floor のわずかな上昇」のみを吸収する残差補正であり、これを peak へ再乗
算すると、pass 1 で `corrected_peak` 基準に較正した nuisance の実パワーと、
最終的な peak 実パワー（`corrected_peak * correction2`）との間に小さな
ミスマッチが生じる（`correction2` は 1 に極めて近いため、通常このミスマッチ
は無視できるほど小さい）。この残差は上記「近似誤差と U_GT への寄与」節と同様
に `RESONANCE_GT` truth の `U_GT`（generator truth の保守上限）へ計上すべき
成分である（実測は `tests/test_generators.py` の SNR confound 回帰テストで
declared SNR ±0.5dB として検証される）。

**近似誤差と U_GT への寄与**: この 1-パス補正は「peak 窓内の PSD レベルは
floor 成分の寄与を無視できるほど peak 成分が支配的である」という線形近似に
基づく（peak 成分をスケール `c` 倍すると、peak 窓内のパワーはおおむね `c**2`
倍になる、という前提。floor 成分自身が peak 窓内にも常に存在するため、この
前提は宣言 prominence が小さい＝floor 寄与が相対的に無視できないほど、また
`welch` の周波数分解能が粗い（narrow bandwidth を数ビンでしか解像できない）
ほど劣化する）。この残差は `RESONANCE_GT` truth の `U_GT`（generator truth
の保守上限）へ計上すべき成分である（`tests/test_generators.py` の帯域端
(50Hz/300Hz, declared 12dB) 検証で実測される許容誤差 ±1.5dB が、この近似
誤差の実測上限の目安）。

**context nuisance を較正パスへ折り込む**（Codex レビュー 2026-09-01 P1）:
上記 1-パス補正パスは元々 `context`（cosine-ramp / voiced-prefix-suffix /
transition-adjacent）適用**前**の core のみを測定していた。`common.
finalize()` は gain → context → noise の順で適用するため（noise は上記の
通り既に事前折り込み済み）、実際に出荷される最終 PCM は較正時に測定した
core とは異なる波形（context 適用後）になる。特に `20ms-cosine-ramp` は
core 自身のサンプル値を in-place でテーパーするため、較正時に想定した
floor/peak レベルと最終 PCM のそれが食い違いうる。

修正: `_core()` は `combined_floor` と `combined_floor + scaled_peak`（unit
gain）の両方に `common.assemble_context()` を適用してから測定する
（**gain は測定チェーンに含めない** — `apply_gain_dbfs` は入力を peak=1.0
へ独立に再正規化するため、floor-alone と mixed のそれぞれに別々に適用すると
無関係なスケール差を持ち込んでしまう。gain は core 全体への単一スカラー倍
であり dB 差 (peak-floor) を保存するため、測定チェーンから除外しても補正の
正しさは変わらない）。

**"steady" core 区間への測定窓の限定**: `100ms-voiced-prefix/suffix` と
`transition-adjacent` は core の前後に無関係な別波形（voiced tone）を
**連結**するだけで core 自身のサンプル値は書き換えない。これら 2 context
では、prefix/suffix・adjacent tone の高調波が declared `center_hz` ±
`bandwidth_hz/2` の peak 窓や floor 測定域に紛れ込みうるため、
`_context_core_bounds()` で core の "steady" 区間（宣言 truth が実際に対象と
する区間）のオフセットを求め、その区間だけを切り出してから測定する
（`steady-isolated` / `20ms-cosine-ramp` は core の長さを context が変えない
ため区間は core 全体のまま）。連結型 context ではこの限定により measurement
は「context 適用前の core を直接測る」のと数値的に等価になる（連結は core
自身の値を変えないため）— つまり、この限定を課さないと prefix/suffix/
adjacent の高調波混入で補正が却って壊れる。一方 `20ms-cosine-ramp` は core
自身が書き換わるため、区間全体（テーパー込み）を測ることで較正が実際に
出荷される波形を正しく反映するようになる。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.signal import iirpeak, lfilter, welch

from voice_genesis.calibration.fixtures.generators import common

_WELCH_NPERSEG_CAP = 2048
_PSD_FLOOR_EPS = 1e-20


def _welch_psd_db(x: np.ndarray, sr_hz: int) -> tuple[np.ndarray, np.ndarray]:
    """`(freqs, psd_db)` を固定 `nperseg`（データ内容に依存しない、`len(x)` と
    `_WELCH_NPERSEG_CAP` のみで決まる）で返す。決定論契約: 同一 `row`+seed
    → 同一 `len(x)` → 同一 `nperseg` → 同一 PSD grid。"""
    nperseg = int(min(len(x), _WELCH_NPERSEG_CAP))
    freqs, psd = welch(x, fs=sr_hz, nperseg=nperseg, window="hann", detrend=False)
    return freqs, 10.0 * np.log10(psd + _PSD_FLOOR_EPS)


def _measure_peak_db(mixed: np.ndarray, sr_hz: int, center_hz: float, bandwidth_hz: float) -> float:
    """`center_hz` ± `bandwidth_hz/2` 内の PSD 最大値（混合信号上で測る、
    実際に rendered される局所ピークレベル）。"""
    freqs, psd_db = _welch_psd_db(mixed, sr_hz)
    half_bw = bandwidth_hz / 2.0
    peak_mask = np.abs(freqs - center_hz) <= half_bw
    if not peak_mask.any():
        peak_mask = np.zeros_like(freqs, dtype=bool)
        peak_mask[int(np.argmin(np.abs(freqs - center_hz)))] = True
    return float(psd_db[peak_mask].max())


def _measure_floor_db(floor: np.ndarray, sr_hz: int) -> float:
    """floor 成分（broadband white noise, peak 混合前）単独の PSD 中央値。
    設計上スペクトル的にフラットなので、center 近傍に限定する必要がない
    （= 全帯域の中央値がそのまま local floor の頑健な推定になる。モジュール
    docstring 参照: 混合信号上の近傍窓から測ろうとすると resonator skirt に
    汚染される）。"""
    nyquist = sr_hz / 2.0
    freqs, psd_db = _welch_psd_db(floor, sr_hz)
    valid = (freqs >= 50.0) & (freqs <= nyquist * 0.95)
    return float(np.median(psd_db[valid]))


def _nuisance_noise_component(
    row: object, rng: np.random.Generator, reference_signal: np.ndarray
) -> np.ndarray | None:
    """declared `noise_snr_db` の nuisance noise 成分を、`common.add_noise_at_snr`
    が使う式と同一の SNR 式で解析的に事前生成する（`[UNDERSPEC-CAL-C15]`。Codex
    レビュー 2026-09-01 P1）。

    **バグの所在**: `common.finalize()` は通常この noise を **prominence 較正の
    後**（gain・context 適用後）に加える。そのため較正時に想定した spectral
    floor と、最終 PCM に実際に現れる floor が食い違い、confound 行（declared
    SNR 付き）では最終 PCM 上の実現 prominence が declared 値を下回っていた。

    **修正の algebra**: `common.apply_gain_dbfs` は mixed signal 全体（floor +
    peak 成分）への**単一スカラー倍**（peak 正規化 + 一定倍率）であり、
    成分間の相対 dB 比を保存する。したがって nuisance noise をこの pre-gain
    スケールで生成し `floor` へ折り込んでおけば、後段の gain 適用・PCM 量子化
    を経ても、宣言 SNR と宣言 prominence の両方が最終 PCM 上で整合する
    （`render()` 側で `common.finalize()` の重複 noise 適用を止める必要がある
    — 二重適用こそが元のバグだった）。

    ここでの `reference_signal` は `common.add_noise_at_snr` が通常参照する
    「noise 適用直前の signal」に相当する pre-gain 版のプロキシである。gain は
    後段で mixed signal 全体へ均一に掛かるスカラーに過ぎないため、この
    pre-gain スケールで SNR を合わせておけば最終 PCM 上の SNR も宣言値に
    一致する。

    **`reference_signal` は CORRECTED clean signal を渡すこと**（Codex レビュー
    2026-09-01 P1 #2）: prominence 較正の `correction` 係数は peak 成分の
    実パワーを変える（module docstring の one-shot 近似誤差 ±1.5dB 相当の
    残差補正）。宣言 SNR は「実際に出荷される peak 成分の実パワー」を基準に
    定義されるため、呼び出し側は `floor + scaled_peak`（pre-correction）で
    はなく `floor + scaled_peak * correction`（post-correction）を
    `reference_signal` として渡さなければならない。`correction` を決めるには
    floor（= nuisance を含む floor）の測定が要り、nuisance を決めるには
    `correction` が要る、という素朴な循環は、呼び出し側 (`_core`) が
    「nuisance-free で `correction` を確定 → その `corrected_peak` を基準に
    nuisance を導出」という固定 2 パスの手順で断ち切る（`_core` の実装・
    module docstring「nuisance noise の基準信号は CORRECTED clean signal」
    参照。本関数自体はどちらの `reference_signal` が渡されても同じ SNR 式を
    適用するだけで、循環を断つロジックは持たない）。

    `context` 系列（20ms-cosine-ramp 等）由来で `core` 配列長が伸びる行との
    組合せは、正準 nuisance 系列が「1 行につき 1 軸のみ変更」（`axes.
    CANONICAL_NUISANCE_SEQUENCE`）のため RESONANCE_GT の固定 456-cell 行列には
    現れない（noise 軸と context 軸が同一行で同時に変わる行が存在しない）。
    """
    if row.noise_clean or row.noise_snr_db is None:
        return None
    signal_power = float(np.mean(np.square(reference_signal)))
    if signal_power <= 0.0:
        return None
    raw = rng.standard_normal(reference_signal.size)
    raw_power = float(np.mean(np.square(raw)))
    if raw_power <= 0.0:
        return None
    target_noise_power = signal_power / (10.0 ** (row.noise_snr_db / 10.0))
    scale = np.sqrt(target_noise_power / raw_power)
    return raw * scale


def _context_core_bounds(context: str, sr_hz: int, n_core: int) -> tuple[int, int]:
    """`common.assemble_context()` が組み立てる文脈付き波形のうち、
    RESONANCE_GT truth が実際に対象とする "steady" core 区間の
    `(start, length)` サンプルオフセットを返す（module docstring の
    「"steady" core 区間への測定窓の限定」参照）。

    - `steady-isolated` / `20ms-cosine-ramp`: context は core の長さを変えず
      (`20ms-cosine-ramp` は core を in-place でテーパーするのみ)、core 全体が
      そのまま steady 区間。
    - `100ms-voiced-prefix/suffix`: prefix（100ms 分のサンプル）の直後、
      suffix の直前が core。
    - `transition-adjacent`: adjacent tone は core の**後ろ**に連結される
      だけなので core は先頭のまま。
    """
    if context in ("steady-isolated", "20ms-cosine-ramp"):
        return 0, n_core
    if context == "100ms-voiced-prefix/suffix":
        n_prefix = common.n_samples(0.100, sr_hz)
        return n_prefix, n_core
    if context == "transition-adjacent":
        return 0, n_core
    raise ValueError(f"unknown context level: {context!r}")


def _prominence_correction(
    row: object,
    sr_hz: int,
    center: float,
    bandwidth: float,
    start: int,
    length: int,
    floor_component: np.ndarray,
    peak_component_: np.ndarray,
) -> float:
    """`floor_component + peak_component_` を context 適用後の steady core
    区間で測定し、宣言 prominence へ合わせる one-shot 補正係数を返す（module
    docstring「prominence 実現則」参照）。"""
    context_floor = common.assemble_context(
        floor_component, sr_hz=sr_hz, context=row.context, f0_hz=row.f0_hz
    )
    context_mixed = common.assemble_context(
        floor_component + peak_component_, sr_hz=sr_hz, context=row.context, f0_hz=row.f0_hz
    )
    steady_floor = context_floor[start : start + length]
    steady_mixed = context_mixed[start : start + length]

    floor_db = _measure_floor_db(steady_floor, sr_hz)
    peak_db = _measure_peak_db(steady_mixed, sr_hz, center, bandwidth)
    shortfall_db = float(row.prominence_db) - (peak_db - floor_db)
    return 10.0 ** (shortfall_db / 20.0)


def _core(row: object, rng: np.random.Generator) -> np.ndarray:
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    floor = rng.standard_normal(n)

    center = row.center_hz
    bandwidth = row.resonance_bandwidth_hz
    nyquist = sr_hz / 2.0
    w0 = float(np.clip(center / nyquist, 1e-4, 0.999))
    q = max(center / bandwidth, 0.5)
    b, a = iirpeak(w0, q)
    peak_component = lfilter(b, a, rng.standard_normal(n))

    floor_rms = common.rms(floor) or 1e-9
    peak_rms = common.rms(peak_component) or 1e-9
    target_ratio = 10.0 ** (row.prominence_db / 20.0)
    scaled_peak = peak_component * (target_ratio * floor_rms / peak_rms)

    start, length = _context_core_bounds(row.context, sr_hz, n)

    # Pass 1（nuisance-free）: one-shot 補正パス（測定 → 不足分算出 → 1 回だけ
    # 追加乗算。反復なし）。measurement は `context` 適用後の "FULLY
    # FINALIZED" レンダリング（gain は測定チェーンに含めない — module
    # docstring 参照）の steady core 区間で行う。ここではまだ nuisance noise
    # を折り込まない（`correction` が `nuisance` に依存し、`nuisance` の基準
    # 信号が `correction` に依存する循環を避けるため。module docstring
    # 「nuisance noise の基準信号は CORRECTED clean signal」参照）。
    correction = _prominence_correction(
        row, sr_hz, center, bandwidth, start, length, floor, scaled_peak
    )
    corrected_peak = scaled_peak * correction

    # declared-SNR nuisance noise は CORRECTED clean signal（floor +
    # corrected_peak）を基準に折り込む（Codex レビュー 2026-09-01 P1 #2:
    # 従来は pre-correction の floor+scaled_peak を基準にしており、
    # `correction != 1` の行では realized SNR が declared SNR から乖離して
    # いた）。
    nuisance = _nuisance_noise_component(row, rng, floor + corrected_peak)
    combined_floor = floor if nuisance is None else floor + nuisance

    # Pass 2（re-verify）: nuisance を折り込んだ floor は local spectral
    # floor を僅かに押し上げうるため、同じ one-shot 補正をもう一度だけ
    # `combined_floor` 基準で適用し prominence を再検証する（固定 2 パスの
    # 決定論的手続きであり反復探索ではない。nuisance の再計算はトリガー
    # しない — 残差は module docstring「残差誤差と U_GT への寄与」参照）。
    correction2 = _prominence_correction(
        row, sr_hz, center, bandwidth, start, length, combined_floor, corrected_peak
    )
    final_peak = corrected_peak * correction2

    mixed = combined_floor + final_peak
    return common.peak_normalize(mixed)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row, rng)
        # declared-SNR nuisance noise（あれば）は `_core`/
        # `_nuisance_noise_component` が既に折り込み済みなので、
        # `common.finalize()` に二重で足させない（元のバグの本体）。
        finalize_row = row if row.noise_clean else replace(row, noise_clean=True)
        return common.finalize(core, finalize_row, rng)
    return common.finalize(core, row, rng)
