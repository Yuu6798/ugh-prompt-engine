# S4 RECORD — BLOCKED

- 原因: terminal_ri|2018#215|pjs003#65: performance の再構築ハッシュが凍結 pin と一致しない (got a9ea8600323e4ab4… / pin aecf69d27d54cb4e…)
- 影響: S2 と別の素材を評価しながら manifest のハッシュを来歴として提示することになり、PASS の provenance が偽になる
- 最小修正案: 参照先の lab/wav が S2 走行時から変わっていないかを確認する

S4 の結果は出さない。修正実装は行わない（設計書 §24）。

S2 PASS / S3 PASS / S3.5 の結果は変更しない（設計書 §16）。

## 停止までに通過した Gate

判定には使わない。**どこまで進んで何で止まったか**を記録だけで追えるようにするための証拠。

| 項目 | 値 |
|---|---|
| G0-1_s3_canonical | pass |
| G0-2_s35_canonical | pass |
| G0-3_provenance_chain | pass |
| G0-5_clean_worktree | pass |
| G0-5_closure_digest | 1f251eb52ee5c317963d74b63aec3d50222f28dbd00ce9043e5eb049ffdd55e9 |
| G0-5_closure_file_count | 21 |
| commit | a8e0d156f28ce95edf51a0f3d01e010d8ded7291 |
| s3_results_sha256 | 65b91402f2b6ead2b8d3269455413e6bd0ae575d66a33a52f0a2367c91e55cd0 |
| s35_results_sha256 | 5dcbc4329d9e7343e0f48d96665be5faa0f9406425a6552249eb9b5fcebb95ba |
| input_manifest_sha256 | 78fcd8cc6e3f2b7c9edae72e0894f552b9cdfb0087d44b1df0d45402063fec4f |
| material_relocation | pass |
| material_roots | {'pjs_corpus': '/tmp/claude-0/-home-user-ugh-prompt-engine/0af06e53-6f38-53ad-9662-a107f94f5678/scratchpad/voicegenesis_materials/pjs_ex/PJS_corpus_ver1.1', 'ritsu_singing_db': '/tmp/claude-0/-home-user-ugh-prompt-engine/0af06e53-6f38-53ad-9662-a107f94f5678/scratchpad/voicegenesis_materials/ritsu_ex/üuögë╣âèâcüvë╠É║âfü[â^âxü[âXVer2.0.2'} |
| candidate_pairs | 6 |
| candidate_pair_keys | `terminal_ri\|2018#215\|pjs003#65`, `terminal_i\|1st_color#86\|pjs001#54`, `medial_ri\|1st_color#104\|pjs002#42`, `terminal_ri\|2018#598\|pjs065#29`, `terminal_i\|1st_color#218\|pjs001#73`, `medial_ri\|1st_color#357\|pjs002#54` |
| candidate_contexts | `medial_ri`, `terminal_i`, `terminal_ri` |
| §4_candidate_derivation | pass |
