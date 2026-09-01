from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# provenance.py: prerequisite events must contain a substantive hashed record.
prov_path = Path("voice_genesis/calibration/provenance.py")
prov = prov_path.read_text(encoding="utf-8")

sha_helper = '''def _is_sha256_hex(value: object) -> bool:\n    return (\n        isinstance(value, str)\n        and len(value) == 64\n        and all(ch in "0123456789abcdef" for ch in value)\n    )\n\n\n'''
validator = sha_helper + '''def _valid_unseal_prerequisite_payload(\n    payload: Mapping[str, Any], expected_kind: str\n) -> bool:\n    """Validate the minimum frozen event envelope for an unseal prerequisite.\n\n    The canonical design requires every procedural evidence event to carry the\n    hash of the object it attests to.  A kind-only ledger row therefore cannot\n    satisfy an unseal prerequisite.  The selected-candidate prerequisite also\n    needs the selected candidate identity; otherwise it is not a candidate\n    selection record at all.  Deeper artifact semantics remain committed by the\n    64-hex ``artifact_sha`` and are outside this ledger-level seal check.\n    """\n    if payload.get("kind") != expected_kind:\n        return False\n    if not _is_sha256_hex(payload.get("artifact_sha")):\n        return False\n    if expected_kind == "selected_candidate":\n        candidate_id = payload.get("candidate_id")\n        if not isinstance(candidate_id, str) or not candidate_id:\n            return False\n    return True\n\n\n'''
prov = replace_once(prov, sha_helper, validator, "prerequisite payload validator insertion")
prov = replace_once(
    prov,
    '''        if prerequisite_payload.get("kind") != expected_kind:\n            return False\n''',
    '''        if not _valid_unseal_prerequisite_payload(prerequisite_payload, expected_kind):\n            return False\n''',
    "prerequisite payload validation call",
)
prov_path.write_text(prov, encoding="utf-8")


# observables.py: negative/positive control populations must be disjoint.
obs_path = Path("voice_genesis/calibration/observables.py")
obs = obs_path.read_text(encoding="utf-8")
obs = replace_once(
    obs,
    '''    neg_map = _normalize_keyed_outcomes(neg_outcomes, "neg")\n    pos_map = _normalize_keyed_outcomes(pos_outcomes, "pos")\n\n    n_neg = len(neg_map)\n''',
    '''    neg_map = _normalize_keyed_outcomes(neg_outcomes, "neg")\n    pos_map = _normalize_keyed_outcomes(pos_outcomes, "pos")\n\n    cross_class_ids = sorted(set(neg_map).intersection(pos_map))\n    if cross_class_ids:\n        raise DuplicateInstanceIdError("cross_class", cross_class_ids)\n\n    n_neg = len(neg_map)\n''',
    "cross-class control identity guard",
)
obs = replace_once(
    obs,
    '''    (`n_neg`/`n_pos`) は **distinct instance 数**であり、同一 `instance_id` の\n    重複出現は `DuplicateInstanceIdError` で reject する（silently 潰さない）。\n\n    最小数 (`N_neg>=10` かつ `N_pos>=10`) を満たさない construct は結果を\n''',
    '''    (`n_neg`/`n_pos`) は **distinct instance 数**であり、同一 `instance_id` の\n    重複出現は `DuplicateInstanceIdError` で reject する（silently 潰さない）。\n    negative / positive の二母集団も互いに素でなければならず、同一 instance ID\n    を両側へ再ラベルした場合は `kind="cross_class"` の同例外で fail-closed にする。\n\n    最小数 (`N_neg>=10` かつ `N_pos>=10`) を満たさない construct は結果を\n''',
    "detection_rates disjointness doc",
)
obs_path.write_text(obs, encoding="utf-8")


# Main provenance tests: positive prerequisite events now use valid hashed envelopes.
tp_path = Path("voice_genesis/calibration/tests/test_provenance.py")
tp = tp_path.read_text(encoding="utf-8")
replacements = {
    'ledger.append({"kind": "baseline_audit"})': 'ledger.append({"kind": "baseline_audit", "artifact_sha": "a" * 64})',
    'ledger.append({"kind": "candidate_space"})': 'ledger.append({"kind": "candidate_space", "artifact_sha": "b" * 64})',
    'ledger.append({"kind": "selection_rule"})': 'ledger.append({"kind": "selection_rule", "artifact_sha": "c" * 64})',
    'ledger.append({"kind": "selected_candidate"})': 'ledger.append({"kind": "selected_candidate", "artifact_sha": "d" * 64, "candidate_id": "candidate-test"})',
}
for old, new in replacements.items():
    if old not in tp:
        raise SystemExit(f"test_provenance expected prerequisite expression absent: {old}")
    tp = tp.replace(old, new)
tp_path.write_text(tp, encoding="utf-8")


# Dedicated unseal tests: use canonical split-auth wrapper and validate each payload kind.
tpu_path = Path("voice_genesis/calibration/tests/test_provenance_unseal_prerequisites.py")
tpu = tpu_path.read_text(encoding="utf-8")
tpu = replace_once(
    tpu,
    '''from voice_genesis.calibration.provenance import Ledger\nfrom voice_genesis.calibration.vocab import BlockedCode\n''',
    '''import pytest\n\nfrom voice_genesis.calibration.provenance import Ledger\nfrom voice_genesis.calibration.tests.test_provenance import _check_leakage\nfrom voice_genesis.calibration.vocab import BlockedCode\n''',
    "dedicated unseal imports",
)
tpu = replace_once(
    tpu,
    '''def _append_prerequisites(ledger: Ledger) -> dict[str, str]:\n    refs: dict[str, str] = {}\n    for key, kind in _PREREQUISITES:\n        refs[key] = ledger.append({"kind": kind}).entry_sha\n    return refs\n''',
    '''def _prerequisite_payload(kind: str) -> dict[str, str]:\n    artifact_markers = {\n        "baseline_audit": "a",\n        "candidate_space": "b",\n        "selection_rule": "c",\n        "selected_candidate": "d",\n    }\n    payload = {"kind": kind, "artifact_sha": artifact_markers[kind] * 64}\n    if kind == "selected_candidate":\n        payload["candidate_id"] = "candidate-test"\n    return payload\n\n\ndef _append_prerequisites(ledger: Ledger) -> dict[str, str]:\n    refs: dict[str, str] = {}\n    for key, kind in _PREREQUISITES:\n        refs[key] = ledger.append(_prerequisite_payload(kind)).entry_sha\n    return refs\n''',
    "dedicated prerequisite helper",
)
tpu = tpu.replace("Ledger.check_leakage(\n", "_check_leakage(\n")

extra_unseal_tests = '''\n\n@pytest.mark.parametrize("hollow_kind", [kind for _key, kind in _PREREQUISITES])\ndef test_hollow_prerequisite_event_payload_fails_closed(tmp_path, hollow_kind: str) -> None:\n    """A kind-only prerequisite event is not sufficient to authorize unseal."""\n    ledger = Ledger(tmp_path / f"ledger-{hollow_kind}.jsonl")\n    commitments: dict[str, str] = {}\n    for key, kind in _PREREQUISITES:\n        payload = {"kind": kind} if kind == hollow_kind else _prerequisite_payload(kind)\n        commitments[key] = ledger.append(payload).entry_sha\n    frozen = ledger.append({"kind": "selection_frozen", **commitments})\n    unseal = ledger.append(\n        {\n            "kind": "holdout_unseal",\n            **commitments,\n            "selection_freeze_event_sha": frozen.entry_sha,\n        }\n    )\n    ledger.append({"kind": "render", "row_id": "holdout-1"})\n\n    result = _check_leakage(\n        ledger.entries,\n        holdout_row_ids=["holdout-1"],\n        unseal_seq=unseal.seq,\n    )\n    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE\n\n\ndef test_selected_candidate_prerequisite_requires_candidate_id(tmp_path) -> None:\n    ledger = Ledger(tmp_path / "ledger-selected-candidate-id.jsonl")\n    commitments = _append_prerequisites(ledger)\n    malformed = ledger.append({"kind": "selected_candidate", "artifact_sha": "e" * 64})\n    commitments["selected_candidate_sha"] = malformed.entry_sha\n    frozen = ledger.append({"kind": "selection_frozen", **commitments})\n    unseal = ledger.append(\n        {\n            "kind": "holdout_unseal",\n            **commitments,\n            "selection_freeze_event_sha": frozen.entry_sha,\n        }\n    )\n    ledger.append({"kind": "render", "row_id": "holdout-1"})\n\n    result = _check_leakage(\n        ledger.entries,\n        holdout_row_ids=["holdout-1"],\n        unseal_seq=unseal.seq,\n    )\n    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE\n'''
if "test_hollow_prerequisite_event_payload_fails_closed" in tpu:
    raise SystemExit("dedicated unseal regression already present")
tpu = tpu.rstrip() + extra_unseal_tests + "\n"
tpu_path.write_text(tpu, encoding="utf-8")


# observables regression: one physical instance cannot count in both control classes.
tobs_path = Path("voice_genesis/calibration/tests/test_observables.py")
tobs = tobs_path.read_text(encoding="utf-8")
extra_obs_test = '''\n\ndef test_detection_rates_rejects_instance_ids_reused_across_control_classes() -> None:\n    shared_ids = [f"shared-{i}" for i in range(10)]\n    neg = {instance_id: False for instance_id in shared_ids}\n    pos = {instance_id: True for instance_id in shared_ids}\n\n    with pytest.raises(DuplicateInstanceIdError) as excinfo:\n        detection_rates(neg, pos)\n\n    assert excinfo.value.kind == "cross_class"\n    assert excinfo.value.duplicate_ids == tuple(shared_ids)\n'''
if "test_detection_rates_rejects_instance_ids_reused_across_control_classes" in tobs:
    raise SystemExit("cross-class control regression already present")
tobs = tobs.rstrip() + extra_obs_test + "\n"
tobs_path.write_text(tobs, encoding="utf-8")
