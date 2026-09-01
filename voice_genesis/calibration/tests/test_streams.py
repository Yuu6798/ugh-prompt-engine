from __future__ import annotations

import numpy as np

from voice_genesis.calibration.streams import (
    RngLedger,
    derive_generator,
    derive_okm,
    derive_seed,
    expected_rng_stream_names,
    hkdf,
    hkdf_expand,
    hkdf_extract,
    stream_name,
)


def test_hkdf_rfc5869_case1_vectors() -> None:
    """RFC 5869 §A.1 Test Case 1 (Basic test case with SHA-256)."""
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    length = 42

    expected_prk = bytes.fromhex(
        "077709362c2e32df0ddc3f0dc47bba63"
        "90b6c73bb50f9c3122ec844ad7c2b3e5"
    )
    expected_okm = bytes.fromhex(
        "3cb25f25faacd57a90434f64d0362f2a"
        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf3"
        "4007208d5b887185865"
    )

    prk = hkdf_extract(salt, ikm)
    assert prk == expected_prk

    okm = hkdf_expand(prk, info, length)
    assert okm == expected_okm

    okm_combined = hkdf(salt=salt, ikm=ikm, info=info, length=length)
    assert okm_combined == expected_okm


def test_derive_seed_is_deterministic() -> None:
    secret = b"\x01" * 32
    kwargs = dict(
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="deadbeef",
        probe_index=0,
        purpose="generator",
    )
    seed_a = derive_seed(secret, **kwargs)
    seed_b = derive_seed(secret, **kwargs)
    assert seed_a == seed_b
    assert isinstance(seed_a, int)
    assert 0 <= seed_a < 2**64


def test_derive_seed_distinct_purposes_give_distinct_seeds() -> None:
    secret = b"\x02" * 32
    base = dict(
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-1",
        probe_index=0,
    )
    seed_gen = derive_seed(secret, purpose="generator", **base)
    seed_tie = derive_seed(secret, purpose="split_tiebreak", **base)
    assert seed_gen != seed_tie


def test_derive_seed_distinct_row_ids_give_distinct_seeds() -> None:
    secret = b"\x03" * 32
    base = dict(
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        probe_index=0,
        purpose="generator",
    )
    seed_1 = derive_seed(secret, row_id="row-1", **base)
    seed_2 = derive_seed(secret, row_id="row-2", **base)
    assert seed_1 != seed_2


def test_derive_seed_distinct_secrets_give_distinct_seeds() -> None:
    kwargs = dict(
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-1",
        probe_index=0,
        purpose="generator",
    )
    seed_a = derive_seed(b"\x00" * 32, **kwargs)
    seed_b = derive_seed(b"\x01" * 32, **kwargs)
    assert seed_a != seed_b


def test_derive_generator_produces_reproducible_stream() -> None:
    secret = b"\x04" * 32
    kwargs = dict(
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-1",
        probe_index=0,
        purpose="generator",
    )
    gen_a = derive_generator(secret, **kwargs)
    gen_b = derive_generator(secret, **kwargs)
    draws_a = gen_a.standard_normal(10)
    draws_b = gen_b.standard_normal(10)
    assert np.array_equal(draws_a, draws_b)


def test_length_prefix_encoding_prevents_field_boundary_collision() -> None:
    """[UNDERSPEC-CAL-02] の長さ接頭辞方式が区切り文字連結より衝突耐性を持つことの
    直接検証: "ab"+"c" と "a"+"bc" は素朴な連結だと同じバイト列になりうるが、
    長さ接頭辞方式では異なる OKM を生む。"""
    secret = b"\x05" * 32
    okm_1 = derive_okm(
        secret,
        campaign_id="ab",
        family="c",
        split="x",
        row_id="row",
        probe_index=0,
        purpose="p",
    )
    okm_2 = derive_okm(
        secret,
        campaign_id="a",
        family="bc",
        split="x",
        row_id="row",
        probe_index=0,
        purpose="p",
    )
    assert okm_1 != okm_2


def test_rng_ledger_records_public_identifier_not_secret() -> None:
    secret = b"\x06" * 32
    ledger = RngLedger()
    entry = ledger.record(
        secret,
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-1",
        probe_index=0,
        purpose="generator",
    )
    assert entry.public_seed_id != secret.hex()
    assert len(entry.public_seed_id) == 64
    records = ledger.to_records()
    assert len(records) == 1
    serialized = str(records)
    assert secret.hex() not in serialized


def test_rng_ledger_accumulates_multiple_entries() -> None:
    secret = b"\x07" * 32
    ledger = RngLedger()
    for i in range(3):
        ledger.record(
            secret,
            campaign_id="RUN10-CAL",
            family="F0_CONTROL",
            split="CALIBRATION",
            row_id=f"row-{i}",
            probe_index=0,
            purpose="generator",
        )
    assert len(ledger.entries) == 3
    assert len(ledger.to_records()) == 3


def test_rng_ledger_records_carry_stream_name_and_seeded() -> None:
    """Codex レビュー 2026-09-01 P1: `to_records()` は `stream_name`/`seeded`
    を欠いており、canonical な producer の出力が `c0_validate` の RNG 台帳
    形状検査を常に BLOCK していた。`stream_name` は C0 記録粒度（finding #2
    DESIGN RULING: family+purpose の粗粒度、row_id/probe_index には依らない）
    で生成される。"""
    secret = b"\x08" * 32
    ledger = RngLedger()
    entry = ledger.record(
        secret,
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-1",
        probe_index=2,
        purpose="generator",
    )
    assert entry.stream_name == "F0_CONTROL/render"
    assert entry.seeded is True

    [record] = ledger.to_records()
    assert record["stream_name"] == entry.stream_name
    assert record["seeded"] is True
    assert record["public_seed_id"] == entry.public_seed_id


def test_stream_name_split_purposes_are_family_independent() -> None:
    """`purpose="split_hmac"`/`"split_tiebreak"` は family に依らず固定名を
    返す（設計正本 §3.3: split/tie-break はキャンペーン全体で唯一の stream）。
    """
    assert stream_name(family="F0_CONTROL", purpose="split_hmac") == "split/hmac"
    assert stream_name(family="FORMANT_GT", purpose="split_hmac") == "split/hmac"
    assert stream_name(family="F0_CONTROL", purpose="split_tiebreak") == "split/tiebreak"
    assert stream_name(family="FORMANT_GT", purpose="split_tiebreak") == "split/tiebreak"


def test_expected_rng_stream_names_matches_frozen_family_count() -> None:
    """closed set は 7 family の render stream + split/hmac + split/tiebreak
    の 9 件（設計正本 §3.3 DESIGN RULING、finding #2）。"""
    names = expected_rng_stream_names()
    assert len(names) == 9
    assert "split/hmac" in names
    assert "split/tiebreak" in names
    assert "F0_CONTROL/render" in names


def test_rng_ledger_producer_round_trips_through_c0_validate_with_no_rng_block() -> None:
    """producer (`RngLedger.record` 経由の `to_records()`) → validator
    (`c0_validate._check_rng_ledger_shape` / `_check_rng_ledger_closed_set` /
    `BLOCKED_C0_UNSEEDED_RNG`) の往復テスト（Codex レビュー 2026-09-01 P1
    finding #2 の回帰防止: canonical producer が §3.3 の closed set（family
    ごとの render stream 1 個 ∪ split/hmac ∪ split/tiebreak）を過不足なく
    record すれば、validator 側で一切 BLOCK されないことを確認する）。
    """
    from voice_genesis.calibration import c0_validate, vocab
    from voice_genesis.calibration.fixtures.axes import FAMILY_ORDER

    secret = b"\x09" * 32
    ledger = RngLedger()
    for family in FAMILY_ORDER:
        ledger.record(
            secret,
            campaign_id="RUN10-CAL",
            family=family.value,
            split="CALIBRATION",
            row_id="n/a",
            probe_index=0,
            purpose="generator",
        )
    ledger.record(
        secret,
        campaign_id="RUN10-CAL",
        family="SPLIT",
        split="CALIBRATION",
        row_id="n/a",
        probe_index=0,
        purpose="split_hmac",
    )
    ledger.record(
        secret,
        campaign_id="RUN10-CAL",
        family="SPLIT",
        split="CALIBRATION",
        row_id="n/a",
        probe_index=0,
        purpose="split_tiebreak",
    )

    manifest = {"rng_ledger": ledger.to_records()}
    result = c0_validate.validate_c0_manifest(manifest)

    # RNG 台帳自体は形状・seed 参照・closed set いずれも妥当なので、RNG 由来の
    # block/missing key は一切現れない（他キー欠落による
    # BLOCKED_C0_MANIFEST_INCOMPLETE は本テストの対象外 — manifest はわざと
    # rng_ledger のみを与えている）。
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG not in result.blocked_codes
    assert not any(k.startswith("rng_ledger") for k in result.missing_required_keys)
    assert result.unseeded_rng_streams == ()
