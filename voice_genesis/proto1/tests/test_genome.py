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


# --- PR#261 レビュー C5/C6: physio_range 再計算検証 / schema_version 検証 ----


def test_from_dict_rejects_unsupported_schema_version():
    """将来/未知の schema_version を現行レイアウトで誤解釈せず拒否する（C6）。"""
    data = g.to_dict(g.build_genome("valid"))
    data["schema_version"] = "voice-genome/0.3"
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_missing_schema_version_key():
    """schema_version キー自体が欠落している場合も、現行版へのデフォルト補完
    をせず明示的に拒否する（PR#261 レビュー R12: `.get(..., SCHEMA_VERSION)`
    は「キー欠落」を「現行版を宣言」として fail-open してしまっていた）。"""
    data = g.to_dict(g.build_genome("valid"))
    del data["schema_version"]
    assert "schema_version" not in data  # 前提確認
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


# --- PR#261 レビュー R16: 全セクション・全リーフフィールドのキー欠落拒否 ----


@pytest.mark.parametrize(
    "section",
    ["name", "source", "resonance", "noise", "register", "microprosody", "range", "physio_range", "audit"],
)
def test_from_dict_rejects_missing_top_level_section(section):
    """切り詰められた payload（トップレベルのセクション/name キーが欠落）を、
    デフォルト補完せず明示的に拒否する（PR#261 レビュー R16）。"""
    data = g.to_dict(g.build_genome("valid"))
    del data[section]
    assert section not in data  # 前提確認
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


@pytest.mark.parametrize(
    "section,leaf",
    [
        ("source", "tilt"),
        ("source", "source_mode"),
        ("resonance", "formant_scale"),
        ("resonance", "formant_offsets"),
        ("resonance", "bandwidth_scale"),
        ("noise", "breathiness_base"),
        ("noise", "register_gains"),
        ("register", "boundaries_midi"),
        ("register", "transition_width"),
        ("microprosody", "vibrato_rate_hz"),
        ("microprosody", "vibrato_depth_cents"),
        ("microprosody", "jitter_amount"),
        ("microprosody", "jitter_seed"),
        ("range", "lowest_midi"),
        ("range", "highest_midi"),
        ("physio_range", "out_of_physio_range"),
        ("physio_range", "violated_bounds"),
        ("audit", "reference_set_hash"),
        ("audit", "linkability_report_id"),
        ("audit", "residual_gate_passed"),
    ],
)
def test_from_dict_rejects_missing_leaf_field(section, leaf):
    """切り詰められた payload（セクション内のリーフフィールドが欠落）を、
    デフォルト補完せず明示的に拒否する（PR#261 レビュー R16）。"""
    data = g.to_dict(g.build_genome("valid"))
    del data[section][leaf]
    assert leaf not in data[section]  # 前提確認
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_accepts_full_payload_with_all_sections_and_leaves():
    """非退行確認: 全セクション・全リーフフィールドが揃った正しい payload は
    従来どおり受理される。"""
    gen = g.build_genome("valid")
    restored = g.from_dict(g.to_dict(gen))
    assert restored == gen


def test_from_dict_rejects_out_of_physio_range_flag_that_disagrees_with_params():
    """physio_range が実パラメータから再計算した値と一致しない文書を拒否する（C5）。

    source.tilt=-100（明確に範囲外）を持ちながら out_of_physio_range=False
    を宣言する改ざん/手編集文書が、検証なしで通過しないことを確認する。
    """
    data = g.to_dict(g.build_genome("valid", source=g.SourceSection(tilt=-100.0)))
    assert data["physio_range"]["out_of_physio_range"] is True  # 前提確認
    data["physio_range"]["out_of_physio_range"] = False
    data["physio_range"]["violated_bounds"] = []
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_violated_bounds_that_disagrees_with_params():
    """out_of_physio_range は正しくても violated_bounds の中身が実態と食い違う場合も拒否する。"""
    data = g.to_dict(g.build_genome("valid", source=g.SourceSection(tilt=-100.0)))
    data["physio_range"]["violated_bounds"] = ["bogus.field"]
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_accepts_physio_range_that_matches_recomputed_value():
    """正しく再計算済みの physio_range を持つ文書は従来どおり通過する（非退行）。"""
    gen = g.build_genome("wild", source=g.SourceSection(tilt=-100.0))
    restored = g.from_dict(g.to_dict(gen))
    assert restored == gen


# --- PR#261 レビュー R18: 全 float リーフ（リスト内含む）の有限性必須化 ------


def test_from_dict_rejects_nan_in_formant_offsets():
    """formant_offsets（リスト内の float リーフ）への NaN 注入を拒否する。

    `_as_float_tuple` は各要素を `_as_float` 経由で検証するため、単体 float
    フィールドと同じ有限性チェックがリスト内要素にも及ぶことを確認する。
    """
    data = g.to_dict(g.build_genome("valid"))
    data["resonance"]["formant_offsets"] = [0.0, float("nan"), 0.0, 0.0]
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_inf_in_formant_offsets():
    data = g.to_dict(g.build_genome("valid"))
    data["resonance"]["formant_offsets"] = [0.0, float("inf"), 0.0, 0.0]
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_nan_in_scalar_float_leaf():
    """単体 float リーフ（source.tilt）への NaN 注入も同様に拒否する。"""
    data = g.to_dict(g.build_genome("valid"))
    data["source"]["tilt"] = float("nan")
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


def test_from_dict_rejects_negative_inf_in_scalar_float_leaf():
    data = g.to_dict(g.build_genome("valid"))
    data["microprosody"]["vibrato_rate_hz"] = float("-inf")
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


# --- PR#261 レビュー R26: source.source_mode の閉じた語彙検証 ---------------


def test_from_dict_rejects_source_mode_outside_closed_vocabulary():
    """builder（sampler.py `_SOURCE_MODES`）が発行する集合 {modal, breathy,
    pressed} の域外値（例: 手編集による typo や未定義の新モード名）を拒否する。
    """
    data = g.to_dict(g.build_genome("valid"))
    assert data["source"]["source_mode"] in g.SOURCE_MODES  # 前提確認
    data["source"]["source_mode"] = "robotic"  # 域外値
    with pytest.raises(g.GenomeValidationError):
        g.from_dict(data)


@pytest.mark.parametrize("mode", list(g.SOURCE_MODES))
def test_from_dict_accepts_each_valid_source_mode_non_regression(mode):
    """非退行確認: SOURCE_MODES の全値は従来どおり受理される。"""
    gen = g.build_genome("valid", source=g.SourceSection(tilt=-10.0, source_mode=mode))
    restored = g.from_dict(g.to_dict(gen))
    assert restored.source.source_mode == mode
