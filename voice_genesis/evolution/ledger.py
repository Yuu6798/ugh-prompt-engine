"""ledger.py — VG-E0: 台帳ディレクトリへの個体 JSON 書き出し/読み込み
（DESIGN_VG_E0.md §6）。

1 個体 1 ファイル・ファイル名 = `<genome_id>.json`。append-only（変更は PR
経由のみ — 本モジュールは「既存 genome_id に異なる内容で上書き」を
`LedgerConflictError` で拒否することでこの規律を機械的に補強する。同一
内容での再書き込みは冪等 no-op として許可する — bootstrap の「2回実行で
バイト同一」という要求と両立させるため）。

publish は **排他 create**（Codex 指摘1, 2026-08-17 採用）: 同一ディレクトリ内
の一時ファイルへ書き込み・fsync してから `os.link(tmp, dst)` で公開する。
`os.link` は宛先が既に存在すれば `FileExistsError` を送出する（`tmp →
os.replace` は宛先の有無に関わらず常に成功する「後勝ち」publish のため、
同一 genome_id への2並行初回書込みが notes/anchors_provenance の異なる
内容で競合した場合に、後着が黙って先着を踏みつぶし得た）。`FileExistsError`
を捕捉したら既存ファイルの内容と比較し、バイト同一なら冪等 no-op、異なれば
`LedgerConflictError`（append-only 規律の機械的補強）— 既存の意味論は
変更しない。

書込み直前には publish 予定のシリアライズ済みバイト列を
`models.genome_from_dict()`（読み取り側の完全検証: 未知キー拒否・
genome_id 再計算一致・lineage 座標整合・anchors_provenance sha256 構文）
へ通す（Codex 指摘2, 2026-08-17 採用: 呼び出し側が `VoiceGenome` dataclass
を直接構築したり `operator_params` の dict を変異させた場合、
`build_genome()` の検証を経ずに壊れた genome が publish されうるため）。

同じく publish 前検証として、`parents` の各 genome_id が台帳に実在する
ことを確認する（PR #267 Codex R5 指摘1, 2026-08-17 採用: founder =
parents 空はそのまま許可。非 founder の genome が未 publish / typo の
親 ID を参照していても従来は write が通り、系譜グラフに宙吊りエッジ
（存在しない親への参照）が恒久化していた）。不在親は `LedgerError` で
fail-closed。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import List

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402

_GENOME_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class LedgerError(ValueError):
    pass


class LedgerConflictError(LedgerError):
    """append-only 台帳: 既存 genome_id に異なる内容で上書きしようとした
    （PR 経由のみ変更可 — DESIGN_VG_E0.md §6）。"""


class Ledger:
    """`directory` 配下の genome 台帳。1 genome = 1 JSON ファイル。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, genome_id: str) -> Path:
        if not _GENOME_ID_RE.match(genome_id):
            raise LedgerError(f"invalid genome_id format: {genome_id!r}")
        return self.directory / f"{genome_id}.json"

    def write(self, genome: models.VoiceGenome) -> Path:
        """genome を `<genome_id>.json` として排他 create で公開する。同一
        genome_id に既に同一内容が書かれていれば冪等 no-op（bootstrap の
        再実行でバイト同一を保つため）。異なる内容での既存ファイルとの
        衝突は `LedgerConflictError`（append-only 規律の機械的補強）。
        """
        path = self.path_for(genome.genome_id)
        payload = (models.genome_to_json(genome) + "\n").encode("utf-8")

        # publish 直前の round-trip 検証（Codex 指摘2）。
        try:
            models.genome_from_dict(json.loads(payload))
        except models.GenomeValidationError as exc:
            raise LedgerError(
                f"refusing to publish genome_id {genome.genome_id!r}: serialized payload failed "
                f"round-trip validation via genome_from_dict() ({exc})"
            ) from exc

        # publish 前検証（Codex R5 指摘1）: parents の各 genome_id が台帳に
        # 実在することを確認する（founder = parents 空はそのまま通過）。
        # 未 publish / typo の親 ID を許すと系譜グラフに宙吊りエッジが
        # 恒久化するため fail-closed で拒否する。
        for parent_id in genome.parents:
            if not self.exists(parent_id):
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: parent {parent_id!r} does "
                    "not exist in the ledger (parents must already be published — an unpublished or "
                    "mistyped parent id would leave a dangling edge in the lineage graph)"
                )

        self.directory.mkdir(parents=True, exist_ok=True)

        # 排他 create publish（Codex 指摘1）: tmp へ書いて fsync してから
        # os.link(tmp, dst) で公開する。宛先が既に存在すれば os.link は
        # FileExistsError を送出するため、2並行初回書込みは片方が必ず負けて
        # 既存意味論（バイト同一=冪等OK / 差異=LedgerConflictError）で解決する。
        fd, tmp_name = tempfile.mkstemp(dir=self.directory, prefix=f"{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_name, path)
            except FileExistsError:
                existing = path.read_bytes()
                if existing == payload:
                    return path
                raise LedgerConflictError(
                    f"genome_id {genome.genome_id!r} already exists in the ledger with different "
                    "content (append-only ledger — changes must go through a PR, not an overwrite)"
                ) from None
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return path

    def read(self, genome_id: str) -> models.VoiceGenome:
        path = self.path_for(genome_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        genome = models.genome_from_dict(data)
        if genome.genome_id != genome_id:
            # ファイル名(要求ID)↔内容の自己申告ID の契約を強制する（Codex
            # 指摘5）: ファイルがリネームされる／別 genome_id のファイルの
            # 中身をコピーされる等で、内容自己申告の genome_id 再計算検証
            # （genome_from_dict 内）だけでは検出できない不整合を拒否する。
            raise LedgerError(
                f"genome_id mismatch: requested {genome_id!r} but {path} declares "
                f"{genome.genome_id!r} (filename/content binding violated — renamed or corrupted file)"
            )
        return genome

    def exists(self, genome_id: str) -> bool:
        return self.path_for(genome_id).exists()

    def list_genome_ids(self) -> List[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if _GENOME_ID_RE.match(p.stem))
