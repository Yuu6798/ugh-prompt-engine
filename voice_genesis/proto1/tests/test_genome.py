"""test_genome.py — P1 (VG-001) の受け入れテスト: round-trip / 境界値 / out_of_physio_range / 型拒否。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import genome as g


def test_default_genome_within_physio_range():
    gen = g.build_genome("default")
    assert gen.physio_range.out_of_physio_range is False
    assert gen.physio_range.violated_bounds == ()
    assert gen.schema_version == "voice-genome/0.2"


def test_json_round_trip_preserves_equality():
    gen = g.build_genome(
        "roundtrip",
        source=g.SourceSection(tilt=-9.5, source_mode="breathy"),
        resonance=g.ResonanceSection(formant_scale=1.1, formant_offsets=(0.01, -0.02, 0.0, 0.03), bandwidth_scale=1.05),
    )
    text = g.to_json(gen)
    restored = g.from_json(text)
    assert restored == gen


def test_json_round_trip_dict_path():
    gen = g.build_genome("dictpath")
    restored = g.from_dict(g.to_dict(gen))
    assert restored == gen


@pytest.mark.parametrize(
    "key,value",
    [
        ("resonance.formant_scale", 0.80),
        ("resonance.formant_scale", 1.25),
        ("noise.breathiness_base", 0.0),
        ("noise.breathiness_base", 0.6),
        ("source.tilt", -18.0),
        ("source.tilt", -3.0),
        ("microprosody.vibrato_rate_hz", 4.0),
        ("microprosody.vibrato_rate_hz", 7.5),
        ("microprosody.vibrato_depth_cents", 0.0),
        ("microprosody.vibrato_depth_cents", 150.0),
        ("microprosody.jitter_amount", 0.0),
        ("microprosody.jitter_amount", 0.02),
        ("register.transition_width", 1.0),
        ("register.transition_width", 6.0),
    ],
)
def test_physio_prior_boundary_values_are_in_range(key, value):
    """凍結表の境界値そのもの（lo/hi 両端）は範囲内として扱われる（半開区間ではない）。"""
    section_name, field_name = key.split(".")
    kwargs = {field_name: value}
    if section_name == "resonance":
        resonance = g.ResonanceSection(**kwargs)
        gen = g.build_genome("boundary", resonance=resonance)
    elif section_name == "noise":
        noise = g.NoiseSection(**kwargs)
        gen = g.build_genome("boundary", noise=noise)
    elif section_name == "source":
        source = g.SourceSection(**kwargs)
        gen = g.build_genome("boundary", source=source)
    elif section_name == "microprosody":
        microprosody = g.MicroprosodySection(**kwargs)
        gen = g.build_genome("boundary", microprosody=microprosody)
    elif section_name == "register":
        register = g.RegisterSection(**kwargs)
        gen = g.build_genome("boundary", register=register)
    else:  # pragma: no cover - defensive
        raise AssertionError(section_name)

    assert key not in gen.physio_range.violated_bounds


@pytest.mark.parametrize(
    "key,value,expected_key",
    [
        ("formant_scale", 1.30, "resonance.formant_scale"),
        ("formant_scale", 0.5, "resonance.formant_scale"),
    ],
)
def test_out_of_physio_range_flag_for_resonance(key, value, expected_key):
    resonance = g.ResonanceSection(formant_scale=value)
    gen = g.build_genome("outrange", resonance=resonance)
    assert gen.physio_range.out_of_physio_range is True
    assert expected_key in gen.physio_range.violated_bounds


def test_out_of_physio_range_does_not_reject_construction():
    """範囲外は生成拒否ではなくフラグ（§1.5 楽器原理）。例外を投げず構築できる。"""
    source = g.SourceSection(tilt=-100.0)  # 大きく範囲外
    gen = g.build_genome("wild", source=source)
    assert gen is not None
    assert gen.physio_range.out_of_physio_range is True
    assert "source.tilt" in gen.physio_range.violated_bounds


def test_register_boundaries_out_of_range_reports_index():
    register = g.RegisterSection(boundaries_midi=(10.0, 62.0, 74.0, 88.0))  # 10.0 は [40,96] 外
    gen = g.build_genome("badreg", register=register)
    assert "register.boundaries_midi[0]" in gen.physio_range.violated_bounds


def test_register_boundaries_non_ascending_flagged():
    register = g.RegisterSection(boundaries_midi=(62.0, 52.0, 74.0, 88.0))  # 昇順でない
    gen = g.build_genome("badorder", register=register)
    assert "register.boundaries_midi.ascending" in gen.physio_range.violated_bounds


def test_formant_offsets_and_bandwidth_scale_excluded_from_physio_check():
    """凍結表に無いフィールドは範囲チェック対象外（underspec_log_p1.md [UNDERSPEC-P1-1]）。"""
    resonance = g.ResonanceSection(formant_offsets=(5.0, -5.0, 10.0, -10.0), bandwidth_scale=99.0)
    gen = g.build_genome("noprior", resonance=resonance)
    assert gen.physio_range.out_of_physio_range is False


def test_revalidate_physio_range_recomputes():
    gen = g.build_genome("recompute")
    # frozen dataclass を直接改変できないので、わざと不正な physio_range を持つ
    # インスタンスを手作りしてから revalidate で正しい値に戻せることを確認する。
    tampered = g.VoiceGenome(
        name=gen.name,
        source=gen.source,
        resonance=gen.resonance,
        noise=gen.noise,
        register=gen.register,
        microprosody=gen.microprosody,
        range=gen.range,
        physio_range=g.PhysioRangeSection(out_of_physio_range=True, violated_bounds=("bogus",)),
        audit=gen.audit,
    )
    fixed = g.revalidate_physio_range(tampered)
    assert fixed.physio_range.out_of_physio_range is False


# --- 不正な型の拒否 -----------------------------------------------------------


def test_from_dict_rejects_non_dict():
    with pytest.raises(g.GenomeValidationError):
        g.from_dict("not-a-dict")  # type: ignore[arg-type]


def test_from_dict_rejects_wrong_type_for_float_field():
    data = g.to_dict(g.build_genome("valid"))
    data["source"]["tilt"] = "loud"  # str ではなく float を期待
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_wrong_length_tuple():
    data = g.to_dict(g.build_genome("valid"))
    data["resonance"]["formant_offsets"] = [0.0, 0.0]  # 長さ 4 を期待
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_bool_as_float():
    """Python の bool は int のサブクラスなので、明示的に弾く必要がある。"""
    data = g.to_dict(g.build_genome("valid"))
    data["source"]["tilt"] = True
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_json_rejects_malformed_json():
    with pytest.raises(g.GenomeValidationError):
        g.from_json("{not valid json")


def test_from_dict_rejects_bad_audit_types():
    data = g.to_dict(g.build_genome("valid"))
    data["audit"]["residual_gate_passed"] = "yes"  # bool | null を期待
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)
