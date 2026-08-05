"""authoring — L0a 著述契約: スキーマ公開範囲 + 記号検証ゲート + report 正規形。

正本 = `docs/l0a_authoring_contract.md`（設計 = `docs/llm_adapter_planning.md`
§4「L0a」）。音声処理ゼロ——このパッケージは Score dict の記号検証と信頼軸表
導出のみを行い、音を出さない。

Modules:
  contract.py       — 著述契約 spec (`config/authoring_contract_l0.yaml`) の
                       pydantic モデル + loader
  validate.py       — score dict → 公開スキーマ範囲 + canonical 検証
  trusted_axes.py   — 信頼軸表の機械導出
                       (`config/authoring_trusted_axes_l0.yaml`)
  report.py         — 差分報告 (`report.json`) 正規形の凍結スキーマ
"""
from __future__ import annotations
