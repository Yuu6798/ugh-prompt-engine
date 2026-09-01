from __future__ import annotations

import numpy as np

from voice_genesis.calibration.streams import (
    RngLedger,
    derive_generator,
    derive_okm,
    derive_seed,
    hkdf,
    hkdf_expand,
    hkdf_extract,
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
