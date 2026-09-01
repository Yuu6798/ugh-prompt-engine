from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement target, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# P1: required scalar C0 identity fields must be nonblank strings, not merely non-hollow.
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    '''REQUIRED_BLOCKING_KEYS: tuple[str, ...] = (\n    "repo.url",\n    "repo.commit_sha",\n    "repo.dirty_tree",\n    "measurement_directory_status",\n    "candidates.meter_paths_sha256",\n    "candidates.generator_paths_sha256",\n    "candidates.schema_paths_sha256",\n    "candidates.test_paths_sha256",\n    "dependencies.python_version",\n    "dependencies.numpy_version",\n    "dependencies.scipy_version",\n    "dependencies.librosa_version",\n    "dependencies.soundfile_version",\n    "sample_format.dtype",\n    "sample_format.channel_policy",\n    "sample_format.resampling_impl",\n    "sample_format.resampling_parameters",\n    "frozen_design.claim_critical_set",\n    "frozen_design.meter_specs",\n    "frozen_design.fixture_spec",\n    "frozen_design.split_spec",\n    "frozen_design.selection_spec",\n    "frozen_design.provenance_spec",\n    "frozen_design.cost_caps",\n    "frozen_design.stop_rules",\n    "independence_ledger",\n    "rng_ledger",\n)\n\n''',
    '''REQUIRED_BLOCKING_KEYS: tuple[str, ...] = (\n    "repo.url",\n    "repo.commit_sha",\n    "repo.dirty_tree",\n    "measurement_directory_status",\n    "candidates.meter_paths_sha256",\n    "candidates.generator_paths_sha256",\n    "candidates.schema_paths_sha256",\n    "candidates.test_paths_sha256",\n    "dependencies.python_version",\n    "dependencies.numpy_version",\n    "dependencies.scipy_version",\n    "dependencies.librosa_version",\n    "dependencies.soundfile_version",\n    "sample_format.dtype",\n    "sample_format.channel_policy",\n    "sample_format.resampling_impl",\n    "sample_format.resampling_parameters",\n    "frozen_design.claim_critical_set",\n    "frozen_design.meter_specs",\n    "frozen_design.fixture_spec",\n    "frozen_design.split_spec",\n    "frozen_design.selection_spec",\n    "frozen_design.provenance_spec",\n    "frozen_design.cost_caps",\n    "frozen_design.stop_rules",\n    "independence_ledger",\n    "rng_ledger",\n)\n\n#: REQUIRED_BLOCKING のうち frozen environment / preprocessing identity を\n#: 表す scalar string fields。`_is_hollow()` は意図的に `0`/`False` を\n#: populated とみなすため、これらは別途 nonblank `str` を必須化する。\n_REQUIRED_STRING_SCALAR_KEYS = frozenset(\n    {\n        "repo.url",\n        "measurement_directory_status",\n        "dependencies.python_version",\n        "dependencies.numpy_version",\n        "dependencies.scipy_version",\n        "dependencies.librosa_version",\n        "dependencies.soundfile_version",\n        "sample_format.dtype",\n        "sample_format.channel_policy",\n        "sample_format.resampling_impl",\n    }\n)\n\n''',
)

replace_once(
    "voice_genesis/calibration/c0_validate.py",
    '''        if key == "repo.commit_sha" and (\n            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None\n        ):\n            missing.append(\n                f"{key}: shape (must be a full 40-character lowercase hex commit SHA, "\n                f"got {value!r})"\n            )\n            continue\n        container_kind = _CONTAINER_TYPE_KEYS.get(key)\n''',
    '''        if key == "repo.commit_sha" and (\n            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None\n        ):\n            missing.append(\n                f"{key}: shape (must be a full 40-character lowercase hex commit SHA, "\n                f"got {value!r})"\n            )\n            continue\n        if key in _REQUIRED_STRING_SCALAR_KEYS and not isinstance(value, str):\n            missing.append(\n                f"{key}: type (must be a nonblank string, got {type(value).__name__})"\n            )\n            continue\n        container_kind = _CONTAINER_TYPE_KEYS.get(key)\n''',
)

# P2: first failure closes the ordered boundary bracket; later passes cannot move last_pass.
replace_once(
    "voice_genesis/calibration/observables.py",
    '''    for level, flag in zip(ordered_levels, pass_flags):\n        passed = bool(flag) if flag is not None else False\n        if passed:\n            last_pass = level\n        elif first_fail is None:\n            first_fail = level\n    return last_pass, first_fail\n''',
    '''    for level, flag in zip(ordered_levels, pass_flags):\n        passed = bool(flag) if flag is not None else False\n        if not passed:\n            first_fail = level\n            break\n        last_pass = level\n    return last_pass, first_fail\n''',
)

# Regression: reject non-string required scalar identity fields.
p = Path("voice_genesis/calibration/tests/test_c0_validate.py")
text = p.read_text(encoding="utf-8").rstrip() + "\n"
append = '''\n\n@pytest.mark.parametrize(\n    ("path", "invalid"),\n    [\n        (("sample_format", "dtype"), False),\n        (("sample_format", "resampling_impl"), True),\n        (("dependencies", "numpy_version"), 0),\n        (("repo", "url"), 123),\n        (("measurement_directory_status",), False),\n    ],\n)\ndef test_required_string_scalar_fields_reject_non_strings(\n    path: tuple[str, ...], invalid: object\n) -> None:\n    manifest = _complete_manifest()\n    node: dict[str, object] = manifest\n    for part in path[:-1]:\n        child = node[part]\n        assert isinstance(child, dict)\n        node = child\n    node[path[-1]] = invalid\n\n    result = c0_validate.validate_c0_manifest(manifest)\n\n    dotted = ".".join(path)\n    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes\n    assert any(item.startswith(f"{dotted}: type") for item in result.missing_required_keys)\n'''
if "test_required_string_scalar_fields_reject_non_strings" in text:
    raise SystemExit("c0 scalar regression already present")
p.write_text(text + append.lstrip("\n"), encoding="utf-8")

# Regression: non-monotonic later success must not invert the first-failure bracket.
p = Path("voice_genesis/calibration/tests/test_observables.py")
text = p.read_text(encoding="utf-8").rstrip() + "\n"
append = '''\n\ndef test_failure_boundary_stops_at_first_failure() -> None:\n    levels = ["L1", "L2", "L3"]\n    flags = [True, None, True]\n\n    last_pass, first_fail = failure_boundary(levels, flags)\n\n    assert last_pass == "L1"\n    assert first_fail == "L2"\n'''
if "test_failure_boundary_stops_at_first_failure" in text:
    raise SystemExit("boundary regression already present")
p.write_text(text + append.lstrip("\n"), encoding="utf-8")
