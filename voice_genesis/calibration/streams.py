"""HKDF-SHA256 による RNG stream 分離（設計正本 §3.3, §7）。

全ての乱数（generator / split tie-break / meter 内部乱数）は、単一の secret から
`(campaign_id, family, split, row_id, probe_index, purpose)` で一意に分離された
stream を HKDF (RFC 5869) で導出する。`secret` は本モジュールが生成・保存すること
は一切なく、常に呼び出し側から引数として渡される（設計正本 §0 授権境界）。

台帳 (`RngLedger`) には **secret を記録しない**。記録するのは OKM (output keying
material) の sha256 という「公開識別子」のみであり、これから secret や実際の
乱数列を逆算することはできない（設計正本 §13）。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

import numpy as np

from voice_genesis.calibration.fixtures.axes import FAMILY_ORDER

_HASH_LEN = hashlib.sha256().digest_size  # 32 bytes


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 5869 §2.2. salt が空バイト列なら長さ HashLen のゼロ埋めを使う。"""
    if not salt:
        salt = b"\x00" * _HASH_LEN
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 §2.3."""
    if length > 255 * _HASH_LEN:
        raise ValueError("hkdf_expand: requested length exceeds 255*HashLen")
    if length < 0:
        raise ValueError("hkdf_expand: length must be non-negative")
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def hkdf(*, salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """extract + expand の合成。"""
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)


def _length_prefixed_info(*fields: str) -> bytes:
    """info バイト列 = 各 field を UTF-8 化し、4-byte big-endian 長さ接頭辞を付けて
    連結したもの。

    [UNDERSPEC-CAL-02] 設計正本は info の具体的バイト表現形式（区切り記号 vs 長さ接頭
    辞など）を規定していない。単純な区切り文字連結（例: `"||".join(fields)`）は
    field 自体に区切り文字列が出現した場合に異なる論理的分割が同一バイト列へ衝突しう
    るため、長さ接頭辞方式（各 field の長さを明示してから内容を続ける）を採用する。
    これは衝突耐性のある標準的なエンコーディングであり、実装マップ §3 の
    「最も単純で安全な選択」基準に合致する。
    """
    parts: list[bytes] = []
    for f in fields:
        raw = f.encode("utf-8")
        parts.append(len(raw).to_bytes(4, "big"))
        parts.append(raw)
    return b"".join(parts)


def stream_info(
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int,
    purpose: str,
) -> bytes:
    """§7 の info = campaign_id || family || split || row_id || probe_index || purpose
    を長さ接頭辞方式で連結する。"""
    return _length_prefixed_info(
        campaign_id, family, split, row_id, str(probe_index), purpose
    )


def derive_okm(
    secret: bytes,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int,
    purpose: str,
    length: int = 8,
) -> bytes:
    """HKDF-SHA256 で `length` バイトの output keying material を導出する。"""
    info = stream_info(
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
        purpose=purpose,
    )
    return hkdf(salt=b"", ikm=secret, info=info, length=length)


def derive_seed(
    secret: bytes,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int,
    purpose: str,
) -> int:
    """OKM の先頭 8 byte を unsigned big-endian int として seed に使う
    (np.random.PCG64 向け)。"""
    okm = derive_okm(
        secret,
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
        purpose=purpose,
        length=8,
    )
    return int.from_bytes(okm, "big")


def derive_generator(
    secret: bytes,
    *,
    campaign_id: str,
    family: str,
    split: str,
    row_id: str,
    probe_index: int,
    purpose: str,
) -> np.random.Generator:
    """`derive_seed` から `np.random.Generator(PCG64(seed))` を組み立てる。"""
    seed = derive_seed(
        secret,
        campaign_id=campaign_id,
        family=family,
        split=split,
        row_id=row_id,
        probe_index=probe_index,
        purpose=purpose,
    )
    return np.random.Generator(np.random.PCG64(seed))


#: C0 manifest の `rng_ledger` が declare する split-level stream の固定名
#: （設計正本 §3.3。family に依らずキャンペーン全体で唯一）。
SPLIT_HMAC_STREAM_NAME = "split/hmac"
SPLIT_TIEBREAK_STREAM_NAME = "split/tiebreak"

#: `stream_name()` がこの集合に属さない `purpose` を受け取った場合、当該
#: family の generator render stream (`"<family>/render"`) へ折り畳む
#: （下記 `stream_name` docstring 参照）。
_SPLIT_LEVEL_PURPOSES: dict[str, str] = {
    "split_hmac": SPLIT_HMAC_STREAM_NAME,
    "split_tiebreak": SPLIT_TIEBREAK_STREAM_NAME,
}


def stream_name(*, family: str, purpose: str) -> str:
    """`RngLedgerEntry.stream_name` / C0 manifest `rng_ledger` 記録粒度での
    stream 識別子（Codex レビュー 2026-09-01 P1 finding #2 DESIGN RULING）。

    C0 の `rng_ledger` は row/probe 単位の実 HKDF 導出を個別列挙しない
    （`expected_rng_stream_names()` docstring も参照）。実際の乱数分離
    そのもの（row_id/probe_index を含む一意な分離）は引き続き
    `stream_info()`/`derive_okm()`/`derive_seed()`/`derive_generator()` が
    担い、この関数の戻り値には影響しない。

    `[UNDERSPEC-CAL-C16]` 設計正本 §3.3 は C0 記録粒度と実導出粒度の対応
    までは規定しないため、「split 関連の 2 purpose 以外はすべて当該 family の
    render stream へ折り畳む」という最も単純な写像を採用する:

    - `purpose="split_hmac"` → `"split/hmac"`（family に依らずキャンペーン
      全体で唯一。splitter.py の stratum 内 HMAC 順位付けに対応）
    - `purpose="split_tiebreak"` → `"split/tiebreak"`（同上。§7 の余り偶奇
      tie-break に対応）
    - それ以外の purpose（例: `"generator"`）→ `f"{family}/render"`
      （row/probe に依らず family 単位で 1 つに畳み込む）
    """
    split_level = _SPLIT_LEVEL_PURPOSES.get(purpose)
    if split_level is not None:
        return split_level
    return f"{family}/render"


def expected_rng_stream_names() -> frozenset[str]:
    """C0 manifest `rng_ledger` が過不足なく declare すべき closed stream 名
    集合（設計正本 §3.3。Codex レビュー 2026-09-01 P1 finding #2 DESIGN
    RULING、`[UNDERSPEC-CAL-C16]`）:

        {generator render streams: 1 per (family, purpose="render")}
        ∪ {split: 1 stream "split/hmac"}
        ∪ {tie-break: 1 stream "split/tiebreak"}

    row/probe 単位の HKDF 導出（`derive_generator()` 等が実際に消費する
    `stream_info()` の一意な info バイト列）は、この per-family render
    stream の **sub-derivation** であり、C0 では個別列挙しない（既定の
    row 数・probe 数はキャンペーン規模に応じて変動しうるが、closed set 自体は
    family 構成が変わらない限り不変であるべきため）。

    family 名は `fixtures.axes.FAMILY_ORDER`（凍結 fixture family 全集合。
    matrix.py が re-export する `FixtureFamily` と同一の enum）から機械導出
    する。呼び出し側供給ではなく code-derived であることが本 finding の
    修正の核心（Codex レビュー 2026-09-01 P1: 従来 `rng_ledger` の
    stream_names は任意の well-formed entry が 1 件あれば通過し、closed set
    でないことを検証していなかった）。
    """
    render_streams = {f"{family.value}/render" for family in FAMILY_ORDER}
    return frozenset(render_streams | {SPLIT_HMAC_STREAM_NAME, SPLIT_TIEBREAK_STREAM_NAME})


@dataclass(frozen=True)
class RngLedgerEntry:
    """1 stream 分の台帳エントリ。secret は記録しない
    (public_seed_id = sha256(OKM) のみ)。"""

    purpose: str
    campaign_id: str
    family: str
    split: str
    row_id: str
    probe_index: int
    public_seed_id: str
    stream_name: str
    seeded: bool = True


@dataclass
class RngLedger:
    """全乱数 stream の accumulator（設計正本 §3.3: RNG 台帳。未 seed 乱数の検出は
    呼び出し側が `entries` を C0 manifest の宣言済み stream 集合と突合して行う）。
    """

    entries: list[RngLedgerEntry] = field(default_factory=list)

    def record(
        self,
        secret: bytes,
        *,
        campaign_id: str,
        family: str,
        split: str,
        row_id: str,
        probe_index: int,
        purpose: str,
    ) -> RngLedgerEntry:
        okm = derive_okm(
            secret,
            campaign_id=campaign_id,
            family=family,
            split=split,
            row_id=row_id,
            probe_index=probe_index,
            purpose=purpose,
            length=8,
        )
        public_seed_id = hashlib.sha256(okm).hexdigest()
        entry = RngLedgerEntry(
            purpose=purpose,
            campaign_id=campaign_id,
            family=family,
            split=split,
            row_id=row_id,
            probe_index=probe_index,
            public_seed_id=public_seed_id,
            stream_name=stream_name(family=family, purpose=purpose),
            seeded=True,  # `record()` は常に HKDF から実際に seed を導出済み
        )
        self.entries.append(entry)
        return entry

    def to_records(self) -> list[dict[str, object]]:
        """**AUDIT 形式**: `record()` が積んだ全 entry を 1 対 1 で dict 化した
        per-derivation の完全な列挙（row_id/probe_index を保持）。

        監査・デバッグ用途（「どの row/probe がどの secret 由来 seed で
        導出されたか」を個別に追跡する）のための形であり、**C0 manifest の
        `rng_ledger` へ直接渡してはならない**（Codex レビュー 2026-09-01 P1
        finding #1）: 同一 family/purpose に属する複数 row/probe の entry は
        `stream_name`（`<family>/render` 等、family+purpose 単位の粗粒度）が
        すべて同一値へ折り畳まれるため、2 件目以降が
        `c0_validate._check_rng_ledger_closed_set` の重複検査（closed set は
        stream_name の重複を許さない）に必ず抵触する。これは検査の不備では
        なく意図された挙動であり、declaration-form との役割分離を強制する
        （`test_streams.py` の対応する負のテストが直接この抵触を確認する）。

        C0 manifest producer は代わりに `to_declaration_records()` を使うこと。
        """
        return [
            {
                "stream_name": e.stream_name,
                "seeded": e.seeded,
                "public_seed_id": e.public_seed_id,
                "purpose": e.purpose,
                "campaign_id": e.campaign_id,
                "family": e.family,
                "split": e.split,
                "row_id": e.row_id,
                "probe_index": e.probe_index,
            }
            for e in self.entries
        ]

    def to_declaration_records(self) -> list[dict[str, object]]:
        """**DECLARATION 形式**: `stream_name` 単位で `to_records()` の
        per-derivation entry を集約し、1 stream class = 1 record にした
        dict を返す。C0 manifest の `rng_ledger` はこの形式を consume する
        契約（Codex レビュー 2026-09-01 P1 finding #1: `to_records()` の
        per-derivation AUDIT 形式を manifest producer が直接使うと、同一
        family 内の 2 件目以降の row/probe が `stream_name` 重複で
        `c0_validate._check_rng_ledger_closed_set` に必ず抵触していた）。

        集約規則:
          - `stream_name`: group key（`streams.stream_name()` の戻り値）。
          - `seeded`: group を構成する全 entry の `seeded` が True の場合のみ
            True（1 件でも unseeded な entry を含む group は False とし、
            `BLOCKED_C0_UNSEEDED_RNG` を正しく発火させる）。
          - `public_seed_id`: group を構成する全 entry の `public_seed_id`
            （各 64 桁固定長の hex 文字列）を昇順ソートしてから連結し
            sha256 した digest。固定長要素の連結のため区切り文字なしでも
            `stream_info` の長さ接頭辞問題（可変長 field の境界衝突）は
            生じない。secret 自体は元より `public_seed_id`（= sha256(OKM))
            にも登場しないため、この digest からも逆算できない。
        """
        groups: dict[str, list[RngLedgerEntry]] = {}
        for e in self.entries:
            groups.setdefault(e.stream_name, []).append(e)

        records: list[dict[str, object]] = []
        for stream, members in sorted(groups.items()):
            all_seeded = all(m.seeded for m in members)
            constituent_ids = sorted(m.public_seed_id for m in members)
            digest = hashlib.sha256("".join(constituent_ids).encode("ascii")).hexdigest()
            records.append(
                {
                    "stream_name": stream,
                    "seeded": all_seeded,
                    "public_seed_id": digest,
                }
            )
        return records
