# VG-L0 制御軸表現力の実測（2026-08-20）

> **STATUS: accepted**（2026-08-20 昇格。PR #289 マージ + フォローアップ PR で
> 7/9 巡目の残指摘を反映済み）。§8 の昇格フロー
> `experiment → self-review → PR review → accepted record → STATUS` を完了した。
>
> **accepted の射程**: §0 の三分類（確定 / 未判定 / 撤回）**そのものが accepted**
> であって、「全部が確定した」ではない。U1–U6 は未判定のままで、他セッションが
> 確定事項として採用してよいのは §0-1 の C1–C7 だけ。§1 の checkpoint 逸脱の
> 限定（run 6 代替が許されるのは入力 I/F・介入点の探索まで）も accepted の
> 一部として持ち回ること。

- 位置づけ: [`../DESIGN_VG_L0.md`](../DESIGN_VG_L0.md) §6 の**実装第 1 タスク**
  = 「現行 gate_synth の入力でどこまで表現できるかの実測」の実施記録。
  最小実験契約の残り（制御プロファイル schema・学習遷移台帳・稽古 1 遷移）は
  本実測の結果と §5 の BLOCKER 解決を前提に設計する
- 実行体制: 設計・実測 = Claude（Fable）/ 耳判定は本実測では**行っていない**
  （本記録は機械実測のみ・耳判定 0 件）
- 費用: **GPU ゼロ**（CPU 推論のみ）
- 実測資産（本 PR でコミット）:
  - probe 実装 [`../probes/vgl0_control_axis_probe.py`](../probes/vgl0_control_axis_probe.py)
  - 独立プロセス再現性検査 [`../probes/vgl0_reproducibility_check.py`](../probes/vgl0_reproducibility_check.py)
  - 結果 [`vgl0_control_axis_probe_result.json`](vgl0_control_axis_probe_result.json)
    （主実測 `notes_limit=8`）/ [`_n6`](vgl0_control_axis_probe_result_n6.json)
    / [`_n10`](vgl0_control_axis_probe_result_n10.json)（フレーズ境界揃え・§3.6）
    / [`vgl0_render_reproducibility_result.json`](vgl0_render_reproducibility_result.json)
  - pin 整合の自動検査 [`../tests/test_vgl0_probe_result_pins.py`](../tests/test_vgl0_probe_result_pins.py)
    — 結果 JSON が pin する probe sha とコミット済み probe の実 sha の一致を CI で検査する
    （「probe を編集したが結果を再生成していない」fixture drift の検知。本 PR の作業中に
    実際に 1 度発生し、この検査で捕捉した）

## 0. 結論の三分類（確定 / 未判定 / 撤回）

本記録は初版・2 巡目から**中核主張を 2 度撤回している**。読み違いを防ぐため
結論を三分類で先に置く。詳細な経緯は §7。

### 0-1. 確定（本実測が根拠を与えるもの）

| # | 確定事項 | 根拠 |
|---|---|---|
| C1 | **`consonant_duration_scale`（子音時間配分）は決定論的な制御軸として成立する** — 独立プロセス間 (4/4) でも実行順反転 (17/17) でも WAV sha256 が一致（計 10 プロセス・verdict PASS） | §3.1 / §3.2 |
| C2 | **制御量（子音フレーム比）は入力係数どおり単調**（0.0562 → 0.4167） | §3.3 |
| C3 | **フレーズ間ブレスは「尺」としては厳密に制御できる** — 挿入フレーム数と総尺増加が完全一致（誤差 0） | §3.2 |
| C4 | **フレーズ終端ノートの伸長も尺としては厳密**（`対象ノート数 × 144 フレーム × (scale − 1) × 11.61 ms` と一致。非終端ノートは不変）。フレーズ境界で切った `notes_limit=6/10` でも同一規則を確認 | §3.2 / §3.6 |
| C5 | **音素長の総和保存は 3 走行 (notes_limit 8/6/10) の全 51 条件で成立**し、`ph_dur < 1` は 0 件 | §3.4 |
| C6 | **gate_synth はフレーズ情報を受け取れない** — `ScoreNote` の `phrase_index` / `is_phrase_final` を `_NoteWithMs` が写さない（`gate_synth.py:1045-1053`）。本実測はこのギャップを probe 側で埋めている | §2 |
| C7 | **`notes_limit=8` のさくらの最終ノートはフレーズ終端ではない**（フレーズ 2「やよいの」の 2 音目・`is_phrase_final=false`）。機械実測で確認 | §2 |

### 0-2. 未判定（材料が足りず、どちらとも言えないもの）

| # | 未判定事項 | 何が足りないか |
|---|---|---|
| U1 | **フレーズ間ブレスが「ブレスとして」成立するか** — 尺は厳密だが、SP 区間が実際に無音としてレンダリングされるかは**境界によって割れた**（片方 −26 dB、もう片方 +9.8 dB） | 音響レベルの評価軸 + 耳判定 |
| U2 | **音響応答の単調性** | 適切な評価軸（RMS は粗すぎる）+ 耳判定 |
| U3 | **軸の直交性** | 併用条件でブレスの音響実現が変わっており（§3.2）、独立とは言えない |
| U4 | **可動範囲（破綻閾値）** | 聴取評価・他曲での構造検査 |
| U5 | **SB0 の発動可否** | グリッド解釈の確定・水準数・順位付け形態（§4） |
| U6 | **`consonant_duration_scale` が知覚的な「アタック強度」に対応するか** | 校正実験。**未校正のまま知覚的な意味名を付けない**（§5-6） |

### 0-3. 撤回（過去の版で主張し、取り下げたもの）

| 版 | 撤回した主張 | 理由 |
|---|---|---|
| 初版 | 音響応答は**非単調** | ピーク 0.6 正規化後の RMS を見た artifact（§7） |
| 初版 | 選抜（耳の順位付け）方式の妥当性が実測で裏づけられた | 上記の帰結 |
| 初版 | SB0 発動条件 = 18 ペア vs 3 セッション | 水準数・グリッド解釈・ペア定義のいずれも根拠のない読み替え |
| 2 巡目 | **候補 3 軸すべてが可動** | **ブレス軸と終端軸の定義が誤っていた**（§2-2）。測っていたのは「曲全体の無音パディング」と「最後にレンダリングしたノートの伸長」で、フレーズ軸ではない |
| 2 巡目 | 決定論 PASS（Render Reproducibility） | **同一プロセス内の反復**でしか確認していなかった。独立プロセス検証を追加して測り直した（§3.1） |
| 2 巡目 | 制御軸名「アタック強度」 | 実際に操作したのは子音時間配分。未校正の知覚名を canonical にしない（§5-6） |

## 1. 実測条件と pin

| 項目 | 値 |
|---|---|
| acoustic | `s5_run6_acoustic_v1.onnx` sha256 `aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706` |
| 話者 embed | `s5_run6_acoustic_v1.ritsu.emb` sha256 `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c` |
| canon phonemes | `NamineRitsu_DiffSinger/phonemes.txt` sha256 `1489af3c4806ad2cfc10e663ec27a1bf7c6bf0d6f9a047263948c5cbe36eebfb` |
| **gate vocoder（配布物 pin）** | `nsf_hifigan.oudep` sha256 `e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830`（`s1_gate/README.md` §0 の #2 が正） |
| **gate vocoder（実際に推論へ渡ったバイト列）** | `.oudep` を展開した `nsf_hifigan.onnx` sha256 `a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4` |
| canon 系 onnx | `linguistic` `1c9ec9f6…` / `dsdur/dur` `11bbfad5…` / `dspitch/pitch` `e361ad13…` |
| 楽譜 | `singer/score.py` `24a6004daeb0b5d0…` / 依存 `phoneme_jp.py` `9cbc7e1d34771bb0…`（gate_synth 自身の `load_song_module` provenance 機構から取得） |
| gate_synth 本体 | `gate_synth.py` sha256 `423c4d81fff39d51…` |
| probe 実装 | `vgl0_control_axis_probe.py`（**本 PR でコミット**・sha は結果 JSON の `pins.probe_script`） |
| 曲 | さくら 先頭 8 ノート（`notes_limit=8`・音素 16 個・基準総尺 8.5449 s） |
| 実行環境 | python 3.11.15 / numpy 1.26.4 / onnxruntime 1.29.0（CPUExecutionProvider）/ soundfile 0.14.0 / Linux x86_64 |

**vocoder pin の訂正（正直会計）**: 2 巡目は「canon / vocoder は run 5 と同一・
pin は `results_s3/run5_material_pins.json`」と書いていたが、**これは別資産の
pin を指していた**。`run5_material_pins.json` の vocoder エントリは
`vocoder_pc_nsf_hifigan`（openvpi `pc_nsf_hifigan_44.1k_hop512_128bin_2025.02`
の `model.ckpt`）で、**DiffSinger 学習側**の vocoder である。同ファイル自身も
行 50 で「判定材料合成のローカル ONNX vocoder とは別物 — `gate_synth.py` の
`nsf_hifigan.onnx` は S1 以来のローカル資産」と明記している。

gate 用 vocoder の一次 pin は `s1_gate/README.md` §0 の #2（配布物
`nsf_hifigan.oudep`）が正。本記録は**配布物 pin と展開後 onnx の sha を併記**
する — 配布物 pin は入手の検証に、展開後 onnx の sha は**実際に推論へ渡った
バイト列**の同定に要るため。結果 JSON も後者を `pins.vocoder_onnx` に記録する。

**checkpoint の逸脱と、その射程（正直会計）**: DESIGN_VG_L0 §6/§8 は前提を
**run 5 checkpoint** と pin しているが、本実測は **run 6 の 40K export** を
使った（回収済みで手元にあり CPU 合成の実績もあるため）。**この代替が許される
射程は「入力インタフェースの探索」に限る** — すなわち「どの入力を触れば出力が
動くか」「決定論が成立するか」「不変条件が破れないか」までであって、
**音質・知覚・可動範囲に関する結論は checkpoint 依存**なので本記録からは
持ち出せない。稽古 1 遷移の本実験は DESIGN の pin どおり run 5 checkpoint で
行うか、pin を正式に改訂してから行う（次段で裁定）。

**実装形態（正直会計）**: 本実測は `gate_synth.py` を**無改変**のまま
monkeypatch で介入している（製品コードではない）。monkeypatch は
provenance 上の穴を持つため、その扱いは §5-2 に BLOCKER として記載する。

## 2. 介入点の特定

### 2-1. 合成パイプラインの段構成

`run_pipeline` は **4 段**（linguistic → duration → pitch → acoustic →
vocoder。linguistic は duration 用と pitch 用に**2 回**呼ばれ、独立した
1 段ではない）。

| 触った入力 | 介入点 | 総尺への影響 |
|---|---|---|
| `consonant_duration_scale` | duration 予測 `ph_dur_pred` の後段に係数（子音音素のみスケール） | 不変（8.5449 s） |
| フレーズ間ブレス | `build_inputs` 出力の内部フレーズ境界へ **SP ノートを挿入** | 伸びる |
| フレーズ終端伸長 | `build_inputs` 出力の**フレーズ終端ノート**の `note_target_frames` をスケール | 伸びる |
| （参考）曲頭/曲尾パディング | モジュール定数 `HEAD_FRAMES` / `TAIL_FRAMES` | 伸びる |

**総尺不変は「軸の性質」ではなく実装位置の帰結**: `run_pipeline` は
`ph_dur_pred` を**ノート目標長へ per-note で rescale する**ため、その
**前**に介入すればノート長は保存される。

**波及範囲の注意**: 子音時間配分の介入は duration 段の出力を変えるため、
下流の pitch 予測（`ph_dur` を入力に取る）にも波及する。「子音/母音の
配分だけが動く」は過小記述で、実際は**配分の変化を通じて pitch 曲線も
変わりうる**。これは §5-6 の命名問題（機構名と知覚名の分離）の直接の理由でもある。

### 2-2. 2 巡目のフレーズ軸定義が誤っていた（撤回の中身）

2 巡目は「ブレス位置」を `HEAD_FRAMES`/`TAIL_FRAMES`、「フレーズ終端処理」を
`note_target_frames[-1]` で代表させたが、**いずれもフレーズ軸ではなかった**:

- `HEAD_FRAMES` / `TAIL_FRAMES` は `ph_dur2 = [HEAD_FRAMES] + final_phone_dur
  + [TAIL_FRAMES]`（`gate_synth.py:1174`）および `note_dur_raw`（同 `:1187`）
  として使われる、**曲全体のトークン列の前後に足す SP パディング**。
  フレーズ間のブレスではなく、曲頭・曲尾の無音長である。
  実測でも当該区間の RMS は 0.00317 / 0.00739（曲全体 0.06949）で、
  単なる無音であることが確認できる
- `notes_limit=8` のさくらでは最終ノートは**フレーズ 2「やよいの」の 2 音目**
  （`phrase_index=2` / `is_phrase_final=false`）。機械実測の
  `phrase_structure.is_phrase_final_per_note` =
  `[false,false,true,false,false,true,false,false]` がそれを示す。
  つまり `note_target_frames[-1]` の伸長は「最後にレンダリングしたノートを
  伸ばす」であってフレーズ終端処理ではない

本版は `ScoreNote.phrase_index` / `is_phrase_final` から**実際のフレーズ境界**
（内部境界 = ノート index 2 と 5）を取り、測り直した。誤っていた 2 条件も
`song_pad_head40` / `song_pad_tail40` / `last_note_x1.5` へ**改名して残す**
（何を測っていたのかを名前で正すため）。

### 2-3. gate_synth のフレーズ情報配線ギャップ

`ScoreNote` は `phrase_index` / `is_phrase_final` を持つ（`score.py:40-46`）が、
`gate_synth._NoteWithMs`（`gate_synth.py:1045-1053`）は
`midi` / `mora` / `_dur_ms` の**3 つしか写さない**。したがってフレーズ情報は
DiffSinger 経路へ一切届かない（`grep -c 'phrase_index\|is_phrase_final'
gate_synth.py` = 0）。

本実測は probe 側でこのギャップを埋めた（`build_inputs` の出力を
フレーズ境界情報つきで書き換える）。**これは製品実装の設計ではない** —
正しい解決は gate_synth 専用パッチではなく、共通中間表現への持ち上げ
（§5-4 BLOCKER）である。

## 3. 実測結果

### 3.1 決定論 — 2 つの別概念を分けて扱う

DESIGN_VG_L0 §6 は **Profile Transition Determinism**（同一 r0 + 同一決定 →
同一 r1）と **Render Reproducibility**（同一 r1 → 同一 WAV）を分離し、
前者の PASS を後者の PASS とみなすなと明記している。**本記録が扱えるのは
後者だけ**であり、前者は profile schema と遷移規則の実装後にしか検証できない。

さらに 2 巡目の Render Reproducibility 主張には**別の欠陥**があった:
同一 Python プロセス内で条件を順に生成しており、monkeypatch 状態・module
global・セッションオブジェクト・乱数状態が共有される。**同一プロセス内の
反復一致は independent replay の証明にならない**（レビュー指摘）。

本版は [`vgl0_reproducibility_check.py`](../probes/vgl0_reproducibility_check.py)
で**1 条件 = 1 サブプロセス**として測り直した:

結果 = [`vgl0_render_reproducibility_result.json`](vgl0_render_reproducibility_result.json)
（**計 10 プロセス**・verdict **PASS**）:

| 検査 | 内容 | 結果 |
|---|---|---|
| fresh-process replay | `baseline` / `cdur_x2.0` / `phrase_breath_20f` / `phrase_final_x1.5` を、まっさらな import から始まる別プロセスで各 2 回生成し WAV sha256 を比較 | **4/4 MATCH**（`787ca23d1de7` / `391f375bd729` / `da796c2d0895` / `68e3c8fa103b`） |
| 順序非依存性 | 全 17 条件を forward 順・reverse 順の 2 プロセスで通し、同一条件の WAV sha256 を比較 | **17/17 MATCH** |
| ExecutionProfile 一致 | 全プロセスの python/onnxruntime/numpy/platform が同一であること | **一致**（distinct = 1） |

なお fresh-process で得た sha は、同一プロセス内で通した主実測の sha とも
一致している。**同一プロセス内反復が偽陽性だったわけではない** — 証拠として
不十分だっただけで、独立プロセスで測り直しても結論は変わらなかった。

**同一プロセス内の反復**（`*_repeat` 条件）は結果 JSON でも
`in_process_repeat` として**別枠に分離**し、「independent replay の証拠には
ならない」と注記した。

**なお PASS の射程**: 本検査が固定するのは *ExecutionProfile を固定した上での*
再現性であって、環境非依存の決定論ではない。だから実行環境そのものを
結果 JSON へ記録し、全プロセスで一致していることまで検査対象にしている。

### 3.2 軸ごとの可動性 — 「WAV が変わった」は可動の証拠として弱い

**指標について**: gate_synth は wav を**ピーク 0.6 へ正規化**して書き出す
（`wav_peak` は全条件 0.6）。したがって wav から測った RMS は実質
クレストファクタで、**単一サンプルの peak に支配される**。一次指標には
gate_synth 自身が record.json に残す**正規化前の生値**
（`wav_rms_raw` / `wav_peak_raw`）を使う。

| 条件 | wav sha256（先頭 12） | 総尺 (s) | rms_raw | peak_raw |
|---|---|---|---|---|
| baseline | `787ca23d1de7` | 8.5449 | 0.007329 | 0.063278 |
| cdur ×0.25 | `e1897a872e09` | 8.5449 | 0.006921 | 0.052421 |
| cdur ×0.5 | `9b4954f24066` | 8.5449 | 0.007439 | 0.077433 |
| cdur ×1.5 | `e7d1208d8638` | 8.5449 | 0.006667 | 0.042876 |
| cdur ×2.0 | `391f375bd729` | 8.5449 | 0.006081 | 0.031430 |
| cdur ×3.0 | `8192c83fa20e` | 8.5449 | 0.006053 | 0.036085 |
| song_pad head40 | `20b6342f727a` | 8.9165 | 0.007128 | 0.078481 |
| song_pad tail40 | `0738256e2d7e` | 8.9165 | 0.007076 | 0.039631 |
| **phrase_breath 10f** | `82a8ba7307bc` | **8.7771** | 0.007097 | 0.055206 |
| **phrase_breath 20f** | `da796c2d0895` | **9.0093** | 0.007468 | 0.044488 |
| **phrase_final ×1.25** | `c65afe0edc6b` | **9.3809** | 0.006855 | 0.033570 |
| **phrase_final ×1.5** | `68e3c8fa103b` | **10.2168** | 0.006647 | 0.035541 |
| last_note ×1.5 | `64feb15a6788` | 8.9629 | 0.007082 | 0.056718 |
| cdur ×2.0 + breath 20f | `a85d024f11df` | 9.0093 | 0.006364 | 0.040676 |

**「WAV sha が baseline と違う = 軸に届いた」という判定は格下げする**
（レビュー指摘）。sha 差分は「入力が出力の**どこか**を変えた」ことしか
言わず、意図した制御が意図した形で効いたことを含意しない。以下は
**個別に量として検証できた**ものだけを可動と呼ぶ:

**(a) フレーズ間ブレス — 尺は厳密、音響実現は割れた**

尺は完全に予測どおり。フレーム長 11.61 ms（hop 512 / 44.1 kHz）、内部境界
2 箇所への挿入で:

- 10f × 2 箇所 = 20 フレーム = 232.2 ms → 8.5449 + 0.2322 = **8.7771 s**（実測一致）
- 20f × 2 箇所 = 40 フレーム = 464.4 ms → 8.5449 + 0.4644 = **9.0093 s**（実測一致）

一方、**挿入した SP 区間が実際に無音としてレンダリングされるかは境界で割れた**
（同一ファイル内の隣接ノート領域との比。ピーク正規化はファイル内で共通なので
この比は正規化不変）:

| 条件 | 境界 1（ノート idx3） | 境界 2（ノート idx7） |
|---|---|---|
| phrase_breath 10f | rms 0.00547 / 直前比 **−24.5 dB** | rms 0.02311 / 直前比 **+4.3 dB** |
| phrase_breath 20f | rms 0.00782 / 直前比 **−26.0 dB** | rms 0.06461 / 直前比 **+9.8 dB** |
| cdur×2.0 + breath 20f | rms 0.00741 / 直前比 **−25.8 dB** | rms 0.01588 / 直前比 **−1.0 dB** |

境界 1 は −25 dB 前後で**無音**と言える。境界 2 は直前ノートより**エネルギーが
高い**（20f で +9.8 dB）。SP トークンは acoustic まで届いているが、
**そこが無音として鳴るかどうかは文脈依存**で、少なくとも 1/2 の境界で
無音になっていない。したがって **U1「ブレスとして成立するか」は未判定**。
尺の制御（C3）とは分けて記帳する。

**(b) フレーズ終端伸長 — 尺は厳密。非終端ノートは不変**

`is_phrase_final == True` のノート（idx 2 と 5・各 144 フレーム）だけを
×1.25 / ×1.5 した。`note_target_frames` の実測:

| 条件 | note_target_frames |
|---|---|
| baseline | `[72, 72, 144, 72, 72, 144, 72, 72]` |
| phrase_final ×1.25 | `[72, 72, **180**, 72, 72, **180**, 72, 72]` |
| phrase_final ×1.5 | `[72, 72, **216**, 72, 72, **216**, 72, 72]` |

**非終端ノート（72 フレーム）は 6 個すべて不変**で、終端ノートだけが
144→180→216 と動いている。総尺 8.5449 → 9.3809 → 10.2168 s
（**0.25 刻みあたり** +0.836 s = 2 ノート × 36 フレーム × 11.61 ms。
一般形は §3.6 の `対象ノート数 × 144 × (scale − 1)`）。

**フレーズ境界で切った `notes_limit` での確認**: `notes_limit=8` はフレーズ 2 の
途中で切れているため、「最終ノートがフレーズ終端であるケース」を含まない。
フレーズ境界ちょうどで終わる `notes_limit=6`（フレーズ 0-1 完結・終端 = idx 2,5）
と `notes_limit=10`（フレーズ 0-2 完結・終端 = idx 2,5,9）でも測り直した
（§3.6）。

**(c) 直交性は否定側の証拠が出た**

`cdur×2.0 + breath 20f` は、ブレス単独条件と比べて**境界 2 の音響実現が
変わっている**（+9.8 dB → −1.0 dB）。総尺は同じ 9.0093 s だが、
**片方の軸を動かすともう片方の軸の音響的な効き方が変わる**。
2 巡目の「同時指定で破綻しない」までの格下げは維持しつつ、
**直交性は積極的に否定される方向の観測**として記帳する（U3）。

### 3.3 制御量は単調 / 音響応答は未判定

介入後の子音フレーム比（duration 段での実測。制御量そのもの）:

| 条件 | 子音比（既定 → 適用後） |
|---|---|
| cdur ×0.25 | 0.1923 → **0.0562** |
| cdur ×0.5 | 0.1923 → **0.1064** |
| cdur ×1.5 | 0.1923 → **0.2632** |
| cdur ×2.0 | 0.1923 → **0.3226** |
| cdur ×3.0 | 0.1923 → **0.4167** |

**制御量は入力係数どおり単調**（0.056 → 0.417）。ここは確定（C2）。

**音響応答の単調性は本実測では判定できない**（U2）:

- 一次指標 `rms_raw` は ×0.5 の 0.007439 を頂点に ×3.0 の 0.006053 まで
  下がるが、×0.25（0.006921）が ×0.5 より低く**反転が 1 箇所**残る。
  変動幅は 0.00605〜0.00744（約 ±10%）で、単調とも非単調とも断定できる
  精度がない
- そもそも RMS（生値でも）は**知覚品質の代理として粗い**。「アタックが
  強くなったか」を測るなら音素境界の立ち上がりや子音区間のエネルギー比が
  要るが、本実測では測っていない

したがって「格子を細かく取れば知覚も滑らかに動くか」は**未判定**であり、
初版の「選抜（耳の順位付け）方式の妥当性が実測で裏づけられた」という主張は
**撤回済み**。DESIGN_VG_L0 が選抜方式を採ること自体は設計判断として有効だが、
本実測はその根拠を与えていない。

### 3.6 フレーズ境界で切った `notes_limit` での再確認

`notes_limit=8` はフレーズ 2 の途中で切れているため「最終ノートがフレーズ終端
であるケース」を含まない。フレーズ境界ちょうどで終わる 2 条件でも測り直した
（結果 = [`vgl0_control_axis_probe_result_n6.json`](vgl0_control_axis_probe_result_n6.json)
/ [`vgl0_control_axis_probe_result_n10.json`](vgl0_control_axis_probe_result_n10.json)）。

| notes_limit | `is_phrase_final_per_note` | 終端ノート | 内部境界 | 末尾がフレーズ終端か |
|---|---|---|---|---|
| 6（フレーズ 0-1 完結） | `[F,F,T,F,F,T]` | idx 2, 5 | idx 2 のみ | **はい** |
| 8（フレーズ 2 の途中で切断） | `[F,F,T,F,F,T,F,F]` | idx 2, 5 | idx 2, 5 | **いいえ** |
| 10（フレーズ 0-2 完結） | `[F,F,T,F,F,T,F,F,F,T]` | idx 2, 5, 9 | idx 2, 5 | **はい** |

**終端伸長**（`is_phrase_final == True` のノートのみ対象。**非終端ノートは
全ケースで 72 フレームのまま不変**）:

| notes_limit | baseline | ×1.25 | ×1.5 | 対象ノート数 |
|---|---|---|---|---|
| 6 | 6.8731 s `[72,72,144,72,72,144]` | 7.7090 s（+0.8359） | 8.5449 s（+1.6718） | 2 |
| 10 | 11.0527 s `[…,144,…,144,…,144]` | 12.3066 s（+1.2539） | 13.5605 s（+2.5078） | 3 |

いずれも **`対象ノート数 × 144 フレーム × (scale − 1) × 11.61 ms`** と一致する
（終端ノートの基準長が 144 フレームのため）:

| | 増分フレーム | 増分秒 | 実測 |
|---|---|---|---|
| n=6 ×1.25 | 2×144×0.25 = 72 | 0.8359 | 0.8359 |
| n=6 ×1.5 | 2×144×0.50 = **144** | **1.6718** | 1.6718 |
| n=10 ×1.25 | 3×144×0.25 = 108 | 1.2539 | 1.2539 |
| n=10 ×1.5 | 3×144×0.50 = **216** | **2.5078** | 2.5078 |

**末尾ノートがフレーズ終端である場合も同じ規則で伸びる**ことを確認した。

> **訂正（レビュー指摘・4 巡目）**: 本節と C4 は当初これを
> 「`対象ノート数 × 36 フレーム × 11.61 ms`」と書いていたが、36 フレームは
> **×1.25 のときの 1 ノートあたり増分**にすぎず、×1.5 では 72 フレームになる。
> 「いずれも」で両水準に適用したのは誤りで、上の表の ×1.5 行とは 2 倍ずれる
> （表の実測値自体は正しかった）。scale を含む式へ改めた。

**ブレス挿入**（内部境界のみ。**曲末には入れない**）:

| notes_limit | 境界数 | 10f | 20f |
|---|---|---|---|
| 6 | 1 | 6.9892 s（+0.1161） | 7.1053 s（+0.2322） |
| 10 | 2 | 11.2849 s（+0.2322） | 11.5171 s（+0.4644） |

`境界数 × frames × 11.61 ms` と完全一致。`notes_limit=6` では末尾ノートが
フレーズ終端でも**ブレスは挿入されない**（曲の最後にフレーズ間ブレスは
来ないため）— 意図どおりの挙動。

**総尺は軸の識別子にならない（`notes_limit=6` で露呈）**: `phrase_final ×1.25`
（終端 2 ノートを 144→180、計 +72 フレーム）と `last_note ×1.5`（末尾 1 ノートを
144→216、計 +72 フレーム）は**総尺が完全に同一の 7.7090 s** になる。
`note_target_frames` は `[72,72,180,72,72,180]` と `[72,72,144,72,72,216]` で
明確に別物だが、総尺だけを見ていると区別できない。§3.2 の「WAV sha も総尺も
可動の証拠として弱い」を裏づける実例であり、**中間量（`note_target_frames` /
`phone_frame_invariant`）を記帳する理由**でもある。

### 3.4 音素長の不変条件 — 総和保存は成立、下限ガードは**存在しない**

`run_pipeline` の Stage 1 は per-note rescale のあと `int(round(x))` で丸め、
端数 `resid` を**そのノートの最後の音素へ加算**する。この処理に
**下限ガードは無い**（`gate_synth.py` Stage 1）。実際に acoustic へ渡った
`durations` を横取りして測った結果:

| 条件 | 最小子音フレーム | 最小母音フレーム | 下限 1 までの余裕 | 総和保存 |
|---|---|---|---|---|
| baseline | 9 | 44 | 8 | ✅ |
| **cdur ×0.25** | **3** | 62 | **2** | ✅ |
| cdur ×0.5 | 5 | 55 | 4 | ✅ |
| cdur ×1.5 | 13 | 37 | 12 | ✅ |
| cdur ×2.0 | 17 | 32 | 16 | ✅ |
| **cdur ×3.0** | 22 | **25** | 21 | ✅ |
| phrase_final ×1.5 | 8 | 45 | 7 | ✅ |

- **総和保存は 3 走行（`notes_limit` 8 / 6 / 10）の全 51 条件で成立**（C5）。
  `sum(ph_dur) == sum(note_target_frames)`、`ph_dur < 1` は **0 件**
- ただしこれは「**さくらのこの係数範囲では出なかった**」以上のことを意味しない
- **余裕は薄く、しかもノートを増やすと縮む**。×0.25 での最小子音フレーム:

  | notes_limit | 最小子音フレーム | 下限 1 までの余裕 |
  |---|---|---|
  | 6 | 3 | 2 |
  | 8 | 3 | 2 |
  | **10** | **2** | **1** |

  ノート範囲を広げただけで余裕が 2 → **1 フレーム**に縮んだ。さくら全 20 ノートや
  うみ、より短いノートを持つ曲では **0 フレームに到達しうる**。
  コード側に下限ガードが無い以上、これは profile 値の domain gate で
  塞ぐしかない（§5-5 BLOCKER）。**「さくら 8 ノートで通った」を安全性の
  根拠にしてはならない**ことの実測的な裏づけになっている
- ×3.0 では逆に**母音側**が 25 フレームまで圧迫される。下限に先にぶつかるのが
  常に子音とは限らない

### 3.5 可動範囲は「観測できた範囲」であって「安全な範囲」ではない

`consonant_duration_scale` は 0.25〜3.0、ブレスは 10/20 フレーム、
終端は ×1.25/×1.5 しか試していない。**どこまで動かすと音として破綻するか**は
聴取していないため未評価（U4）。

したがって schema v0 でこれらを `min` / `max` として凍結してはならない
（レビュー指摘）。**観測された実験レンジと安全レンジを型で分離する**:

```json
"consonant_duration_scale": {
  "type": "number",
  "experimental_observed_range": [0.25, 3.0],
  "observed_on": {"song": "sakura", "notes_limit": [6, 8, 10], "checkpoint": "run6_40k"},
  "min_phone_frame_margin_observed": 1,
  "safe_range": null
}
```

`safe_range` を埋める条件は §5-5 の構造ゲート（全曲・最短/最長ノート・
各 phoneme count）通過 + 耳で破綻しないことの確認。

## 4. 耳監査コストの見積もり — **算定不能（SB0 発動判断は次段へ）**

2026-08-19 の裁定で SB0 は棚上げ・**発動条件 = 「VG-L0 実測で耳監査が
1 遷移あたり 10〜12 ペアを超える」**とした。本記録での扱い:

- **本実測に耳判定は 0 件**であり、発動条件の述語（実測された耳監査量）を
  満たす材料が無い
- 初版の「3 軸 × 4 水準 = 12 テイク」前提は、(i) 水準 4 の根拠が §3.5 のとおり
  無く、(ii) DESIGN_VG_L0 §6 の「制御軸の決定論的な水準組（**グリッド**）」を
  軸独立スイープ（3×4=12）と読むか直積（4³=64）と読むかで桁が変わり、
  (iii) 発動条件の「ペア」をセッション単位へ読み替える操作も入っていた。
  **いずれも根拠のない読み替え**なので撤回済み
- → **SB0 の発動可否は本記録では判定しない**（U5）。判定に必要なのは
  (a) グリッドの解釈確定、(b) 可動範囲の聴取評価に基づく水準数、
  (c) 順位付けの実施形態、の 3 点で、いずれも次段の成果物である

## 5. 次段への申し送り — ControlProfile schema v0 の **BLOCKER**

**以下が未解決のあいだ、ControlProfile schema v0 を canonical として
凍結しない**（レビュー指摘の設計汚染ブロッカー）。

### 5-1. [BLOCKER] Performance 制御の意味の正本を 1 箇所に置く

**この危険は仮説ではなく、リポジトリ内で既に 4 回起きている。**
現状、演奏制御は**3 つの独立したバックエンド**に分かれて実装されている:

| 実装 | バックエンド | 出力レート |
|---|---|---|
| `singer/performance.py` + `singer/render_song.py` | 自前ソースフィルタ（`glottal.py` + `formant_tv.py`） | 22050 Hz |
| `singer/performance.py` + `foundry/adapter/perf_genes.py` + `foundry/adapter/render.py` | **WORLD**（`pyworld.synthesize`） | 24000 Hz / 5 ms |
| `foundry/s1_gate/gate_synth.py` | **DiffSinger ONNX** + NSF-HiFiGAN | 44100 Hz / hop 512 |

**gate_synth は `performance.py` も `perf_genes.py` も import していない**
（`grep -n "performance\|perf_genes\|perf\." gate_synth.py` → 該当なし）。
つまり DiffSinger 経路には演奏制御層が**存在しない**。

既に発生している意味衝突（**新しい制御軸を足す前から**）:

| 名前 | 実装 A | 実装 B | 乖離 |
|---|---|---|---|
| **breath** | `performance.py:18` `BREATH_DURATION_SEC = 0.25`（フレーズ間**無音の秒数**） | `voice_spec.py:237` `breath_lift`（WORLD 非周期性 ap の**無次元底上げ**）/ `proto1/genome.py:66` `breathiness_base`（**ノイズ比**駆動値） | 3 つが無関係な量 |
| **jitter** | `glottal.py:112` `jitter_amount = 0.006`（F0 への**無次元相対比**） | `perf_genes.py:174` `jitter_cents = 3.0`（**セント**） | 同じ 10 ms ブロック法だが単位が約 170 倍違う |
| **vibrato** | `performance.py:99` **曲頭からの絶対位相**（デフォルト値なし） | `perf_genes.py:82` **ノート先頭で位相リセット**（既定 5.2 Hz / 25 cents）/ `proto1/genome.py:80` 既定 5.5 Hz / 45 cents（範囲拘束あり） | 位相規約 3 種・既定 3 種。しかも WORLD 経路では genome 側 vibrato が `perf_genes.py:139` で `depth_cents=0.0` に**潰されている**のに対し、singer 経路では genome 値がそのまま通る（`render_song.py:395-396`） |
| **attack** | `performance.py:21` `NOTE_ATTACK_RELEASE_MS = 15.0`（**ms**・しかもフレーズ先頭のみ適用） | `VISION_evolution_theory_v0.1.md:602` `"attack": 0.46`（**無次元 0-1 の genome prior**・未実装） | 単位も適用範囲も別物 |
| **drift** | `perf_genes.py:167` F0 の**セント揺れ** | `evolution/models.py:66` **genome 変異オペレータ名**（simplex 座標の変位・`DRIFT_STEP_MAX=0.08`） | 同名で完全に別概念 |
| **onset** | `performance.py:20` `VIBRATO_ONSET_DELAY_MS`（ビブラート深さの**ランプ長**） | `perf_genes.py:52` `onset_glide`（**ピッチのしゃくり**）/ `gate_synth.py:288` `mora.onset`（**語頭子音**） | 3 つが別概念 |

ここへ VG-L0 が独自に `breath` / `attack` / `final` を **gate_synth 内部へ**
足すと、5 つ目の衝突を作ることになる。

**方針（次段で確定させる）**: 制御意味の正本を renderer の外側へ置く。

```
ControlProfile（監査可能な制御意味 or 未校正なら機構的事実値・単位を名前に含める）
    ↓
Semantic Performance IR
    ↓
Renderer Adapter ── DiffSinger adapter / WORLD adapter / singer adapter
```

- ControlProfile が持つ意味は**1 つ**。renderer は自分の内部パラメータへ
  変換するだけ。例: `phrase_breath_duration_ms` を正本値とし、
  DiffSinger adapter は境界 SP フレーム数へ、WORLD adapter は
  `TimelineSegment` 間の無音サンプル数へ変換する
- **禁止**: renderer 固有値（`HEAD_FRAMES` など）を共通 ControlProfile の
  意味名として直接保存すること
- **単位を名前に含める規律**を新設する（上表の `BREATH_DURATION_SEC` と
  `phrase_breath_duration_ms` のような衝突を構造的に防ぐため）

### 5-2. [BLOCKER] monkeypatch を canonical 経路へ入れない

本実測の provenance 上の穴を正直に記す: gate_synth 本体・モデル・楽譜の
sha が**全部同じでも**、patch の中身次第で別の WAV が出る。「コード本体は
同じだが実行時だけ関数が書き換わる」という hidden mutable behavior は、
LearningTransition の証拠経路に入れてはならない。

**本 PR で閉じた分**: 結果 JSON に probe 自身と gate_synth 本体の sha を
**モデルと同格で**束縛した（`pins.probe_script` / `pins.gate_synth` /
`pins.score_module` / acoustic / embed / vocoder / canon 各 onnx）。
入力パラメータと ExecutionProfile も同じ JSON に入っている。

**次段の要件**: 製品実装では monkeypatch を**禁止**する。

```python
apply_control_profile(score_features, duration_features, profile) -> features
```

のような純関数または明示的 adapter とし、profile が既定（None）のときは
現行と**バイト同一**の経路になることを検査で担保する。

### 5-3. [BLOCKER] `perf_genes` と学習 ControlProfile の優先規則

`adapter/perf_genes.py` には `onset_glide` / `vibrato` / `drift` / `jitter` /
`portamento` があり、VG-L0 の ControlProfile も将来同じ項目を持つ可能性が高い。
境界未定義のまま両方を適用すると二重掛けになる（二重ブレス・二重 vibrato）。

**実態の確認（次段の裁定材料）**:

- `perf_genes` の値は**遺伝しない**。出所は手書きの preset JSON
  （`adapter/presets/*.json` 4 本。うち 3 本は perf ブロックがバイト同一）で、
  `FoundryVoiceSpec` は frozen dataclass だが `parent` / `lineage` /
  `generation` フィールドを**持たない**（`voice_spec.py:145-151`）
- genome 台帳には `performance_prior` スロットが**予約されているが空**。
  記録済み 14 genome すべてで `{}`（writer が `forge_triangle.py:630` で
  ハードコード）
- `_validate_perf`（`voice_spec.py:126-142`）は未知キーと非有限値では
  fail-closed するが、**数値範囲の検査は 1 つも無い**

したがって「継承された既定技能 vs 学習された上書き」という素朴な整理は
**現状の実装には対応していない**（perf_genes は継承物ではなく preset）。
次段は次のどちらかを**明文化**してから schema を切る:

- A: `perf_genes` = 既定技能 / ControlProfile = 学習上書き → `effective = override(base)`
- B: `perf_genes` = identity 非依存の演奏 prior / ControlProfile = 差分 → `effective = base + delta`

**禁止**: renderer ごとに優先順位を勝手に決めること。LearningTransition は
`親の effective profile + 学習 delta = 子の effective profile` を**再計算可能**に
すること。

### 5-4. [BLOCKER] phrase metadata は共通中間表現へ持ち上げる

§2-3 の配線ギャップは **gate_synth 専用パッチとして直さない**。
`Score → Performance IR → Renderer` の共通中間表現へ持ち上げる。

最低フィールド: `note_id` / `phrase_id` / `is_phrase_first` /
`is_phrase_final` / `phonemes` / `note_duration` / `pitch` /
（任意）`breath boundary`。

この IR に ControlProfile を適用してから renderer adapter へ渡す。
これにより WORLD と DiffSinger で「phrase breath」の意味が分裂することを防ぐ。
なお `performance.py:48` の `is_phrase_last` フラグは**設定されているが
一度も読まれていない**（フレーズ末減衰は `segs[-1]` で別に判定している）ので、
IR 設計時にどちらの規約を正本にするかを決める必要がある。

### 5-5. [BLOCKER] 制御値の domain / safety gate

§3.4 のとおり、現行の duration 処理には**下限ガードが無い**。schema と
適用関数の**両方**に hard invariant を置く:

- `all ph_dur >= 1 frame`
- `sum(ph_dur of note) == note_target_frames`（現状は成立しているが**検査は無い**）
- 有限値のみ / `scale > 0` / schema 定義の min ≤ value ≤ max

不可能なノート（2 音素なのに `target_frames = 1` など）では**fail-closed**。
黙ってどちらかを 0 にしない。推奨形:

```python
redistribute_phone_frames(predicted, target_frames, control, min_phone_frames=1)
```

成功条件 = 総和の厳密保存 / 最小フレーム保証 / 決定論的な端数処理。

### 5-6. [BLOCKER] 未校正の値に知覚的な名前を付けない

本実測が操作したのは **子音時間配分**であって、知覚上の「アタック強度」
そのものではない。しかも §2-1 のとおり変更後の duration は pitch predictor の
入力にも入るため、効果は acoustic まで波及する。

`attack_strength = 2.0` と schema 化すると「アタックを 2 倍」という意味で
保存されるが、実際の操作は「子音時間配分を変更し、その結果 pitch / acoustic も
変化」である。これは LearningTransition の意味汚染になる。

**本 PR で実施済み**: probe の制御名・条件ラベルを機構名へ改名した
（`consonant_scale` → `consonant_duration_scale`、条件 `attack_x*` →
`cdur_x*`）。本記録も同様に改めた。

**次段の要件**: schema v0 では機構フィールドと意味解釈を分離する。

```json
{
  "controls": {"consonant_duration_scale": 1.5},
  "semantic_labels": {"attack_strength": {"status": "uncalibrated"}}
}
```

semantic alias は `consonant_duration_scale → 聴感/計量校正 → attack_strength`
の対応が確認されてから導入する。**禁止**: `attack_strength` /
`articulation_strength` 等の知覚意味名を未校正のまま canonical field にすること。

### 5-7. その他の設計要件（BLOCKER ではないが引き継ぐ）

1. **総尺不変軸と総尺可変軸を型で区別する**。ただし §2-1 のとおりこれは
   実装位置の帰結なので、「ブレス/終端を長さ中立に実装できるか」を先に
   検討する余地がある
2. profile が持つ値を「係数」にするか「目標制御量（子音比など）」にするかは
   **未決**。目標制御量は曲やノート構成が変わっても意味を保つ利点があるが、
   実現には**曲ごとに duration 予測を走らせて逆算する**必要があり、
   「解析的に計算できる」わけではない

## 6. 未実施（本記録の射程外）

- 稽古 1 遷移（r0→r1）の実施とブラインド順位付け（耳判定 0 件）
- held-out 曲（うみ）での A/B 転移判定、および**うみでの音素長不変条件の検査**
  （§3.4 の余裕はさくら限定）
- 制御プロファイル schema v0 / `learning-transition/0.1` 台帳の実装
  （§5 の BLOCKER 解決が先）
- 可動範囲（破綻閾値）の聴取評価 → `safe_range` の確定
- 音響応答の単調性判定（適切な評価軸の選定込み・VG-E1 と合流）
- ブレスの音響実現が境界で割れる件（§3.2 U1）の原因究明
- Profile Transition Determinism の検証（schema 実装後）

## 7. 版ごとの撤回・訂正

### 7-1. 初版 → 2 巡目（セルフレビュー）

| 初版の記述 | 訂正 |
|---|---|
| 中核の知見 = 音響応答は非単調 | **撤回**。ピーク 0.6 正規化後の RMS を見た artifact。生値では反転 1 箇所・±10% で**未判定** |
| 選抜（耳の順位付け）方式の妥当性が実測で裏づけられた | **撤回**（上記の帰結。勾配/補間の否定も同様に撤回） |
| 決定論 PASS | **限定**。初版は baseline 反復のみで制御軸経路を通っていなかった → 制御軸ありの反復を追加 |
| アタック軸のみ総尺不変（軸の性質） | **訂正**。実装位置（per-note rescale の前）の帰結 |
| 直交性を確認 | **格下げ**。総尺は構成上必ずそうなり、独立性の証拠にならない |
| 目標制御量は解析的に計算できる | **訂正**。曲ごとに duration 予測の逆算が要る |
| SB0 発動条件: 18 ペア vs 3 セッション | **撤回**。水準数・グリッド解釈・ペア定義のいずれも根拠のない読み替え → **算定不能** |
| 段構成「3 段」 | **訂正**。実際は 4 段で linguistic は 2 回呼ばれる |
| 前提 = run 5 checkpoint（逸脱の記載なし） | **明示**。本実測は run 6 40K を使用 |

### 7-2. 2 巡目 → 本版（外部レビュー 2 件）

| 2 巡目の記述 | 訂正 |
|---|---|
| 候補 **3 軸すべて可動** | **撤回**。ブレス軸・終端軸の定義が誤っていた（§2-2）。測り直した結果は「尺は厳密に制御できる / ブレスの音響実現は境界で割れる」（§3.2） |
| ブレス位置 = `HEAD_FRAMES`/`TAIL_FRAMES` | **訂正**。曲全体の SP パディングであってフレーズ間ブレスではない。条件を `song_pad_head/tail` へ改名 |
| フレーズ終端処理 = `note_target_frames[-1]` | **訂正**。`notes_limit=8` の最終ノートはフレーズ終端ではない（機械実測で確認）。条件を `last_note_x1.5` へ改名し、真の終端ノート伸長を別条件で測り直した |
| Render Reproducibility PASS | **測り直し**。同一プロセス内の反復では independent replay の証明にならない → 1 条件 1 プロセス + 順序反転で再検証（§3.1） |
| 制御軸名「アタック強度」 | **格下げ**。機構名 `consonant_duration_scale` へ改名（§5-6） |
| 可動範囲 0.25〜3.0 | **限定**。`experimental_observed_range` であって `safe_range` ではない（§3.5） |
| WAV sha が違う = 軸に届いた | **格下げ**。sha 差分は「どこかが変わった」しか言わない（§3.2） |
| vocoder pin = onnx の sha のみ | **訂正**。正本は配布物 `.oudep` の sha（`s1_gate/README.md` 行 20）。両方を併記（§1） |
| checkpoint 逸脱は「結論に影響しない」 | **限定**。代替が許される射程は**入力インタフェースの探索まで**。音質・知覚・可動範囲の結論は checkpoint 依存（§1） |
| probe 実装・結果 JSON は非コミット | **是正**。本 PR でコミット（`evolution/probes/` / `evolution/records/`） |

## 8. STATUS への昇格フロー（新設）

`.claude/memory/STATUS.md` は後続セッションが状態復元に読むため、通常の
研究メモより**汚染伝播力が高い**。実際、本件では「3 軸すべて可動」が
レビュー前に STATUS へ入っていた。以後、次の順序を守る:

```
experiment / provisional record
    ↓ self-review
    ↓ external / PR review
accepted record
    ↓
STATUS canonical summary
```

- レビュー前に STATUS へ載せる場合は **`[PROVISIONAL]` / `[REVIEW_PENDING]`
  の明示を必須**とする
- レビューで意味が確定した後に `accepted` へ変更する
- 本記録は **accepted**（冒頭・2026-08-20 昇格）。STATUS の該当行からも
  `[PROVISIONAL]` を外した

> **昇格の前提条件（外部レビュー指摘・2026-08-20）**: accepted 昇格は
> **snapshot registry が fail-closed 化され、result↔snapshot の SHA 帰属・
> live_unmeasured の SHA 束縛・consumed キー集合の完全一致が閉じた後**に成立する。
> 本記録の昇格とこれらの是正は**同一 PR（#291）に入っている**ため、マージされた
> 状態では順序条件を満たす。逆に言えば、**#291 がマージされない限りこの accepted は
> 有効でない** — 部分マージやチェリーピックで昇格だけを取り込んではならない。

> **昇格が 1 手番遅れた（正直会計）**: PR #289 は 2026-08-20 にマージされたが、
> 冒頭の PROVISIONAL 表記と STATUS の `[PROVISIONAL]` はその場で外しておらず、
> 次セッションまで「マージ済みなのに provisional」という状態が残った。
> **昇格フローを新設した当の PR が、そのフローの最終段を実行していなかった**。
> フォローアップ PR で実行し、以後は**マージと同じ PR の中で昇格を済ませる**
> （マージ後の別作業にすると、いま起きたとおり落ちる）。

## 9. pin / provenance ファミリーの終端宣言（2026-08-20・4 巡目）

`AGENTS.md` §3-2 は「**同型穴はファミリー単位で全数掃討し、終端を宣言する**」を
定め、L437-446 は #191 の実測（Codex 9 ラウンドが全て「全入力の pin 検証接続」と
いう同根で、最初に設計を固めていれば 1–2 ラウンドで閉じた）を根拠に、
**一部入力だけ pin して残りを指摘駆動で塞ぐこと**を明確に戒めている。

本 PR は 3 巡目まで実際にそのドリップ対応をしていた（probe sha → 消費バイト →
checker sha → プロセス間 pin と、同じファミリーの穴が 1 件ずつ露呈した）。
4 巡目でファミリーを定義し、全数を一度に閉じて終端を宣言する。

**ファミリーの定義**: 「verdict（PASS 判定）を生む全入力が、(a) pin され、
(b) 実行時に消費されたバイトと一致検証され、(c) 全プロセスで不変であること」

| # | メンバー | 状態 |
|---|---|---|
| 1 | probe 実装 | **ロード時（import 前）** pin + 実行後の再 hash 一致 + コミット済み実体との一致を CI 検査 |
| 2 | 検査スクリプト（checker） | **ロード時** pin + 実行後の再 hash 一致を検査 |
| 2b | gate_synth 本体 | **import 前** pin + 実行後の再 hash 一致（コミット実体との照合はしない = §9-2 の据え置き項目） |
| 3 | acoustic / dsconfig / canon 3 onnx / vocoder | pin + **消費バッファそのものの hash と照合**（fail-closed） |
| 4 | 楽譜モジュール（score.py + 依存） | pin のみ。**照合は実装済みだが未実測**（正本 4 本は凍結版が生んだもので当該検査を通っていない）→ 昇格は再実測後。2026-08-20 8 巡目で昇格を撤回 |
| 5 | 話者 embed / 音素辞書 | **照合は実装済みだが未実測**（`*_with_sha` ローダで閉じた。2026-08-20 12 巡目で射程外宣言を撤回）→ 昇格は再実測後 |
| 6 | ExecutionProfile | 全プロセスで一致を検査 |
| 7 | プロセス間の pin 不変性 | 10 プロセス全ての pin セットを突き合わせ |
| 8 | 出力先が入力を壊さないこと | `--out-dir` / `--result-json` を全入力と突き合わせる**書き込み前**の衝突ガード（probe / checker 両方）。protected_inputs は**必須引数** |
| 9 | 条件ディレクトリの破壊的再作成 | 所有マーカー `.vgl0_probe_output` のあるディレクトリのみ削除 |

> **掃討が 1 巡で閉じきらなかった（5 巡目・正直会計）**: 4 巡目で「終端」と
> 宣言したが、その時点で **probe / gate_synth の pin は import 後に read して
> いた**（checker には同じ巡でロード時 pin を入れたのに、probe 側へ横展開して
> いなかった）。加えて `--result-json` / `--out-dir` が入力を指した場合の
> **書き込み衝突ガード**も欠けていた。5 巡目でこの 2 件を閉じ、下表を更新した。
> 「ファミリー全数掃討」を宣言するときは、**同じ検査を全メンバーへ機械的に
> 横展開したかを確認してから**宣言すること（本件の反省）。

**終端宣言**: 上表 1〜3・6〜9 は閉じた。**4 と 5 は「実装済み・未実測」**で、
再実測まで閉じない。**5 の射程外宣言は 12 巡目で全面撤回した**（下記）。
撤回前の宣言は以下だった —
話者 embed / 音素辞書は `gate_synth` がパス read するため、消費バイト照合まで
閉じるには gate_synth の I/O 構造変更（`load_model_bundle_bytes` 相当への集約）が
必要で、本 PR の read-only 契約に反する。射程は結果 JSON の
`consumed_model_bytes_check.not_covered` に機械可読な形で残した。

> **境界宣言を 1 件分だけ撤回した（2026-08-20 フォローアップ・§9-3）**: 当初の
> 5 は「話者 embed / 音素辞書 / **楽譜モジュール**」の 3 つを一括で射程外に
> していたが、**楽譜モジュールについては前提が誤っていた** — `load_song_module`
> は消費した digest を per-call で返しており、gate_synth を一切変えずに照合
> できる（7 巡目レビュー指摘）。**「gate_synth の I/O 構造変更が要る」という
> 共通理由を、実際には確かめずに 3 つへ一括適用していた**のが誤りの機構で、
> 本記録が 3 度撤回したのと同型（機構から因果を組み立てて実測しない）。
> 境界宣言はメンバーごとに理由を実地確認してから引く。残る 5 は
> 話者 embed / 音素辞書の 2 つで、理由は上のとおり変わらない。
>
> **その撤回に乗せて member 4 を「閉じた」としたのは早すぎた（8 巡目で再訂正）**:
> 照合コードは書いたが、**正本 4 本はいずれも凍結版 `85e33df2…` が生んだもので
> `consumed_score_bytes_check` を持たない**（fixture テストも当該フィールドの
> 無い結果は skip する）。つまり **fail-closed 保証を実測した走行が 1 つも無い** —
> 既存の正本については「多条件走行の途中で楽譜が差し替わっていない」ことを
> 依然として排除できていない。member 4 は**実装済み・未実測**として開いたままに
> し、live probe で正本を再生成した時点で昇格する。
>
> これは s6 record の申し送り「**対策は投入でなく効果で終端宣言する**」に自分で
> 違反した形（り→ん破綻の帰属撤回と同じ機構 = 意図を効果と読んだ）。申し送りを
> 書いた当人が次の PR で同じ誤りをしたので、**§9 の表では「実装済み」と
> 「実測済み」を分けて書く**ことで構造的に防ぐ。
>
> **member 5 の射程外宣言も全面撤回した（12 巡目）**: 「話者 embed / 音素辞書は
> gate_synth がパス read するので I/O 構造変更が要る」は**事実誤認**だった —
> `load_canon_phonemes_with_sha` / `load_own_phonemes_json_with_sha` /
> `load_speaker_embed_vector_with_sha` が**既に存在し、gate_synth 本体も
> 1745/1883/1920 行で使っている**。非 hash 版で読んでいたのは probe 側の選択
> でしかなかった。**同じ member 5 で 2 度、API 面を確かめずに「構造変更が要る」と
> 宣言していた**ことになる（7 巡目 = 楽譜 / 12 巡目 = 残り 3 入力）。
> `*_with_sha` へ切り替えて `consumed_input_bytes_check` を追加し、
> **member 5 は「射程外」から「実装済み・未実測」へ**。これで
> `consumed_model_bytes_check.not_covered` は**空**になった。
>
> 教訓: **「この層は閉じられない」と書く前に、その層の公開 API を grep する**。
> 2 度とも grep 1 回で反証できる誤りだった。

**再開条件**（`AGENTS.md` §3-3 の宣言様式）: 本宣言を根拠に据え置くのは
「宣言済み範囲の再指摘で、新しい実害経路を示さないもの」に限る。
偽成功・データ破壊・provenance 汚染への**具体的な実害経路が新たに示された場合**は
巡数に関わらず再着手する。5 の恒久解決は前処理層の製品実装（§5-2 BLOCKER）と
同時に行う。

## 9-1. レビュー打ち切りとフォローアップ送り（2026-08-20・User 裁定）

Codex 自動レビューは **6 巡目で打ち切り**（User 裁定）。1〜6 巡目の指摘は
すべて採用・コミット済み（見送り 0 件）。

> **「resolve は実行環境の制約で不可」は訂正する（2026-08-20 フォローアップ）**:
> #289 の作業セッションでは REST が repo スコープ 403 を返したため
> 「resolve 不可・User へ引き継ぐ」と書いたが、**フォローアップ PR のセッションでは
> GitHub MCP 経由の `resolve_review_thread` が通った**（#291 の 3 スレッド +
> #289 の 9 巡目スレッドを実際に resolve 済み）。**環境の恒久的制約ではなく
> セッションごとの権限差**だった。以後は「不可」と決めつけず毎回試すこと
> （不可のときだけ採否をコミットへ残して引き継ぐ）。

**7 巡目以降はフォローアップ PR で扱う。** 落とさないよう内容を記録する:

| 指摘（7 巡目 P2） | 中身 | 扱い |
|---|---|---|
| Protect every derived acoustic input in the checker | `--acoustic-onnx` が `--acoustic-dir` の外にある場合、派生する `.phonemes.json` / `<spk>.emb` が checker 側の保護リストに無い。`ProbeConfig.protected_inputs()` を再利用すべき | フォローアップ |
| Refuse to unlink unowned checker result files | checker の `result_json.unlink()` が所有検査なしに既存の無関係ファイルを消しうる（`order_forward.json` 等の固定名） | フォローアップ |
| Verify the score bytes returned by each synthesis | `load_song_module()` が返す `_shas`（消費した楽譜モジュールの digest）を `synth_once` が捨てている。**gate_synth が既に per-call で返しているので、read-only I/O を変えずに検証できる** | フォローアップ。**§9 の member 5 境界宣言を一部見直す材料**（楽譜側は gate_synth 改変なしで閉じられる可能性がある） |

3 件目は §9 の「member 5 は gate_synth の I/O 構造変更が必要」という境界宣言の
うち**楽譜モジュール分については前提が誤っていた**可能性を示す。
→ **再検証の結果、指摘が正しかった**（楽譜は gate_synth 改変なしで照合できる）。
ただし**照合を実装しただけでは member 4 は閉じない** — 正本 4 本は凍結版が生んだ
もので当該検査を通っていないため、8 巡目で「実装済み・未実測」へ差し戻した
（§9 の注記 / §9-3）。

## 9-2. レビュー採否の基準

指摘を無差別に取り込むと、意図的な設計判断まで「修正」されて設計が濁る。
本 PR で直すのは次のいずれかに当たるものだけとする:

1. **クリティカルな欠陥** — 誤った結論を出す / 検証が通ったように見えて通っていない
2. **意味的汚染** — 未校正の値に知覚名を付ける等、後段の schema や
   LearningTransition へ誤った意味を持ち込む
3. **将来のバグ要因** — 下限ガード欠如・stale fixture・TOCTOU など、条件が
   変わった時に静かに壊れる配線

それ以外は**設計範囲**として据え置く。本 PR で据え置いたもの:

- **probe が monkeypatch であること** — gate_synth の read-only 契約を守るため。
  製品形は `apply_control_profile(...)`（§5-2）で、probe を先にその形にすると
  製品設計を実測スクリプトの都合で決めることになる
- **消費バイト hash が 6 バッファ止まり** — embed / 音素辞書は gate_synth が
  パス read する。閉じるには gate_synth の I/O 構造変更が必要で read-only 契約に反する
  （射程は結果 JSON の `not_covered` に明記）。**楽譜モジュールは据え置きから外した** —
  フォローアップ PR で `load_song_module` の戻り値により閉じた（§9-3）
- **`gate_synth.py` の sha を CI で照合しない** — 活発に変更されるため偽陽性製造機になる。
  日付つき記録が測定時点を記録していれば provenance は足りる
- **`safe_range: null` / 耳判定 0 件のまま** — 埋めることがレビューの禁じた操作そのもの
- **`song_pad_*` 条件を残す** — 撤回した主張が何を測っていたかを名前で残すため

## 9-3. フォローアップ PR（2026-08-20・7/9 巡目の 4 件 + 昇格）

§9-1 で送りにした 3 件と、9 巡目の 1 件を閉じた。あわせて §8 の昇格フローの
最終段（PROVISIONAL → accepted）を実行した — **フローを新設した PR 自身が
最終段を実行していなかった**ため（冒頭の正直会計）。

| # | 指摘 | 対応 |
|---|---|---|
| 1 | Verify the score bytes returned by each synthesis（7 巡目 P2） | **採用**。`synth_once` が `load_song_module` の戻り値 digest を保持し、条件ごとに `consumed_score_sha256` として記録。`verify_consumed_score_bytes` で pin と突き合わせ、不一致は rc=1（fail-closed）。**ただし §9 member 4 の昇格は 8 巡目で撤回** — 正本 4 本は凍結版が生んだもので当該検査を通っておらず、保証を実測した走行が無い（実装済み・未実測） |
| 2 | Protect every derived acoustic input in the checker（7 巡目 P2） | **採用**。checker が保護リストを書き写すのをやめ、`ProbeConfig.protected_inputs()` を流用する単一ソースへ。`--acoustic-onnx` が `--acoustic-dir` の外にある場合の `*.phonemes.json` / `*.<spk>.emb` が自動で覆われる |
| 3 | Refuse to unlink unowned checker result files（7 巡目 P2） | **採用**。work ディレクトリに所有マーカー `.vgl0_checker_workdir` を導入（probe の条件ディレクトリと同じ様式）。マーカーが無く空でもないディレクトリは fail-closed。`run_probe` は所有 work 直下の結果ファイルしか unlink しない |
| 4 | Enforce each canonical result's full geometry（9 巡目 P2） | **採用**。正本 3 本に「条件集合が測定版の `CONDITIONS` と完全一致 / `single_condition is None` / `order == "forward"` / ファイル別 `notes_limit` 6・8・10」を機械検査。ラベル重複も落とす |

**ガードは実測なしで検査する**: 2 と 3 は「合成が終わってから入力を壊す」型で、
起動前に落ちれば足りる。`tests/test_vgl0_probe_guards.py` が onnxruntime を
import 用にスタブして preflight だけを叩く（ONNX 資産不要・CI で回る）。

### 実測を生んだ版の凍結（snapshot 機構）

上の 3 件はいずれも probe / checker 本体の編集なので、`pins.probe_script` /
`checker_script` の sha が正本 4 本とずれる。従来の検査は「pin == live 実装」
だったため、**再実測しない限り probe の欠陥を直せない**構造になっていた。
再実測の入力は run 6 の 40K ONNX + canon + vocoder で、いずれも repo に入らない
（Pod / Drive 律速）。

そこで `probes/snapshots/` に**実測を生んだ版をバイト同一で凍結**し、検査を
「pin は live か snapshot のどちらかで必ず実体へ解決できる」へ改めた
（運用 = `probes/snapshots/README.md`）。fixture drift の検知は失われていない —
編集して凍結も登録もしなければテストは落ちる。

**これは再実測の免除ではない**。live 実装は現在どの正本からも pin されていない
= **実測証拠が無い**。`index.json` の `live_unmeasured` に理由と再検証条件を
書くことをテストで強制し、`test_live_scripts_are_measured_or_declared_unmeasured`
が宣言漏れと（再実測後の）宣言残りの両方を落とす。

**未実測のまま「出力は変わらない」とは書かない**: 追加したのは検査と preflight
だけで合成経路には触れていないが、それを**実測で確かめてはいない**。機構から
因果を組み立てて実測しない誤りは本記録が 3 度撤回した型なので、`live_unmeasured`
の `reason` にもそのまま書いた。再実測の手順は同ファイルの `revalidation`。

#### User 裁定: **時限措置として承認**（2026-08-20）

snapshot 機構は「pin == live」という 1 行の不変条件を多部品プロトコルへ置き換える
措置で、**恒久パターンではない**。User 承認の枠は 3 点。いずれも散文ではなく
`index.json` の `policy` に宣言し、テストが宣言の存在と内容を検査する:

| 枠 | 内容 | 強制方法 |
|---|---|---|
| 期限 | run 6 資産が再用意でき次第、live 実装で正本を再生成し凍結を廃する | 出口が機械強制（下記） |
| 1 世代 | 1 スクリプトにつき凍結は 1 世代まで。2 世代目が要る = 再実測を先送りしている合図 | 台帳検査が 2 世代目を弾く |
| 置換 | CI が履歴を取れるようになったら **named commit の git blob 照合**へ置換（重複バイトがゼロになる） | STATUS のキューへ登録 |

**出口は運用者の善意に依存しない**: 再実測すると `正本が live sha を pin →
帰属検査が落ちる → 名前を外す → measured_results が空で落ちる → 行削除 →
実体とディレクトリの突き合わせで削除` の連鎖が起き、**凍結物は放置できない**
（放置すると CI が赤くなる）。

**採用時に自覚している代償**（`policy.known_costs` に機械可読で保持）: 検査の
比重が「実測の健全さ」から「台帳の整合」へ寄ること / **本 PR の指摘 18 件の
うち約 8 件は本機構自体の穴**で直した欠陥より多くを持ち込んだ側面があること /
凍結コピーが lint・リファクタの対象外になること / 未実測の live 実装が正本に
居座ること。

### セルフレビュー + bot レビュー 1 巡目（snapshot 機構への指摘 6 件・全採用）

snapshot 機構は**本 PR で新設した仕組みそのもの**なので、導入と同じ巡で穴を
潰した。Codex 3 件 + セルフレビュー 3 件、見送り 0。

| 出所 | 指摘 | 対応 |
|---|---|---|
| Codex P2 | 台帳の `file` に `../` や絶対パスが入ると registry の外を hash して「コミット済み実装」として通る | `_snapshot_path()` で字句検査（区切り文字・絶対パス）+ 解決後の `is_relative_to(SNAPSHOTS)` の二重で弾く。台帳を読む全経路をこの関数へ集約 |
| Codex P2 | `schema` 判別子を宣言しておきながら読み飛ばしている | `_snapshot_index()` が `vgl0-probe-snapshot/0.1` を検査してから中身を消費 |
| Codex P2 | `measured_results` が**存在確認だけ**。正本の一部を live 版で再生成すると、古い snapshot が「この版が生んだ」と偽り続ける | 各 `measured_results` について、その正本が pin する probe/checker sha と `entry["sha256"]` の一致を検査。部分再実測での帰属汚染を弾く |
| セルフ | **ruff が凍結物を lint 対象にしていた** — ルール追加や自動修正でバイトが 1 文字変わるだけで全 pin が壊れる（「直すほど壊れる」形で詰む） | `pyproject.toml` の `extend-exclude` で `probes/snapshots/` を lint 対象外に。除外が実際に効くことを一時ファイルで実地確認 |
| セルフ | checker のゲート要約 `probe_runs` が**新しい楽譜バイトゲートを覆っていない** — rc では落ちるが「何を検査して PASS にしたか」の証跡に残らない | `probe_run_failures` を 2 ゲート走査へ一般化（欠落も fail-closed）。`probe_runs` に `consumed_score_ok` を追加し、fixture テスト側も在るときだけ検査 |
| セルフ | guards テストが checker の **import 副作用**（`sys.path` 挿入）に依存していた — fixture が path を戻すため実行順次第で ImportError | `chk.probe_mod` 経由へ変更 |
| セルフ | **正典記録の節順が壊れていた** — 本記録は §9-1 → §9-3 → §9-2、`s3_record` は 未転記の列挙が「Drive 実在確認」の見出し下に入り込んでいた（追記を anchor の直前に挿入した結果）。`AGENTS.md` §3-1 が正直会計の毀損として扱う自己矛盾に当たる | 両方を移動して節順を復元。s3 側は移動で前後が反転する参照語（下記→上記）も直した |

**3 件とも実地確認した**（台帳を改竄して検査が落ちることを確認 = schema 改竄 16 failed /
パス脱出 13 failed / 偽の帰属 1 failed、復元で 38 passed）。指摘を入れて緑のまま、
という「検査したつもり」を避けるため。

### 外部レビュー + bot 2 巡目（4 件・全採用）

| 出所 | 指摘 | 対応 |
|---|---|---|
| Codex P2 | **部分再実測で未実測宣言が消える** — 正本 1 本を live 版で作り直しただけで「実測済み」と判定され、残り 3 本の幾何が未実測だという唯一の警告が失われる | 宣言に `revalidation_results`（再検証で作り直す結果の**全数**）を持たせ、**全数が live sha を pin して初めて実測済み**とする。部分被覆では宣言が残る |
| Codex P2 | **所有マーカーの symlink 追従** — `.vgl0_checker_workdir` が保護対象入力への symlink だと `exists()` が「所有」と誤判定し、書き込みがリンク先を切り詰める | `lstat` で通常ファイルのみ所有と認め、書き込みは `O_NOFOLLOW`。**probe 側の `.vgl0_probe_output` も同型だった**ので（`rmtree` が無関係ディレクトリを消しうる）ファミリーとして一括掃討 |
| 外部 | `live_unmeasured` を live SHA へ束縛する | 各宣言に `sha256` を持たせ、CI が `sha256(live) == 宣言 sha` を要求。**live を再編集したら古い未実測宣言を流用できない** |
| 外部 | `consumed_*` 検査を**完全集合一致**にする（在るキーだけ照合して PASS させない） | モデル側は `_CONSUMED_TO_PIN`、楽譜側は pins の `score_module_*` 全数を期待集合とし、`欠け/余分` を不一致に。記録ゼロの走行も不一致。指定どおり empty / 欠落 / 未知キー / digest 不一致 / 完全一致 の 5 通りをテスト化 |

外部レビューの 1・4・5（帰属検証 / パス封じ込め / schema 厳密化）は 1 巡目で対応済み。
6（昇格の順序）は §8 に前提条件として明記した。

**ミューテーション確認**: live 改変 + 宣言 sha 据え置き → 1 failed /
再検証セットの縮小 → 1 failed / checker・probe とも symlink マーカーを弾き
リンク先が無傷であることを確認。

### bot 3 巡目（2 件・全採用）

| 指摘 | 中身 | 対応 |
|---|---|---|
| Keep the Drive absence claim scoped to title searches | `s3_record` 側は「タイトル検索であって全件列挙ではない」と限定したのに、**`STATUS.md` に旧主張（全件走査・Drive に存在しない）が残っていた**。範囲を超えた negative で、まだ生きている Drive 回収経路を早々に塞ぎかねない | STATUS を「これらのクエリでは見つからない / Drive 経路は未確定」へ書き換え。1・2 の User 送りも**暫定**と明示。`AGENTS.md`「撤回・訂正の同語横断全数掃討」の適用漏れ（1 箇所直して他を残した）= こちらの掃討ミス |
| Avoid truncating hard-linked ownership markers | 2 巡目で symlink は塞いだが、**hard link は `lstat` でも通常ファイルに見え `O_NOFOLLOW` でも弾けない**。マーカー名が保護対象入力への別名だと `O_TRUNC` が共有 inode を切り詰める | (a) マーカーは**内容の判別子** `vgl0-owner-marker/1` で所有判定（型では区別できないので中身で見る）、(b) 書き込みを **`O_EXCL` の新規作成のみ**にし既存を一切開かない。checker は既存の自前マーカーを書き換えずに再利用 |

**掃討の教訓**: 2 巡目の symlink 修正は「リンク経由でマーカーを騙る」ファミリーの
**一部（symlink）しか塞いでいなかった**。`O_NOFOLLOW` を入れた時点で「リンク経由は
閉じた」と考えたが、hard link は別機構で通る。§9 の member 5 と同じく、**機構を
1 つ塞いで系統を閉じたと見なした**のが誤り。今回は型ではなく**中身**で所有を
判定する形にし、既存を開かない設計にして系統ごと閉じた。

### bot 4 巡目（2 件・全採用）

| 指摘 | 中身 | 対応 |
|---|---|---|
| Reject duplicate live-unmeasured script entries | `{d["script"]: d for d in ...}` の畳み込みが重複行を黙って捨てるため、**古い宣言と新しい宣言が同居**したまま検査が通る | 台帳の読み込み時点で一意性を検査。**1 件だけでなくファミリー全数**を列挙した: `live_unmeasured.script` / `snapshots.file` / `snapshots.(script, sha256)` / `measured_results` 内の重複 / 同じ結果が複数 snapshot に帰属 |
| Verify checkpoint bytes before recording sidecar hashes | **`s3_record` §7.3 で自分が書いた回収指示書が provenance 汚染の経路を作っていた** — 「`.sha256` を開いて中身をコピー（再計算不要・サイドカーが正本）」。サイドカーが古い / 壊れている / 別分割と取り違えられていると、`anchors_provenance` が実体と違う pin を恒久に束縛する | pin は**実体のバイトから**採り、サイドカーは `sha256sum -c` の照合相手に格下げ。照合できない分割は値を書かず「未照合」と残す（埋めれば表は揃うが、揃った表が嘘になる）。未転記 1 の記述も同語で掃討 |

**ミューテーション確認**: `live_unmeasured` 重複 → 17 failed / `measured_results`
重複 → 17 failed（復元で 39 passed）。

> **2 件目は「指摘の質が変わった」例**: 1〜3 巡目は本 PR が新設した機構への指摘
> だったが、4 巡目は**記録に書いた手順そのもの**の欠陥だった。実装だけでなく
> 「人間に渡す指示書」も provenance 汚染の経路になりうる — 指示書は実行されない
> ので CI では捕まらず、**読んだ人が汚染を実行してしまう**点でむしろ危険。

### bot 5 巡目（3 件・全採用）

| 指摘 | 中身 | 対応 |
|---|---|---|
| Confine result names to the records directory | `snapshots[].file` は 1 巡目で閉じたが、**`measured_results` / `revalidation_results` は同じ形のまま `RECORDS / name` へ join していた**。絶対パスや `../` が入れば records 外の JSON を読み、細工した外部ファイルが snapshot 帰属や再検証被覆を満たす | `_record_path()` を新設し、台帳由来の文字列がパスになる箇所を**全数**通す。契約の番人テストも追加 |
| Pin the probe module imported by the checker | checker 側の検証（衝突ガード・期待条件集合）は **import 時の in-memory 定義**で走るのに、結果に載る probe sha は**サブプロセスが報告した値**。import 後に live probe が編集されると、旧定義で検証した PASS が新しい sha へ帰属する | `_PROBE_SHA_AT_IMPORT` を import 前に固定し、**実行後の実体 / サブプロセス pin との三者一致**を publish 条件に（finding `checker_probe_module_pinned`） |
| Keep the Drive recovery route unconfirmed | STATUS は前巡で限定したのに、**`s3_record` 本体が「回収経路は依然として存在しない」と断定したまま**で、直後の限界注記と同一記録内で矛盾していた | 「見つからない = 未確定であって存在しないと確定したのではない」へ書き換え、User 送りも暫定と明示。evidence artifact の `conclusion` も同じ範囲へ限定 |

**同じ失敗を 3 度重ねた**: 「ファミリーを閉じたつもりで一部しか閉じていない」が
§9 member 5（共通理由の一括適用）→ symlink/hard link → `file` は閉じたが
`measured_results`/`revalidation_results` は残す、と 3 回続いた。さらに
「1 箇所直して同語の別箇所を残す」が STATUS → 記録本体で 2 回。
**今回は指摘 1 件につき「同じ形をした場所を grep で全数当たってから閉じる」を
実施**（`_record_path` の適用箇所 4 つ / 「存在しない」の同語掃討）。

### bot 6 巡目（1 件・採用）+ **TOCTOU 系統の終端宣言**

| 指摘 | 中身 | 対応 |
|---|---|---|
| Import the probe from the bytes you hash | checker が `read_bytes()` で hash した後、**Python が別途 read して import** していた。この 2 読み窓の間に差し替え、サブプロセスと最終再 hash の前に戻せば、3 つの hash が揃ったまま「checker 側の検証を行っていないコード」へ PASS が帰属する | `compile`/`exec` で**hash 済みバッファそのもの**をモジュール化（`sys.modules` へ登録）。パス経由の `import` を廃止し、「hash したバイト列 == 実行したバイト列」を構造的に成立させた |

**終端宣言（`AGENTS.md` §3-3 の様式）— 「ローカル FS を競合させる TOCTOU」系統**:

閉じたのは *checker が読む probe* の窓で、これは clean な修正が存在した。
**残る同型は `probe` 自身の self-hash**（`_SELF_SHA_AT_LOAD`）で、こちらは
**Python が `__main__` を実行するために読んだバイト列を、後から取得する移植的な
手段が無い**（`__loader__.get_data()` も再 read になる）。よってここは閉じない。

射程: 残存する脅威は「**手動実行中の作業ツリーへ並行して書き込める者**が、
2 つの read の間だけ差し替えて元へ戻す」もの。その能力がある者は
`tests/` も probe 本体も自由に書き換えられるので、この窓を塞いでも
provenance は守れない（防御が意味を持つ境界の外側）。本 probe は
**信頼できない入力を受け取らず、著者が自分のマシンで手動起動する研究計器**で
あることを前提として、ここを境界とする。

**再開条件**: (a) probe が信頼できない入力や CI の並行実行下で走るようになった、
(b) `__main__` の実行バイト列を移植的に取得する手段が確立した、(c) 実測で
**偶発的に**（攻撃者無しで）この窓を踏んだ事例が出た — のいずれか。

### bot 7 巡目（1 件・採用）+ **負の主張の限定は「見出しフィールド」で担保する**

| 指摘 | 中身 | 対応 |
|---|---|---|
| Scope the evidence purpose to the title queries | evidence artifact の `conclusion` / `limits` は限定済みなのに、**先頭の `purpose` だけが「Drive に無い」と無限定のまま**で、同一 artifact 内で自己矛盾していた | `purpose` を「Q1/Q2 では見つからなかった」へ限定。さらに `claim`（`assertion` / `scope` / `does_not_assert` / `recovery_route_status`）を機械可読で追加 |

**同型の失敗が 4 回目だったので、直し方を変えた**: 「Drive 負の主張の限定」は
STATUS → 記録本体 → `conclusion` → `purpose` と**4 箇所を 4 巡かけて individually
直している**。原因は「限定は散文で書く」設計にあり、消費者が**どのフィールドを
読むかで主張の強さが変わる**こと。今回は artifact へ `claim` を追加して、
**主張と射程を 1 つのフィールドに同居**させた（`does_not_assert` を明示する形）。
以後、負の所見は散文ではなくこの形で書く。

**全数監査を実施**: artifact の全文字列フィールドを機械的に走査して断定表現を
洗い（未限定は `purpose` 1 件のみと確認）、repo 全体を「Drive に無い / 存在
しない」で grep して残存ゼロを確認した。「grep で全数当たってから閉じる」を
今回は artifact 内部の**フィールド粒度**まで下ろしている。

### bot 8 巡目（1 件・採用）— **自分の申し送りに違反していた**

| 指摘 | 中身 | 対応 |
|---|---|---|
| Revalidate before closing the score-byte gate | 7 巡目で楽譜バイト照合を実装し §9 member 4 を「閉じた」としたが、**正本 4 本はいずれも凍結版が生んだもので `consumed_score_bytes_check` を持たない**（テストも skip する）。**fail-closed 保証を実測した走行が 1 つも無い**まま、記録が保証を主張していた | member 4 を「実装済み・未実測」へ差し戻し、終端宣言から外した（閉じたのは 1〜3・6〜9）。§9-3 の該当行にも撤回を明記 |

**これは s6 record の申し送り「対策は投入でなく効果で終端宣言する」への違反**で、
り→ん破綻の帰属撤回とまったく同じ機構（`score_d3_sustain.py` の docstring が
言う「主力データ」= **意図**を**効果**と読んだ）。**申し送りを書いた当人が次の
PR で同じ誤りを犯した**。

散文の戒めが効かなかったので構造で防ぐ: §9 の表は今後
**「実装済み」と「実測済み」を分けて書く**。実装だけで昇格させない。

### bot 9 巡目（1 件・採用）

| 指摘 | 中身 | 対応 |
|---|---|---|
| Require the complete revalidation result set | 被覆判定が `required` を**台帳の記載だけ**から導いており、正本が増えたり宣言から 1 件抜けたりしても「残りが未実測のまま被覆が満たされた」ことになる。`revalidation_results` は全数だという主張自体が未検査だった | `_canonical_results_for()` を新設し、**その script を pin している正本の全数**と宣言の完全一致を被覆計算の前に要求 |

宣言の内容ではなく **「宣言が全数であること」** を機械で検査する形にした。
ミューテーション確認: 再検証セットから 1 本抜く → 2 failed（復元で 41 passed）。

### bot 10 巡目（1 件・採用）+ **レビュー上限に到達**

| 指摘 | 中身 | 対応 |
|---|---|---|
| Require the complete measured-result set | 9 巡目で閉じた全数性の**鏡像**。`measured_results` は「載っている名前が正しいか」しか見ておらず、その版が生んだ正本が台帳から抜け落ちても素通りした | `_canonical_results_for(script, sha)` へ一般化し、**両方向**（`revalidation_results` / `measured_results`）の全数性を同じ helper で強制 |

**9 巡目と同時に閉じるべきだった**: 台帳の 2 つのリストはどちらも「全数」を
主張しているのに、9 巡目では片方だけ直した。**ファミリーの部分閉じが 5 回目**
（member 5 一括適用 / symlink→hard link / `file`→`measured_results` /
Drive negative の 4 箇所 / 今回の全数性 2 方向）。今回は helper を
双方向へ一般化して、**同じ不変条件を 2 箇所で別々に書かない**形にした。

**上限到達の宣言（CLAUDE.md「bot レビュー対応の運用」/ `AGENTS.md` §3-4）**:
本 PR の bot レビューは **10 巡に到達**した。以後の扱い:

- **実害基準を満たす指摘**（偽成功・データ破壊・provenance 汚染の**新しい具体
  経路**）は**巡数に関わらず採用する**（打ち切りは実害基準を上書きしない）
- **それ以外**（逓減領域・宣言済み範囲の再指摘・同型の言い換え）は個別対応を
  打ち切り、本節を参照する 1 行で退けたうえで **User へ引き継ぐ**

引き継ぎ時点の未閉鎖項目（いずれも実測律速で、コードでは閉じられない）:

1. **§9 member 4 = 実装済み・未実測**（楽譜バイト照合。正本再生成で昇格）
2. **§9 member 5 = 射程外**（話者 embed / 音素辞書。gate_synth の I/O 構造変更が前提）
3. **TOCTOU 系統の残り**（probe 自身の self-hash。移植的手段が無い = §9-3 の終端宣言）
4. **live probe / checker は未実測**（`live_unmeasured` に宣言・Pod/Drive 律速）

### bot 11 巡目（1 件・**実害例外として採用**）— マーカー同一性の系統を全数閉鎖

上限到達後の最初の指摘。**打ち切り規定は実害基準を上書きしない**ため、
巡数ではなく中身で判断した。

| 指摘 | 中身 | 判定 |
|---|---|---|
| Bind ownership markers to their directory | **正当な別マーカーへの hard link** は判別子を保つので `is_own_marker` を通り、無関係なディレクトリが「所有済み」と見なされて `order_forward.json` 等が unlink される（**データ破壊の新しい具体経路**） | **採用**（実害例外） |

**今回は 1 経路ではなく系統を閉じた**: マーカーを騙る経路は本 PR だけで
symlink（2 巡目）→ 保護対象への hard link（3 巡目）→ **正当な別マーカーへの
hard link**（11 巡目）と 3 度露呈しており、**1 つずつ塞ぐ限り必ず取り残す**。
所有判定を 4 条件の連言へ作り直した:

1. `lstat` が通常ファイル（symlink を弾く）
2. **`st_nlink == 1`**（hard link された実体を弾く）
3. 先頭が判別子（モデル・楽譜への別名を弾く）
4. 2 行目の `dir=` が**自分の親ディレクトリ**と一致（**よそのマーカーの
   hard link / コピー**を弾く）

副作用として work ディレクトリの移動・改名で所有が外れるが、fail-closed
（消さずに止まる）なので安全側。エラーメッセージに対処法を書いた。

**この 4 条件で「型・リンク数・内容・場所」の全次元を覆っている**。以後の
マーカー系指摘は、この 4 つのいずれにも当たらない**新しい次元**を示すもの
だけを採用する。

### bot 12 巡目（1 件・**実害例外として採用**）— member 5 の射程外宣言が事実誤認だった

| 指摘 | 中身 | 判定 |
|---|---|---|
| Use existing digest loaders before scoping inputs out | member 5 の「gate_synth の I/O 構造変更が要る」は**事実誤認**。`load_canon_phonemes_with_sha` / `load_own_phonemes_json_with_sha` / `load_speaker_embed_vector_with_sha` が既にあり、gate_synth 本体も使っている。非 hash 版で読むと `collect_pins` の後に差し替えられたバイトで合成しても rc=0 のまま通り、**使っていないバイト列に provenance が束縛される** | **採用**（偽成功の具体経路 = 実害例外） |

**一次ソースで確認してから採用した**（`grep -n "_with_sha" gate_synth.py` →
562/567/579/585/675/682 行に定義、1745/1883/1920 行で本体が使用）。指摘を鵜呑みに
しない、は本トラックの反復教訓なので今回は先に検証した。

対応: 3 つを `*_with_sha` へ切り替え、`consumed_input_bytes_check`（キー集合の
完全一致 + digest 照合 + fail-closed）を追加。checker のゲート要約にも 3 つ目と
して配線。**`consumed_model_bytes_check.not_covered` は空になった**。

**同じ member 5 で 2 度、API を確かめずに「構造変更が要る」と宣言していた**
（7 巡目 = 楽譜 / 12 巡目 = 残り 3 入力）。どちらも **grep 1 回で反証できる誤り**
だった。教訓を §9 の注記へ: **「この層は閉じられない」と書く前に、その層の公開
API を grep する**。

**ただし member 4・5 とも「閉じた」とは書かない** — 実装しただけで正本は
凍結版が生んだものであり、8 巡目に学んだとおり**実装は効果ではない**。両者とも
**実装済み・未実測**とし、再実測で昇格する。

> **作業事故 1 件（正直会計）**: ミューテーション確認の後始末に
> `git checkout -- <probe>` を使い、**同ラウンドの未コミット変更（consumed キー集合
> 完全一致 + symlink マーカー修正）を消した**。復旧して再適用済み。検査のために
> 触ったファイルを戻すときは、コミット済み版へ戻す操作ではなく**作業コピーの
> バックアップから戻す**こと。
