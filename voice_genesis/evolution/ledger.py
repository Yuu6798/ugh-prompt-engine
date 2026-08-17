"""ledger.py — VG-E0: 台帳ディレクトリへの個体 JSON 書き出し/読み込み
（DESIGN_VG_E0.md §6）。

1 個体 1 ファイル・ファイル名 = `<genome_id>.json`。append-only（変更は PR
経由のみ — 本モジュールは「既存 genome_id に異なる内容で上書き」を
`LedgerConflictError` で拒否することでこの規律を機械的に補強する。同一
内容での再書き込みは冪等 no-op として許可する — bootstrap の「2回実行で
バイト同一」という要求と両立させるため）。

atomic write は `svp_rpe.utils.atomic_io`（インストール済みパッケージ、
`src/svp_rpe/utils/atomic_io.py`）があれば流用し、import できない環境
（万一 svp-rpe が editable install されていない場合の防御）では
tmp書き込み→`os.replace` の同型フォールバックを使う（DESIGN_VG_E0.md §6
「atomic write は svp_rpe.utils の atomic_io があれば流用、無ければ
tmp→rename」）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402

try:
    from svp_rpe.utils.atomic_io import atomic_write_bytes
except ImportError:  # pragma: no cover - 防御的フォールバック（本環境では未到達）
    import os
    import tempfile

    def atomic_write_bytes(path: Path, data: bytes) -> None:
        path = Path(path)
        output_dir = path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


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
        """genome を `<genome_id>.json` として atomic に書き出す。同一
        genome_id に既に同一内容が書かれていれば冪等 no-op（bootstrap の
        再実行でバイト同一を保つため）。異なる内容での上書きは
        `LedgerConflictError`（append-only 規律の機械的補強）。
        """
        path = self.path_for(genome.genome_id)
        payload = (models.genome_to_json(genome) + "\n").encode("utf-8")
        if path.exists():
            existing = path.read_bytes()
            if existing == payload:
                return path
            raise LedgerConflictError(
                f"genome_id {genome.genome_id!r} already exists in the ledger with different "
                "content (append-only ledger — changes must go through a PR, not an overwrite)"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, payload)
        return path

    def read(self, genome_id: str) -> models.VoiceGenome:
        path = self.path_for(genome_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return models.genome_from_dict(data)

    def exists(self, genome_id: str) -> bool:
        return self.path_for(genome_id).exists()

    def list_genome_ids(self) -> List[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if _GENOME_ID_RE.match(p.stem))
