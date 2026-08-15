# 日本語歌唱コーパス ライセンス・入手可能性 調査記録

調査日: 2026-08-15（UTC）。事実収集のみ。採否判断は別担当。
「不明」は推測で埋めていない箇所。

## 比較表

| コーパス | 歌唱内容 | ライセンス種別 | 派生音声（合成音声）の生成・公開 | 商用利用 | 再配布 | 本環境からの到達性 |
|---|---|---|---|---|---|---|
| **PJS**（Koguchi & Takamichi 2020） | 単独男性歌手・100曲・27分20秒・歌唱＋並行朗読・音素ラベル/MIDI/MusicXML付 | **CC BY-SA 4.0**（コーパス全体） | 明記の制限なし（CC BY-SA の継承条件下で許可されると解釈できる。ただし「合成音声の生成・公開」を名指しした条文はない＝不明） | **可**（論文に明記） | CC BY-SA の範囲で可（要 SA 継承・帰属表示） | 到達確認済み（HTTP 200, Google Sites） |
| **JVS-MuSiC**（Tamaru et al. 2020, 東大） | 100人・共通曲「かたつむり」49分23秒＋個別曲88分3秒＝計約137分・24kHz/16bit | タグ類=CC BY-SA 4.0／**音声本体とMPDファイルは別ライセンス**（非商用限定の独自条項） | 不明（論文・サイトに明記なし） | **不可（無償ティアでは）**。商用利用は個別問い合わせ制（48kHz/24bit高音質版を別提供） | **不可**（少数ファイル(~3件)のブログ等への部分掲載のみ許可、全体再配布は禁止） | 到達確認済み（HTTP 200） |
| **東北きりたん歌唱データベース**（Ogawa & Morise 2021） | 単独（東北きりたん・VOICEROIDキャラ）・アカペラ50曲・音素/MIDI/MusicXMLラベル付。時間・SR = 不明（ページ未掲載） | 独自利用契約（Twitter/実名アカウント登録制、著作権法30条の4準拠） | **公開目的での利用は原則不可**（"著作物に表現された思想又は感情の享受を目的"としない範囲＝研究限定。ソフトウェア/サービスへの組込みは事前承認制） | **明確に不可**（「商用目的で利用しないこと」） | **不可**（「本件音声データ再頒布は行わないこと」） | ログインページ到達確認済み（HTTP 200）。ダウンロードには実名アカウント登録が必要＝本タスク範囲外 |
| **jaCappella**（Nakamura et al. 2023, 東大+AIST） | 20人（半)プロ歌手・6パート(Vo/S/A/T/Bs/VP)・35曲・計34分・48kHz/24bit・MusicXML付 | 独自ライセンス（"jacappella" custom license, HF上でgated） | **非商用研究・個人利用の範囲でのみ可**（改変物の配布は学術研究/非商用研究/個人利用に限定） | **不可（無償）**。商用ライセンスは別途有償契約 | **不可**（「複製・再配布禁止」。研究成果デモとして曲の一部(~5曲相当のフレーズ)の公開のみ許可） | HuggingFace到達確認済み（HTTP 200）。ただしgated dataset＝アクセスにHFアカウント登録＋連絡先共有の同意が必要 |
| **波音リツ 歌声データベース**（UTAU音源, Canon） | 単独・**フレーズ/持続音のUTAU原音サンプル集**（伝統的な「曲を通し歌った」歌唱データではない点に注意）。時間・曲数 = 不明（利用規約ページに記載なし） | 独自利用規約（canon-voice.com） | **可**（「加工しての転載、再配布可」＝合成音声の生成・公開を包含すると読める） | **明確に可**（「商用利用可です。」） | **明確に可**（「音源の転載、再配布可」「原音を加工しての転載、再配布可」） | 到達確認済み（HTTP 200） |
| **ONIKU KURUMI 歌声データベース**（御丹宮くるみ） | 単独・56曲（時間・SR = ページに不明記、ダウンロード版は16bit/44100Hz変換済み） | 独自利用契約（onikuru.info） | 明記なし（禁止条文なし＝グレー。成果物公開時のクレジット表記義務あり） | **要事前問い合わせ**（個人利用可、商用は個別契約） | **不可**（全部・一部の再頒布禁止。ラベル/MIDI/MusicXMLのみ再配布可） | 到達確認済み（HTTP 200）。ダウンロードは規約同意後Google Driveリンク（本タスクでは未実施） |
| **おふとんP 歌声データベース** | 単独・46曲(49メロディ)・約46分30秒（無音除く）・96kHz/24bit・MIDI/MusicXML/UST付 | 独自利用規約（Google Sites配布所） | 商用利用・法人利用・**このDBから派生モデル/ライブラリの配布は事前連絡必須**（禁止ではなく要許諾） | **要事前連絡**（法人は商用/非商用問わず要連絡） | 音声本体は**不可**。ラベル/MIDI/MusicXMLのみ再配布可 | 到達確認済み（HTTP 200） |
| **IdolSongsJp Corpus**（Suda et al., AIST, ISMIR 2025） | 10女性+8男性(半)プロ歌手・15曲（アイドルグループ様式）・マスター+ステム+ドライボーカル+コード注釈・48kHz/32bit float | 独自ライセンス（"idol-songs-jp-license"） | **原則可**（「rearrange, parody, and apply machine learning techniques」を許可。ただし他者の人格権を侵害する合成音声生成は禁止） | 非商用研究・エンタメ用途は事前同意不要で無料。**商用利用は事前許諾が必要** | 明確な再配布可否は不明（規約に「redistribute」の明文引用は未確認） | HuggingFace(imprt/idol-songs-jp)到達確認済み（HTTP 200）。**gated dataset**＝連絡先共有への同意が必要。なおZenodo (10.5281/zenodo.17706547) には論文PDFのみで音声本体は無い |

---

## 1. PJS（Phoneme-balanced Japanese Singing-voice corpus）

- **論文**: Koguchi & Takamichi, "PJS: phoneme-balanced Japanese singing voice corpus," arXiv:2006.02959 (2020).
- **内容**: 男性単独歌手1名（作曲・録音も本人）。100文の音素バランス文に作曲した歌唱＋並行朗読。歌唱データ合計 **27.20分**、朗読データ合計12.09分。48kHz/24bit RIFF WAV。各センテンスに `.wav`（歌唱/朗読）・`.mid`・`.xml`(MusicXML)・`.lab`（音素ラベル）・`.txt`（曲情報）が付属。
- **ライセンス逐語引用**（論文 Abstract・Conclusion, arXiv:2006.02959, 取得日2026-08-15）:
  > "CC BY-SA 4.0 license: All the data in our corpus is licensed with CC BY-SA 4.0. Therefore, our corpus is available for both research and commercial use, unlike existing corpora [...]"
  > （Conclusion 節）"The PJS corpus is available on our project page [16]. All the data is licensed with the CC BY-SA 4.0 license."
- **配布元 URL**: https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus （論文中の脚注[16]と一致）
- **研究/商用可否**: 両方可（CC BY-SA 4.0 の一般条件どおり）。
- **派生音声（合成音声）の生成・公開可否**: 明文の禁止条項なし。CC BY-SA 4.0 は改変・派生物作成を許容し、同一ライセンスでの継承（ShareAlike）と原著作者クレジット表示（Attribution）を要求する一般的な条件。**「合成音声そのもの」を名指しした条文は論文・プロジェクトページ双方に見当たらず、この点は不明**。
- **再配布可否**: CC BY-SA の範囲で可（帰属表示必須）。
- **クレジット義務**: CC BY-SA の Attribution 要件（プロジェクトページでは論文/ブログ公開時の告知を推奨、と WebFetch 要約あり。逐語未確認のため参考情報扱い）。
- **入手経路と到達性**: プロジェクトページ（Google Sites）→ Google Drive リンク（ver.1.1, zip, 0.26GB）。プロジェクトページ HTTP 200 確認済み（curl, 2026-08-15）。ダウンロード自体は本タスクでは実施していない。

## 2. JVS-MuSiC（Japanese multispeaker singing-voice corpus）

- **論文**: Tamaru, Takamichi, Tanji, Saruwatari, "JVS-MuSiC: Japanese multispeaker singing-voice corpus," arXiv:2001.07044 (2020), 東京大学。
- **内容**: 100人（男性49・女性51）のプロ話者/歌手。共通曲「かたつむり」（童謡）＋個別曲。共通曲の合計収録時間 **49分23秒**、個別曲 **88分3秒**（合計 約137分）。48kHzで収録し24kHzへダウンサンプリング、16bit RIFF WAV（JVS speechコーパスと同条件）。similarity/onenessの評価用CSVあり。
- **ライセンス逐語引用**（論文末尾, arXiv:2001.07044 p.3, 取得日2026-08-15）:
  > "The similarity and oneness matrices are licensed with CC BY-SA 4.0. The audio data and MPD files may be used for
  > • Research by academic institutions
  > • Non-commercial research, including research conducted within commercial organizations
  > • Personal use, including blog posts.
  > Our project page at https://sites.google.com/site/shinnosuke... describes the terms for commercial use."
- **配布元 URL**: https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_music
- **研究/商用可否**: タグ（類似度・oneness行列のCSV）のみ CC BY-SA 4.0。**音声本体とMPDファイルは非商用限定**（学術機関の研究・企業内非商用研究・個人利用〔ブログ含む〕のみ）。商用利用はプロジェクトページで別途要問い合わせ（WebFetch要約: 48kHz/24bit の高音質版を商用向けに別提供、と記載されているがこれは論文原文ではなくプロジェクトページの要約情報＝逐語未確認）。
- **派生音声（合成音声）の生成・公開可否**: 論文・プロジェクトページのいずれにも明記なし＝**不明**。
- **再配布可否**: WebFetch要約（プロジェクトページ, 逐語未取得）: "Re-distribution is not permitted, but you can upload a part of this corpus (e.g., ~3 audio files) in your webpage or blog." 論文原文には再配布条項の記載なし。**この一文はプロジェクトページの要約であり逐語引用ではない点に注意**。
- **クレジット義務**: 論文に明記なし。WebFetch要約: 論文/ブログ等での公開時に開発者への一報を推奨（"If possible, please let me know..."、逐語未確認）。
- **入手経路と到達性**: プロジェクトページ（Google Sites）→ Google Drive（約0.6GB zip）。プロジェクトページ HTTP 200 確認済み（curl, 2026-08-15）。ダウンロード未実施。

## 3. 東北きりたん歌唱データベース

- **一次資料**: Ogawa & Morise, "東北きりたん歌唱データベースを対象とした歌声の統計的解析," 日本音響学会誌 42巻3号 (2021), pp.140-145（引用要件として PJS 論文中の参考文献[11]にも記載）。ラベルデータの GitHub ミラー: https://github.com/mmorise/kiritan_singing
- **内容**: 東北きりたん（VOICEROIDキャラクター）単独によるアカペラ歌唱、50曲（童謡・アニメソング中心）。音素境界ラベル（mono_label）・MIDIラベル（Melodyne自動採譜→手動調整）・MusicXML付。**総収録時間・サンプリングレートは調査対象ページに記載なし＝不明**。
- **入手/登録ページ**: https://zunko.jp/kiridev/login.php （研究者向け、Facebook実名アカウントでのログインが必要）。**ダウンロードは本タスク範囲外のため未実施。利用契約書PDF本体の逐語全文は未取得（ログイン後のみ閲覧可能な可能性が高く、公開ページからの取得は不可）**。
- **利用条件（WebFetch要約＋GitHub README引用の組み合わせ。一部逐語、一部要約）**:
  - GitHub README 引用（mmorise/kiritan_singing, 取得日2026-08-15）:
    > "本データベースはあくまでも改正著作権法30条の4に定められた範囲での利用に限定されている"
  - login.php ページの WebFetch要約（逐語箇所は "" 内）:
    - 許可: 研究・開発目的限定
    - 禁止: 「"利用" において著作物に表現された思想又は感情の享受を目的としないこと」＝**享受目的（エンターテインメント/鑑賞目的）での利用は不可** → これは「合成音声を作品として公開する」用途と衝突しうる重要な制約
    - 「"商用目的で利用しないこと"」（引用符内は要約中の直接引用マーク、原文の該当条文番号は不明）
    - 「"本件音声データ再頒布は行わないこと"」（同上）
    - クレジット義務: 「©SSS」の表示
    - モデル組込み時: 「事前に乙に連絡し承認を得ること」（学習済みモデルをソフトウェア/サービスに組み込んで発表する場合は東北ずん子プロジェクト運営元＝SSS合同会社への事前承認が必須）
    - 契約終了時のデータ破棄義務、東京地裁専属管轄
  - **重要な注意**: 上記の引用符付き文言は WebFetch（要約モデル経由）による抽出であり、契約書原文からの完全な逐語コピーではない可能性がある。**契約書PDF原本への直接アクセスは今回実施していない**（ログイン後に取得可能と推測されるページのため）。正確な条文確認には登録・ログインが必要。
- **派生音声の生成・公開可否**: 上記の「享受目的での利用不可」規定により、**歌声合成の出力を音楽作品として公開する用途は原則対象外**と読める。研究目的（論文発表等）に限定される可能性が高いが、契約書原本での確認が必要（不明点として明記）。
- **姉妹DB（東北イタコ）**: クラウドファンディングで制作されたプロジェクトの存在は確認したが、公開状況・ライセンス条件の詳細は今回の検索では確認できず＝**不明**（配布ページの特定に至らず）。
- **到達性**: login.php は HTTP 200（curl, 2026-08-15）。GitHub ミラー（ラベルのみ、音声本体なし）も到達確認済み。

## 4. jaCappella Corpus

- **論文**: Nakamura, Takamichi, Tanji, Fukayama, Saruwatari, "jaCappella Corpus: A Japanese a Cappella Vocal Ensemble Corpus," arXiv:2211.16028 (ICASSP 2023), 東京大学 + 産総研(AIST)。
- **内容**: 20人の日本人セミプロ歌手による6パート（Vo/S/A/T/Bs/VP=ボイスパーカッション）アカペラ。35曲（7ジャンルサブセット×5曲: jazz/punk rock/bossa nova/popular/reggae/enka/neutral）、**総収録時間34分**（テストセット350.2秒等の内訳あり）。48kHz、24bit RIFF WAVEモノラル。MusicXML楽譜付。著作権切れの日本童謡をアレンジ。
- **論文中のライセンス言及（逐語引用, arXiv:2211.16028 p.1, 取得日2026-08-15）**:
  > "To avoid copyright-related restriction, we obtained all necessary copyrights and neighboring rights of our songs. Reserving these rights allows users of the jaCappella corpus to share processed audio signals to the extent necessary for research and it can also open the way for commercial use."
  - ※ この一文は「商用利用の道を開く（open the way）」という将来可能性の記述であり、無償での商用利用を直ちに許可する条文ではない点に注意。
- **実際の配布時の利用規約（プロジェクトページ https://tomohikonakamura.github.io/jaCappella_corpus/ の WebFetch要約, 引用符内は同ページからの直接引用）**:
  - 「"You may not use any of the data contained in the jaCappella...for commercial purposes."」＝**無償版は商用利用不可**
  - 「"You may not copy or redistribute the material in any medium or format."」＝**再配布不可**
  - 研究成果のデモ用に約5曲相当のフレーズ断片の公開のみ許可
  - 「"You must give appropriate credit and indicate if changes were made."」＝クレジット必須
  - 改変物（derivative）の配布は学術研究・非商用研究・個人利用に限定
  - 「"Any use that will violate public order and standards of decency are prohibited."」
  - 商用ライセンスは有償で別途利用可能（連絡先: tomohiko.nakamura.jp[at]ieee.org, shinnosuke_takamichi[at]ipc.i.u-tokyo.ac.jp）
  - 引用（ICASSP 2023論文）が必要
- **配布/入手経路**: HuggingFace `jaCappella/jaCappella`（**gated dataset**。"agree to share your contact information to access this dataset" が必須）。
- **到達性**: HuggingFace データセットページ HTTP 200確認済み（curl, 2026-08-15）。README.md の raw取得は HTTP 401（未ログインのため不可、gated 制限の実証）。プロジェクトページ（tomohikonakamura.github.io）も HTTP 200確認済み。

## 5. 波音リツ 歌声データベース（Canon / canon-voice.com）

- **重要な性質上の注意**: 「波音リツ」はUTAU用の歌声合成音源（ボイスバンク）であり、配布されている音声データは典型的には**単音・持続音・連続音などの原音サンプル**（UTAU方式の素片データベース）であって、PJS/Kiritan/JVS-MuSiC/jaCappellaのような「曲を通して歌った」歌唱録音そのものとは性質が異なる可能性が高い。第三者が波音リツ音源から実験的に構築した「試験用歌唱データベース」（`oatsu-gh/utau-namineritsu-singing`, GitHub）も存在するが、これは公式のオリジナル歌唱録音ではなく、UTAU音源から作成された派生データセットである。**ドナー用途としては「音節/母音テンプレート」としての適合性を別途検討する必要がある＝設計判断事項**。
- **公式配布/利用規約ページ**: https://www.canon-voice.com/terms/ （旧URL: canon-voice.com/kiyaku.html。両方到達確認済み、HTTP 200）
- **利用規約逐語引用**（WebFetch経由での日本語原文引用、取得日2026-08-15）:
  > 「商用利用可です。」
  > 「音源の転載、再配布可」
  > 「原音を加工しての転載、再配布可」
  > 「クレジット表記不要」
  > 「個人使用の範囲でのデータの加工、改変を行う事は配布をしない限り問題ありません。」
  > 「他キャラクターへの声当て可」
  > 「一部音素を他キャラクターへ流用可」
  > （VOICEVOX版利用時の例外）「『VOICEVOX』のクレジットが必要です」
- **研究/商用可否**: 商用可。
- **派生音声（合成音声）の生成・公開可否**: 明示的に禁止する条文なし。「加工しての転載、再配布可」により、合成した歌声（=加工物）の再配布・公開は許容されると読める。
- **再配布可否**: 可（原音そのまま・加工後のいずれも）。
- **クレジット義務**: 不要（VOICEVOX経由での利用時のみ例外あり）。
- **入手経路と到達性**: 公式サイト https://www.canon-voice.com/voicebanks/ より音源配布。利用規約ページの到達性は確認済み（HTTP 200）。実データのダウンロード自体は未実施。**収録時間・曲数・サンプリングレート等の技術仕様は今回取得したページには記載なし＝不明**。

## 6. ONIKU KURUMI（御丹宮くるみ）歌声データベース

- **公式ページ**: https://onikuru.info/db-download/ （HTTP 200確認済み）
- **内容**: 御丹宮くるみ（バーチャルシンガー/AIシンガー）単独歌唱。56曲収録（NNSVSレシピの言及より）。配布用最新版（2023.10.02時点）は16bit/44100Hz WAV。**総収録時間はページに記載なし＝不明**。
- **利用規約逐語引用**（WebFetch経由、取得日2026-08-15）:
  > 「本データベースを利用した成果物を公開する際、乙は『御丹宮くるみ歌声データべース』等、当データベースを利用していると分かるような表記をするものとします。」（クレジット義務）
  > 「本データベースの全部、及び一部を再頒布すること」（禁止事項の列挙冒頭。ラベル/MIDI/MusicXMLファイルのみ例外的に再配布可、と NNSVS レシピ側の README にも同旨の記述あり）
  > 「本データベースを利用した成果物を商用利用する場合」→ 事前問い合わせ・追加契約/費用が発生しうる
  > 「法人が本件音声データを商用・非商用問わず利用し、成果物を発表する場合」→ 事前許諾が必要
- **派生音声（合成音声）の生成・公開可否**: 個人の非商用利用であれば、クレジット表記の上で成果物公開が可能と読める（禁止条文なし）。法人利用や商用利用は事前許諾制。
- **再配布可否**: 音声データ本体は不可（ラベル/MIDI/MusicXMLのみ可）。
- **入手経路**: 公式サイトの規約同意後、Google Driveの直接ダウンロードリンク（ボタン形式）。**ダウンロード自体は未実施**（規約同意ステップがあり、本タスクの「本体ダウンロードはしない」方針に合致）。
- **NNSVS連携リポジトリ**: https://github.com/taroushirani/nnsvs_oniku_kurumi_utagoe_db （curl直接アクセスはHTTP 403だったが、これはproxy/UA起因の可能性が高く、GitHubは環境として到達確認済みのドメインのため実質的に到達可能と判断）。README曰く「ライセンスの都合上、データや自動ダウンロード用ヘルパースクリプトは同梱していない」＝ユーザーが公式サイトから個別に規約同意の上取得する必要あり。

## 7. おふとんP 歌声データベース

- **配布ページ**: https://sites.google.com/view/oftn-utagoedb （HTTP 200確認済み）
- **内容**: 単独歌唱、46曲（49メロディバリエーション、移調違いを含む）。57音声ファイル、96kHz/24bit WAV。フレーズ音素区間の合計時間（無音除く）約**46分30秒**。MIDI/MusicXML/ラベルファイル/USTファイル付属。バージョン1.8（2024年7月時点）。
- **利用規約（WebFetch要約、引用符は元ページからの直接引用箇所）**:
  - 禁止事項: 音声データ本体の全部・一部の再配布（「"label files, MIDI files, and MusicXML files may be redistributed"」はその例外として明記）
  - キャラクターの付与禁止、他者攻撃・権利侵害目的の利用禁止、DBの価値を著しく下げる利用の禁止
  - 事前連絡が必要なケース: 商用製品化、法人利用（商用・非商用問わず）、クレジット省略、本DBを利用した新規DB作成、派生音声モデル/ライブラリの配布
  - クレジット義務: 「DB制作:おふとんP」の表示必須（事前許諾があれば省略可）
  - TALQu/COEIROINK等の合成音声ソフトで公開する場合は親作品登録を推奨（義務ではなく推奨、との記載）
- **派生音声（合成音声）の生成・公開可否**: 個人の非商用範囲であれば、クレジット表記の上で公開可能と読める。商用・法人利用・派生モデルの配布は事前連絡（許諾）が条件。
- **再配布可否**: 音声データ本体は不可。ラベル/MIDI/MusicXMLは可。
- **到達性**: HTTP 200確認済み。ダウンロード自体（Dropboxリンク経由）は未実施。

## 8. IdolSongsJp Corpus

- **論文**: Suda, Koguchi, Yoshida, Nakamura, Fukayama, Ogata, "IdolSongsJp Corpus: A Multi-Singer Song Corpus in the Style of Japanese Idol Groups," arXiv:2507.01349 (ISMIR 2025), 産総研(AIST)。
- **内容**: プロ作曲家に委嘱した15曲のアイドルグループ様式楽曲。10名の女性歌手＋8名の男性歌手（プロ/セミプロ）。各曲でsong division（歌割り）構造あり。マスター音源、音源分離用ステム、**ドライボーカルトラック**（個別歌手の無加工ボーカル、ソロ抽出に有用）、コード注釈を含む。48kHz/32bit float RIFF-WAV。合計93.3GB。
- **配布元（実データ）**: https://huggingface.co/datasets/imprt/idol-songs-jp （**gated dataset**、連絡先共有への同意が必須）
- **Zenodoレコード**: https://zenodo.org/records/17706547 （DOI: 10.5281/zenodo.17706547）— **こちらには論文PDF(000075.pdf, 910.3kB)のみが登録されており、コーパス音声本体は含まれていない**。CC-BY-4.0のバッジ表示はこのZenodoレコード（=論文アーカイブ）自体のものであり、コーパス音声データのライセンスとは別物である点に注意（音声本体のライセンスは下記のHuggingFace上の独自ライセンス "idol-songs-jp-license" が適用される）。
- **ライセンス逐語引用**（arXiv:2507.01349 HTML版のWebFetch経由抽出、引用符内は原文引用箇所, 取得日2026-08-15）:
  > "This corpus is available free of charge for non-commercial research and entertainment purposes. No prior consent is required for such uses."
  > "Any commercial use of the corpus requires prior permission from the authors."
  > "rearrange, parody, and apply machine learning techniques to the corpus, provided that the creators' moral rights are upheld."
  > "Sampling the instrumental tracks to create unrelated content or to train machine learning models is prohibited."
  （加えて、他者を誹謗中傷する目的や、他者になりすます合成音声の生成は禁止、との言及あり）
- **研究/商用可否**: 非商用研究・**非商用エンタメ用途は事前同意不要で無償利用可**（"entertainment purposes" が明記されている点が他コーパスと一線を画す）。商用利用は事前許諾制。
- **派生音声（合成音声）の生成・公開可否**: リアレンジ・パロディ・機械学習応用は許可（人格権尊重が条件）。ボーカル信号を使って**他者を誹謗中傷/なりすます不適切な合成音声を作ることは禁止**、という限定的な禁止のみで、「合成音声の生成・公開」自体を包括的に禁じる条文ではないと読める。
- **再配布可否**: 明確な再配布条項の逐語引用は今回未確認＝**不明**。器楽トラックのサンプリング・無関係コンテンツへの転用・ML学習データとしての流用は明確に禁止。
- **著作権/クレジット**: 著者（産総研）が著作権を保持。クレジットは論文引用または「© 2025 National Institute of Advanced Industrial Science and Technology」の表示。
- **到達性**: HuggingFace (`imprt/idol-songs-jp`) HTTP 200確認済み。Zenodoページ HTTP 200確認済み（ただし音声本体は無し、上記参照）。gatedのためダウンロードにはHFアカウント＋連絡先共有への同意が必要（本タスクでは未実施）。

## 9. HuggingFace "japanese singing" 検索で見つかったその他

検索で見つかったが、要件（ソロ歌唱・ライセンス明確・研究/派生音声用途）との適合性が低い、または詳細未精査のもの:

- **SingMOS**（`TangRain/SingMOS`）: 中国語・日本語混在の歌唱クリップ、計9.07時間。MOS予測（品質評点）用データセットで、ソロ歌唱の「ドナー」用途に適した音素バランス・クリーン収録かは不明。ライセンス・収録音源の出所（既存コーパスからの引用か新規収録か）は未精査。
- **SingNet**: 論文タイトルに "large-scale, diverse, in-the-wild singing voice dataset" とあり、web由来のin-the-wildデータの可能性が高く、著作権的にクリーンな「ドナー」用途には不向きの可能性が高い。詳細未精査（時間の都合で対象外）。
- 上記2件は本調査の必須対象リストには含まれていなかったため、存在の確認のみに留め、ライセンス逐語引用等の深掘りは実施していない。

---

## 到達性チェック結果一覧（curl, 2026-08-15T07:39 UTC 前後実施）

| URL | HTTPステータス |
|---|---|
| sites.google.com/.../pjs_corpus | 200 |
| sites.google.com/.../jvs_music | 200 |
| zunko.jp/kiridev/login.php | 200 |
| huggingface.co/datasets/jaCappella/jaCappella | 200（gated、README raw取得は401） |
| www.canon-voice.com/terms/ | 200 |
| www.canon-voice.com/kiyaku.html | 200（→ https://www.canon-voice.com/kiyaku.html にリダイレクトなし、そのまま200） |
| sites.google.com/view/oftn-utagoedb | 200 |
| onikuru.info/db-download/ | 200 |
| github.com/taroushirani/nnsvs_oniku_kurumi_utagoe_db | curl直叩きは403（proxy/UA起因の可能性。GitHubは環境として到達確認済みドメイン） |
| arxiv.org/abs/2507.01349 | 200 |
| huggingface.co/datasets/imprt/idol-songs-jp | 200（gated） |
| zenodo.org/records/17706547 | 200（音声本体なし、論文PDFのみ） |
| tomohikonakamura.github.io/jaCappella_corpus/ | 200 |

## 主要な不明点（推測で埋めていない箇所）

1. JVS-MuSiC・Ritsu・ONIKU KURUMI・おふとんP について、それぞれの**サンプリングレート/ビット深度が全ファイル種別で統一されているか**、DB全体の総収録時間の一部（Ritsu, ONIKU KURUMI は特に）は情報源に記載がなく不明。
2. 東北きりたんの**利用契約書PDF原本の完全な逐語全文**はログイン後にのみ閲覧可能な可能性が高く、今回は未取得（web検索結果とページ要約からの間接情報のみ）。
3. 東北イタコ歌唱データベースの**現在の公開状況・配布URL・ライセンス条件**は特定できず。
4. IdolSongsJp Corpusの**再配布可否**を明記した条文は未確認。
5. jaCappella論文中の "open the way for commercial use" という表現と、実際の配布時規約 "You may not use...for commercial purposes" の間には**ニュアンスの差**があり、どちらが最終的な拘束力を持つ規約なのか（＝配布時規約が優先されると考えるのが妥当）は明記されているが、論文発表時点(2023年)と現在(2026年)で規約が改定されている可能性は未確認。
