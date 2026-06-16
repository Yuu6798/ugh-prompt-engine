# Metamorphic Probe — 決定論パイプラインの grip/校正/直交性を自動掃引する計器

`scripts/metamorphic_probe.py` + `tests/test_metamorphic_probe.py`。

## 思想：研究課題＝メタモルフィック関係

本リポジトリの中心研究課題は、いずれも「**入力をこう変えたら出力はこう変わるはず**」
という *関係（metamorphic relation）* の検証に還元できる:

| 研究課題 | メタモルフィック関係 |
|---|---|
| **grip**（ツマミは効くか） | ノブ↑ ⟹ センサー出力が単調に動く |
| **calibration**（センサーは正確か） | 入力値 ≈ 検出値（既知 ground-truth との比） |
| **orthogonality**（ノブは独立か） | ノブ A を回しても センサー B は不変 |
| **determinism**（同一入力→同一出力） | 同一 spec → 同一 RPE |

正解値を全入力に用意できない音声領域では、個別の golden 値より *関係* のほうが
広く検証できる。これは K0/K1 grip ハーネスを手書き fixture から実掃引へ一般化したもの。

## 計器の構成

`generate_synth_samples.render_sample`（パラメータ→決定論的合成）と実
`extract_physical_from_file` を繋ぎ、ノブを掃引して実応答を計測する。
**pass/fail を下す「裁判官」ではなく、grip 曲線・センサー盲点・校正誤差を記録する
「制御盤（計器）」**として働く（audit と同じ no-verdict 思想）。

- `build_spec(key, mode, bpm, brightness_level, ...)` — golden サンプルをテンプレに
  借り、連続ノブ（bpm / brightness=倍音richness）だけ書き換えて spec を組む
- `synth_extract(spec)` — 合成 → 一時 WAV → 実 extract（golden path と同一経路）
- `sweep_brightness` / `sweep_bpm` / `build_report` — 掃引と知見レポート生成
- `physical_invariants` / `grip_summary` — 不変条件点検・grip 要約（純関数、高速）

```bash
python scripts/metamorphic_probe.py                 # Markdown レポート
python scripts/metamorphic_probe.py --json          # JSON
python scripts/metamorphic_probe.py --out report.md # ファイル出力
python -m pytest tests/test_metamorphic_probe.py    # プロパティ検証
```

## 検証するメタモルフィック関係（Hypothesis）

実 extractor を回すため `derandomize=True` で CI 決定論化、`max_examples` を絞る。
合成器が追従しない bpm *校正* は pass/fail にせず、robust な関係のみ assert する。

1. **centroid grip + key 直交性** — brightness_level↑ ⟹ `spectral_centroid` 単調非減少
   かつ span>5Hz（tight grip）。同時に検出 key は不変（brightness は key に漏れない）。
2. **centroid ⊥ bpm** — tempo を 90→140 に振っても centroid のずれは grip 幅より遥かに小。
3. **determinism** — 同一 spec → 同一 centroid/bpm/key/brightness。
4. **不変条件** — brightness∈[0,1]、各帯域比∈[0,1]、centroid>0、各 confidence∈[0,1]。
5. **回帰ガード（計測知見）** — `test_brightness_high_band_is_blind_for_synth`。

## 計測された設計知見（G major, 2026-06-16）

### brightness 掃引（ノブ＝倍音 richness）

| level | centroid(Hz) | brightness | high_ratio | mid_ratio |
|---|---|---|---|---|
| 0.0 | 839.4 | 0.0 | 0.0 | 0.838 |
| 0.5 | 917.0 | 0.0 | 0.0 | 0.891 |
| 1.0 | 961.3 | 0.0 | 0.0 | 0.910 |

- **`spectral_centroid` は tight grip**（span≈122Hz, 単調）。
- **高域比 `brightness` センサーは合成器レンジで盲**（high_ratio≡0）。合成器の基音は
  <4kHz に留まり >4kHz 帯を駆動できないため。**「ツマミ死」ではなく「センサー盲」**
  — 同一ノブに対し centroid は応答し brightness は応答しない、という判別が一掃引で出る
  （K1 で個別発見した区別を一般化して再現）。

### bpm 掃引（ノブ＝tempo, 36s）

| in_bpm | det_bpm | ratio | octave_ambiguous |
|---|---|---|---|
| 70 | 172.3 | 2.46 | False |
| 100 | 99.4 | 0.99 | False |
| 130 | 129.2 | 0.99 | False |
| 160 | 161.5 | 1.01 | False |

- 100/130/160 は±1%で良好追従するが、**70 BPM は 172 へオクターブ誤検出**。
  合成器の bpm grip は不安定で、bpm 校正の絶対精度検証は実音源（R1-audio）が必須
  という既存結論（2026-06-16 メモ）を独立に再現。
- **重要な新知見**: 70→172 のオクターブ誤検出を `octave_ambiguous`（R2-2a 半折り検出器）
  が**フラグできていない**。R2-2a は onset 自己相関の「×2 subdivision lag」比に基づくため、
  この合成ケース（基本周期そのものが倍に取られる型）はカバレッジ外。検出器の実効力は
  この型では発火しない、という具体的限界が掃引で表面化した。

## 限界と次の一手

- **合成器が叩けないノブは計測不能**: 高域 brightness（>4kHz）・実 bpm 病理（Suno の
  89.1 アトラクタ）は合成では再現不可（2026-06-16 で実証済み）。これらは R1-audio 待ち。
  本計器は「合成器で叩けるノブ」の grip/直交性に有効、と帯域を自覚して使う。
- **拡張候補**: (a) brightness センサーを centroid 基準に正規化済みなので、高い基音
  テンプレを足せば高域帯も掃引可能。(b) R2-2a 検出器のカバレッジ外ケース
  （基本周期倍取り）を別途検出する是非を Design Memo 化（実音源で実効力を確認後）。
- **コスト**: 実抽出はウォーム ~1.5–2.5s/件。Hypothesis 群は `max_examples` を 3–4 に
  絞り総計 ~70s。CI 予算に応じてさらに絞れる。
