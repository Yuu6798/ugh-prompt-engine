# RunPod 無人測定 probe 運用 runbook（実行者非依存）

短時間・無人・単発の GPU 測定 pod（「probe」）を RunPod 上で安全に回すための
運用知見集。**全項目が 2026-08-23 の実測に基づく**（PR #312: run4
export-device probe・4 起動 計 ≈ $0.38。動く実例 =
`scripts/run4_export_device_probe_pod.sh` / `scripts/run4_export_device_probe_runner.py`）。
長時間学習 run の無人実行は `S4_RUN5_RUNBOOK.md`（run5 bootstrap 10 段）が正本で、
本書はそれを置き換えない。数値・挙動はすべて実測時点のもので、RunPod 側の
仕様変更で変わり得る（再確認の一次ソースを各節に明記）。

## 1. 回収チャネル（最重要の落とし穴）

- **`https://<podId>-<port>.proxy.runpod.net` の前段 Cloudflare は
  `User-Agent: Python-urllib/*` を UA 文字列のみで一律 403（body =
  `error code: 1010`）にする**。curl 既定・Mozilla・空文字列 UA は同一 URL に
  即 200（UA 分離実験で確定）。**Python urllib を使うクライアントは全リクエストに
  カスタム UA を注入すること**（runner は `_build_request()` に集約し、UA なし
  リクエストの構築経路自体を排除 + source-scan テストで再発防止）
- proxy の応答コードは pod 内サービスの readiness 情報を持たない:
  403（UA ban）は listener の有無と無関係で、pod 削除後に 404 へ変わる。
  「403 → 404」の遷移だけでは何も診断できない
- チャネル性能（実測）: listener は pod 起動 3 秒で bind 可・warm-up なし・
  5MB ≈ 1 秒で転送可（WAV/ONNX 回収に十分）。`python3 -m http.server` の
  directory listing は href scrape で機械回収できる

## 2. 回収アーキテクチャ原則

1. **結果サーバは t=0 から常時 serve する**（exit trap でのみ開く設計は
   「課金を全額消費した後にしか疎通検証できない」構造欠陥。初回 run で
   $0.25 の測定データを喪失した実測起源）。heartbeat ファイル（UTC + stage）
   と console log の live コピーを同じディレクトリで配信する。runner は
   600s 無応答で明示警告を出す（**自動 terminate はしない** — 実装は
   operator abort の提案まで）。無人運用では**操作側**（実行エージェント /
   オペレータ）が「起動後 10 分で heartbeat 不通なら **stop**（terminate では
   ない — terminate = stop + DELETE は volume ごと消すため、proxy 障害で
   結果だけ見えていない場合に回収可能な証拠を破壊する）」を運用規約として
   課す。stop 後に状況を確認し、回収不能と確定してから delete する。
   PR #312 の実行ではこの早期打ち切り規約で損失を ~$0.05 級に束縛した
2. **volume を保険に張る**（`volumeInGb` ≥ 10 + `volumeMountPath: /workspace`）。
   container disk のみだと pod 停止と同時に結果が消滅する。回収失敗時は
   **delete でなく stop で保持** → 再起動サルベージ → 回収確認後に delete
3. **volume 永続化の副作用**: 再起動時に前 run の完了マーカー
   （status.json 等）が残存し、runner が stale を今回の成果として受理し得る。
   pod script は trap 確立直後・サーバ起動前に既存成果物を
   `prev_run_<UTC>/` へ退避する（マーカー優先）。runner 側も `--out` 再利用時に
   完了マーカー 2 種（status.json / probe_results.json）を `prev_fetch_<UTC>/`
   へ退避する。**既知の残余**: runner の退避は**マーカー 2 種のみ**で、診断系
   ファイル（venv_b_env.json 等・成功判定を gate しない）は前 invocation の
   ものが残り新 run の結果と混在し得る — 確実な分離が要る場面では
   **`--out` に毎回新規ディレクトリを渡す**のが正（退避機構は保険であって
   分離保証ではない）
4. **成功条件の要求集合は閉世界契約（固定定数）で書く** — `AGENTS.md` §8
   「回収・検収系の成功条件は閉世界契約で書く」が正本。listing 由来・record
   由来の要求集合は publish 失敗・schema 差・digest 欠落で空洞化し、
   「何も検査せず exit 0」の偽成功経路になる
5. 回収したバイナリは **sha256 を手元で再計算して記録値と照合**してから
   pod を delete する（terminate 後は検証可能な証拠が恒久喪失する）

## 3. 課金制御（構造保証と運用手順を区別する）

- **同時 1 pod 厳守は運用手順**（構造強制ではない — runner は launch 前の
  既存 pod 照合を行わない）: 操作側が launch 前に GET /pods で同名 pod の
  不在を確認し、作成応答の pod id を即ファイル記録する。作成 POST の曖昧な
  失敗（timeout/5xx）後は必ず GET /pods で既存 pod を確認してから再試行。
  **構造保証されているのは** id を抽出できない 201 応答を成功扱いにせず
  exit する点のみ（課金 pod が存在するのに自動化が id なし成功を記録する
  経路の閉塞）
- **POST /pods は自動リトライしない**（タイムアウト後の再送で二重課金 pod が
  生まれる）。GET/DELETE はリトライ可。ネットワーク例外の捕捉は
  `(URLError, TimeoutError, ConnectionError)` のタプルで統一（`URLError` 単独では
  素の `TimeoutError` を取り逃がし stop→DELETE fallback が届かない）
- **self-stop は全 exit 経路で発火 + 平常時は二重化**: trap の `runpodctl stop`
  は 5 回リトライ・失敗時は明示警告。独立の wall-clock watchdog（例 10800s）を
  併設し、**trap → watchdog の順で、いずれも最初の exit 経路（env 検査・
  pin guard 含む）より前に確立する**（trap 設置前に exit し得る経路が
  1 つでもあると self-stop 契約が破れる。実装実例 = pod.sh は trap 直後に
  watchdog を起動し、その後に guard 群を置く。**ただし既知の残余**: trap の
  on_exit が `$RESULTS` を要求するため `mkdir -p` が trap **より前**にあり、
  mount/権限起因の mkdir 失敗は無ガードで exit する — さらに起動ラッパの
  `bash … | tee` は pipefail なしのため、この失敗を wrapper 側 fallback stop も
  拾わない。正しいパターンは「`$RESULTS` に依存しない最小 trap（runpodctl
  stop のみ）を最初に張る → 可失敗なセットアップ（mkdir 等）→ 完全版 on_exit
  へ差し替え」で、次回改修の処方とする）。**既知の残余**: on_exit は
  回収窓を 3h 境界から守るため watchdog を kill してから serve するので、
  回収 hold 中〜stop リトライ全滅の間は backstop が無い（実装は明示警告
  止まり）。次回改修では kill でなく**期限延長**（hold + 余裕分の新 deadline で
  watchdog を再アーム）にして stop 成功まで backstop を維持するのが正
  （二重保証を回収 hold 中にも成立させる）
- **起動コマンドの curl は `--retry 5 --retry-all-errors` + ファイル落とし実行 +
  取得失敗時 self-stop**（`curl | bash` 単発は取得失敗で空実行 exit 0 =
  課金だけ発生する silent no-op になる）。**既知の残余**: この pre-script
  失敗経路の fallback stop は**単発**で、script 内の 5 回リトライも watchdog も
  （script が走っていないため）存在しない — curl 全滅 + stop 一過性失敗の
  複合時は pod が課金を続け得る。次回改修では注入コマンド内の fallback stop
  自体をリトライループにするのが正。当面の防波堤は操作側の起動後 10 分
  heartbeat 確認（§2.1 — この経路では heartbeat が一度も立たないため必ず
  検出される）

## 4. fail-closed 測定規律

- 全素材（ckpt・config・canon・vocoder・repo checkout 側の消費スクリプト
  blob・DiffSinger revision）を埋め込み pin と sha256 照合し、1 件でも
  不一致なら**測定せず** self-stop。**被覆の境界**: entry script 自体は
  この pin 照合の対象外で、バイト検証なしに実行される — その不変性は
  raw.githubusercontent.com を**不変な full 40-hex commit sha** で fetch する
  運用にのみ依存する。したがって `--script-commit` に branch/tag を渡すことは
  禁止（可変 ref だと preflight と pod 側で異なるバイトが走り得る）。
  runner は現状この形式を機械検証しない（残余）— 次回改修で
  `^[0-9a-f]{40}$` 検証、あわせて entry script の sha256 を起動側で照合する
  方式への強化が正
- GPU 前提の測定は `torch.cuda.is_available()` を明示ゲートし、偽なら
  **CPU へ黙ってフォールバックせず停止**（測定条件の静かな変質の防止）
- runner の fetch は status.json の `status` を解析し、`failed` なら診断を
  全回収した上で **exit 非ゼロ**（自動化への偽成功報告の禁止）

## 5. CUDA ホスト差（同一イメージでも割れる）

- **`nvidia-smi` 成功 ≠ venv の CUDA init 成功**。実測: 同一イメージ・同一
  GPU 種（RTX 3090）で、driver 580.95.05 のホストは venv torch 2.13.0+cu126 が
  「CUDA unknown error」で `is_available()=False`、driver 580.65.06 の別ホストは
  同一手順で可用。機構は未特定（ホスト依存要因の存在までが実測）
- 対策: **CUDA 診断ステージ**（/dev/nvidia*・env・ldconfig・system/venv 両
  torch の init エラー全文・pip freeze を常時記録）+ **3-way ゲート**
  （venv 可 → 本計画 / system torch のみ可 → フォールバック昇格 + スタック内
  CPU 対照 / 両方不可 → 専用 stage 名で早期停止 = 別ホストで再起動）。
  再起動は 1 回まで・2 ホスト連続不可なら停止して人間へ

## 6. RunPod REST の実測事実（再確認先: `GET /v1/openapi.json`）

- pod 作成: `POST https://rest.runpod.io/v1/pods`（Bearer 認証）。
  `ports` は文字列配列（例 `["8000/http"]`）・`dockerStartCmd` は文字列配列。
  201 でも payload の妥当性が全検証されるとは限らない
- CPU pod は `containerDiskInGb` ≤ 20 の hard cap（超過は 500）。
  `cpuFlavorIds` 列挙: `cpu3c, cpu3g, cpu3m, cpu5c, cpu5g, cpu5m`。
  チャネル系の切り分け実験は最安 CPU pod（~$0.01）で先に行う価値がある
- REST v1 に pod ログ取得エンドポイントは無い（openapi 全走査で確認）——
  ログが要るなら pod 自身に配信させる（§2 の t=0 serve）
- image は tag 参照で digest pin ではない = 環境再現性の保証境界
  （凍結記録の再現性主張は「同一 pod 内 bit 一致」までに留める）

## 7. 被覆主張の読み方（境界宣言）

本書の「保証 / 必ず / 全〜」系の記述は、各節の**【既知の残余】と対で読む**。
保証の主体（構造 / 運用）と被覆の境界（script 内 / pre-script / 退避対象 /
pin 照合対象）は各節に明記済みで、現時点の既知残余は 6 件（watchdog kill 後の
回収 hold 中・pre-script fallback stop の単発性・`--script-commit` 形式の
機械未検証・trap 前 mkdir の無ガード exit・退避のマーカー限定・fetch 成功
ゲートに診断系ファイルが含まれない——診断系を best-effort とするのは意図的
裁定だが、env 記録（venv_a_env.json 等）は正典記録の environment 節の材料に
なるため、**成功時も delete 前に env/診断ファイルの取得を操作側が確認する**）。実装と
被覆主張の乖離が新たに見つかった場合は、主張を絶対化する方向ではなく
**残余一覧への追記**で扱う（PR #313 レビュー第 1〜5 巡で本会計方式を確立。
これをもって「被覆主張 vs 実装」系統の指摘対応は終端とする）。

## 8. 凍結記録の訂正手順（三面協調更新 = マージ前の記録に限る）

**適用境界（2026-08-24 確立 = PR #314 レビュー第 2 巡）**: 三面協調更新が
許されるのは**その記録がまだ main へマージされていない間**（同一 PR 内での
レビュー採用訂正）だけである。**マージ済みの凍結記録はバイト無改変が原則**
——immutability テストの不変条項（再計測なしのハッシュ更新は不可）と
User 裁定 C（2026-08-21「確定記録は改善方向でも書き換えず attestation
止まり」）に従い、マージ後の訂正・再分類は記録本体を書き換えず**裁定記録
（例: `DEBT_ADJUDICATION_v1.1.md` §8）+ 台帳の参照**で表現する。

マージ前の凍結済み正典記録（immutability テストの二重アンカー下にあるもの）を
レビュー採用で訂正するときは、**(1) record 本体 → (2) 台帳
`evidence_delivered` の sha256 → (3) `test_committed_artifacts_immutable.py` の
`FROZEN_SHA256` 定数**の三面を同一コミットで更新する（PR #312 で 2 回運用し
機能を確認——いずれも同一 PR 内・マージ前の訂正であり上記境界の内側）。三面は**更新対象**の列挙であって掃討範囲ではない——訂正後の
掃討 grep は `AGENTS.md` §8（撤回・訂正の同語横断全数掃討）の定めどおり
訂正**前**の語彙で **repo 全体**（docs・データファイル・ソース・テストを含む）
に対して行う（report 要約・サマリー・テストの期待値など三面の外に複製された
旧主張が残ると、そこから撤回済み主張が復元されるため）。
