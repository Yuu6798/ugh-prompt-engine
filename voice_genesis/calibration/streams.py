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
        )
        self.entries.append(entry)
        return entry

    def to_records(self) -> list[dict[str, object]]:
        return [
            {
                "purpose": e.purpose,
                "campaign_id": e.campaign_id,
                "family": e.family,
                "split": e.split,
                "row_id": e.row_id,
                "probe_index": e.probe_index,
                "public_seed_id": e.public_seed_id,
            }
            for e in self.entries
        ]
