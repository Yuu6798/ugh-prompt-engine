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

親検証は `self.exists(parent_id)`（パス存在のみ）ではなく
`self.read(parent_id)` の成功で行う（PR #267 Codex R6 指摘3, 2026-08-17
採用: `exists()` はファイルの有無しか見ないため、親の位置に破損した
JSON や genome_id 不一致の実体が置かれていても検出できず publish が
通っていた。`read()` を経由すれば `genome_from_dict()` のフル検証
（schema・genome_id 再計算一致・lineage 座標整合）とファイル名↔内容の
自己申告 ID 束縛検証まで働く）。`read()` が送出しうる
`FileNotFoundError` / `json.JSONDecodeError` /
`models.GenomeValidationError` / `LedgerError` はいずれも
`LedgerError` へ正規化して fail-closed で拒否する。

非 founder の publish 時は、上記でロード済みの実在親 + 子の
`operator`/`operator_params` から `operators` モジュールの対応する純関数で
子を決定論的に再導出し、再導出結果の `genome_id` が publish 対象の
`genome_id` と一致することを検証する（PR #267 Codex R7 指摘1, 2026-08-17
採用: 従来は親を `read()` で検証してもロード結果を捨てていたため、実在する
親を正しく参照しつつ座標・generation・operator_params だけを無関係な値へ
すり替えた「偽装子」でも publish が通った — 例えば実在親を parents[0] に
指定したまま、無関係な座標や `generation=999` を宣言した genome でも、
親の実在検証だけでは検出できない）。対応表: `drift`/`reseed`/`edge_walk`/
`novelty_jump` は親1（`parents[0]` をロード）、`vertex_pull` は親2
（`parents` の順にロード）。`founder` は再導出対象外（従来の parents 空
検証のみ）。`genome_id` は coords/seed/lineage/generation/parents/
operator/operator_params の6フィールドを被覆する内容アドレスのため
`genome_id`比較で足りるが、`anchors_provenance` はこの6フィールドの外
（genome_id 計算から除外— models.py 参照）にあるため、再導出結果が
親から伝播した `anchors_provenance` と publish 対象の宣言値が一致する
ことも別途比較する。再導出は operators.py が純関数・全パラメータが
`operator_params` に記録済みであるため完全決定論であり、再導出関数が
送出しうる `ValueError`（例: vertex_pull の両親 anchors_provenance
不一致）も `LedgerError` へ正規化して fail-closed で拒否する。

`read()`・`write()` の既存ファイル分岐はいずれもアクセス直前に
`_reject_symlink_escape()` を通す（PR #267 Codex R9 指摘3, 2026-08-17
採用: `<ledger>/<genome_id>.json` が台帳ディレクトリ外への symlink の場合、
従来の `read()` は追従して外部実体をそのまま台帳エントリとして読み込み、
`write()` の冪等比較（バイト同一なら no-op）も外部実体を台帳エントリと
同一視していた）。パスが symlink であること、または resolve 済みパスが
`self.directory` の外側を指すことのいずれかを検出したら `LedgerError` で
fail-closed 拒否する。

`write()` の戻り値は `WriteResult`（`path` + `created`）（PR #267 Codex
R12 指摘2, 2026-08-17 採用）: `created=True` は今回の呼び出しが
`os.link` による排他 create を実際に成功させた（＝真に新規作成した）
ことを、`created=False` は既存ファイルとバイト同一内容による冪等
no-op（他プロセスが同一 genome_id を先に publish 済みだった等）を
意味する。呼び出し側（`bootstrap.run_bootstrap()` の rollback 等）は
「pre-check 時に存在しなかったはず」という推定ではなく、この実測フラグ
だけを rollback 対象の判定に使うことで、pre-check と write の間の
並行 publish による他者の正当エントリの誤 unlink を防げる。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402
import operators  # noqa: E402

_GENOME_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class LedgerError(ValueError):
    pass


class LedgerConflictError(LedgerError):
    """append-only 台帳: 既存 genome_id に異なる内容で上書きしようとした
    （PR 経由のみ変更可 — DESIGN_VG_E0.md §6）。"""


class WriteResult(NamedTuple):
    """`Ledger.write()` の戻り値（PR #267 Codex R12 指摘2, 2026-08-17 採用）。

    `path`: 公開先パス（`<ledger>/<genome_id>.json`）。
    `created`: この呼び出しが `os.link` による排他 create を実際に成功
    させ、真に新規作成したかどうか。`FileExistsError` を捕捉した上での
    バイト同一冪等 no-op は `False`（＝このプロセスはファイルシステムに
    何も新規作成していない）。呼び出し側が「自分が本当に作ったファイルか」
    を判定するための唯一の正本であり、事前の存在チェックからの推定に
    置き換えてはならない（pre-check と write の間の並行 publish を
    区別できないため）。
    """

    path: Path
    created: bool


class Ledger:
    """`directory` 配下の genome 台帳。1 genome = 1 JSON ファイル。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, genome_id: str) -> Path:
        if not _GENOME_ID_RE.match(genome_id):
            raise LedgerError(f"invalid genome_id format: {genome_id!r}")
        return self.directory / f"{genome_id}.json"

    def _reject_symlink_escape(self, path: Path) -> None:
        """`<ledger>/<genome_id>.json` が symlink である、または解決後の
        パスが `self.directory` の外側を指す場合は `LedgerError` で
        fail-closed 拒否する（PR #267 Codex R9 指摘3, 2026-08-17 採用: 従来
        `read()`/`write()` の既存ファイル分岐はいずれもこのパスをそのまま
        追従していたため、台帳外への symlink が置かれると `read()` は外部の
        実体をそのまま台帳エントリとして読み込み、`write()` の冪等比較
        （バイト同一なら no-op）も外部実体を台帳エントリと同一視していた —
        symlink の付け替えだけで台帳外のファイルを台帳エントリとして偽装
        できてしまう。既存の containment 検査の家風（resolve 済みパス比較
        — `foundry/s1_gate/forge_triangle.py` 等）に揃える。呼び出し元は
        `path` が `read()`/`write()` の既存ファイル分岐（アクセス直前）で
        呼ぶことを前提とする。
        """
        if path.is_symlink():
            raise LedgerError(
                f"refusing to access {path}: ledger entries must be regular files, not symlinks "
                "(symlink escape guard — PR #267 Codex R9 指摘3)"
            )
        directory_resolved = self.directory.resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(directory_resolved)
        except ValueError:
            raise LedgerError(
                f"refusing to access {path}: resolved path {resolved} escapes the ledger directory "
                f"{directory_resolved} (symlink escape guard — PR #267 Codex R9 指摘3)"
            ) from None

    def write(self, genome: models.VoiceGenome) -> WriteResult:
        """genome を `<genome_id>.json` として排他 create で公開する。同一
        genome_id に既に同一内容が書かれていれば冪等 no-op（bootstrap の
        再実行でバイト同一を保つため）。異なる内容での既存ファイルとの
        衝突は `LedgerConflictError`（append-only 規律の機械的補強）。

        戻り値は `WriteResult(path, created)`（PR #267 Codex R12 指摘2,
        2026-08-17 採用）: `created` はこの呼び出しが実際に新規作成した
        かどうかの実測値（`WriteResult` docstring 参照）。
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

        # publish 前検証（Codex R5 指摘1 / R6 指摘3）: parents の各 genome_id
        # が台帳に実在することを `self.read(parent_id)` の成功で確認する
        # （founder = parents 空はそのまま通過）。従来の `self.exists()` は
        # パス存在しか見ないため、親の位置に破損 JSON や genome_id 不一致の
        # 実体が置かれていても通過していた。`read()` を経由することで
        # `genome_from_dict()` のフル検証（schema・genome_id 再計算一致・
        # lineage 座標整合）+ ファイル名↔内容の自己申告 ID 束縛検証まで
        # 働かせる。未 publish / typo / 破損 / ID 不一致のいずれも
        # fail-closed で拒否する。
        loaded_parents: Dict[str, models.VoiceGenome] = {}
        for parent_id in genome.parents:
            try:
                loaded_parents[parent_id] = self.read(parent_id)
            except FileNotFoundError:
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: parent {parent_id!r} does "
                    "not exist in the ledger (parents must already be published — an unpublished or "
                    "mistyped parent id would leave a dangling edge in the lineage graph)"
                ) from None
            except (json.JSONDecodeError, models.GenomeValidationError, LedgerError) as exc:
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: parent {parent_id!r} failed "
                    f"full ledger validation on read ({exc}) — a corrupted, tampered, or genome_id-"
                    "mismatched parent must not be treated as a valid publish target"
                ) from exc

        # publish 前検証（PR #267 Codex R7 指摘1）: 非 founder の子は、ロード
        # 済みの実在親 + 子の operator/operator_params から operators モジュール
        # の対応する純関数で決定論的に再導出し、再導出結果が publish 対象と
        # （genome_id + anchors_provenance の両方で）一致することを検証する。
        # 親の実在検証だけでは、実在親を参照しつつ座標/generation/params を
        # 無関係な値へすり替えた偽装子を検出できないため。
        if genome.operator != "founder":
            try:
                rederived = self._rederive_child(genome, loaded_parents)
            except ValueError as exc:
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: deterministic re-derivation "
                    f"via operators.{genome.operator}() from the loaded parent(s) raised {exc!r} — the "
                    "declared operator_params cannot have produced this child from its declared parents"
                ) from exc
            if rederived.genome_id != genome.genome_id:
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: deterministic re-derivation "
                    f"via operators.{genome.operator}() from the loaded parent(s) + declared "
                    f"operator_params produced a different genome_id ({rederived.genome_id!r}) — the "
                    "declared coords/generation/operator_params do not match what the operator would "
                    "actually produce from the declared parents (forged child)"
                ) from None
            if rederived.anchors_provenance != genome.anchors_provenance:
                raise LedgerError(
                    f"refusing to publish genome_id {genome.genome_id!r}: deterministic re-derivation "
                    f"via operators.{genome.operator}() propagates anchors_provenance="
                    f"{rederived.anchors_provenance!r} from the loaded parent(s), but the publish "
                    f"target declares anchors_provenance={genome.anchors_provenance!r} — genome_id does "
                    "not cover anchors_provenance, so this mismatch would not otherwise be caught"
                ) from None

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
                # symlink escape guard（Codex R9 指摘3）: 冪等比較の対象が
                # 台帳ディレクトリ配下の実体であることを、内容を読む前に
                # 検査する。
                self._reject_symlink_escape(path)
                existing = path.read_bytes()
                if existing == payload:
                    return WriteResult(path=path, created=False)
                raise LedgerConflictError(
                    f"genome_id {genome.genome_id!r} already exists in the ledger with different "
                    "content (append-only ledger — changes must go through a PR, not an overwrite)"
                ) from None
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return WriteResult(path=path, created=True)

    def _rederive_child(
        self, genome: models.VoiceGenome, loaded_parents: Dict[str, models.VoiceGenome]
    ) -> models.VoiceGenome:
        """publish 対象 `genome`（非 founder）を、`loaded_parents`（台帳から
        `read()` 済みの実親）+ `genome.operator_params` から `operators`
        モジュールの対応する純関数で決定論的に再導出する（PR #267 Codex R7
        指摘1）。対応表: `drift`/`reseed`/`edge_walk`/`novelty_jump` は
        `genome.parents[0]` の1親、`vertex_pull` は `genome.parents` の順に
        2親。呼び出し元は `genome.operator != "founder"` を保証済みである
        ことを前提とする。
        """
        params = genome.operator_params
        if genome.operator == "vertex_pull":
            parent_a = loaded_parents[genome.parents[0]]
            parent_b = loaded_parents[genome.parents[1]]
            return operators.vertex_pull(
                parent_a, parent_b,
                weight=params["weight"], vertex=params["vertex"], pull=params["pull"],
            )

        parent = loaded_parents[genome.parents[0]]
        if genome.operator == "drift":
            return operators.drift(parent, rng_seed=params["rng_seed"], step=params["step"])
        if genome.operator == "reseed":
            return operators.reseed(parent, new_seed=params["new_seed"])
        if genome.operator == "edge_walk":
            return operators.edge_walk(
                parent, rng_seed=params["rng_seed"], edge=tuple(params["edge"]), step=params["step"]
            )
        if genome.operator == "novelty_jump":
            return operators.novelty_jump(parent, rng_seed=params["rng_seed"])
        raise LedgerError(  # pragma: no cover - 防御的（VALID_OPERATORS で事前拘束済み）
            f"unsupported operator for re-derivation: {genome.operator!r}"
        )

    def read(self, genome_id: str) -> models.VoiceGenome:
        path = self.path_for(genome_id)
        # symlink escape guard（Codex R9 指摘3）: 存在しないファイルへの
        # 素通し（FileNotFoundError）は変えず、symlink（壊れているものを
        # 含む）や台帳ディレクトリ外を指す実体だけを検出前に拒否する。
        self._reject_symlink_escape(path)
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
