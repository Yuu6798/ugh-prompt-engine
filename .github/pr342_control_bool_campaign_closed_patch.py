from pathlib import Path

obs_path = Path("voice_genesis/calibration/observables.py")
vocab_path = Path("voice_genesis/calibration/vocab.py")
obs_test_path = Path("voice_genesis/calibration/tests/test_observables.py")
vocab_test_path = Path("voice_genesis/calibration/tests/test_vocab.py")

obs = obs_path.read_text()
class_marker = "\ndef _normalize_keyed_outcomes(outcomes: KeyedOutcomes, kind: str) -> dict[str, bool]:\n"
invalid_class = '''\n\nclass InvalidControlOutcomeError(ValueError):\n    \"\"\"Raised when a keyed control outcome is not an actual ``bool``.\n\n    Missing/invalid meter outputs must be mapped by the caller to the documented\n    failure polarity before entering ``detection_rates``; arbitrary truthy values\n    must never be interpreted as successful control evidence.\n    \"\"\"\n\n    def __init__(self, kind: str, instance_id: str, outcome: object) -> None:\n        self.kind = kind\n        self.instance_id = instance_id\n        self.outcome = outcome\n        super().__init__(\n            \"detection_rates: non-boolean control outcome in \"\n            f\"{kind}_outcomes for {instance_id!r}: {outcome!r}\"\n        )\n'''
if "class InvalidControlOutcomeError" not in obs:
    if class_marker not in obs:
        raise SystemExit("normalize marker not found")
    obs = obs.replace(class_marker, invalid_class + class_marker, 1)

old_normalize = '''def _normalize_keyed_outcomes(outcomes: KeyedOutcomes, kind: str) -> dict[str, bool]:\n    \"\"\"`Mapping[instance_id, outcome]` または `(instance_id, outcome)` の\n    `Sequence` を `dict[instance_id, outcome]` へ正規化する。`Mapping` はキー\n    の一意性が言語仕様上保証されるため対象外。`Sequence` 形式で同一\n    `instance_id` が複数回出現した場合は `DuplicateInstanceIdError` を送出する\n    （素朴に `dict(outcomes)` するだけだと後勝ちで重複が黙って消えるため、\n    明示的に走査して検出する）。\n    \"\"\"\n    if isinstance(outcomes, Mapping):\n        return dict(outcomes)\n    seen: dict[str, bool] = {}\n    duplicate_ids: list[str] = []\n    for instance_id, outcome in outcomes:\n        if instance_id in seen:\n            duplicate_ids.append(instance_id)\n            continue\n        seen[instance_id] = outcome\n    if duplicate_ids:\n        raise DuplicateInstanceIdError(kind, sorted(set(duplicate_ids)))\n    return seen\n'''
new_normalize = '''def _normalize_keyed_outcomes(outcomes: KeyedOutcomes, kind: str) -> dict[str, bool]:\n    \"\"\"Normalize keyed outcomes while validating identity and value shape.\n\n    ``Mapping`` keys are unique by construction; sequence form is checked for\n    duplicate instance IDs. In both forms every outcome must be an actual ``bool``\n    (``type(value) is bool``). Missing/invalid upstream outputs are part of the\n    failure numerator by contract, so callers must map them to ``True`` for\n    negative controls and ``False`` for positive controls before this boundary.\n    Truthy sentinels such as NaN or error strings are rejected fail-closed.\n    \"\"\"\n    items = outcomes.items() if isinstance(outcomes, Mapping) else outcomes\n    seen: dict[str, bool] = {}\n    duplicate_ids: list[str] = []\n    for instance_id, outcome in items:\n        if instance_id in seen:\n            duplicate_ids.append(instance_id)\n            continue\n        if type(outcome) is not bool:\n            raise InvalidControlOutcomeError(kind, instance_id, outcome)\n        seen[instance_id] = outcome\n    if duplicate_ids:\n        raise DuplicateInstanceIdError(kind, sorted(set(duplicate_ids)))\n    return seen\n'''
if old_normalize in obs:
    obs = obs.replace(old_normalize, new_normalize, 1)
elif new_normalize not in obs:
    raise SystemExit("normalize function block not found")

old_doc = '''    negative / positive の二母集団も互いに素でなければならず、同一 instance ID\n    を両側へ再ラベルした場合は `kind=\"cross_class\"` の同例外で fail-closed にする。\n\n    最小数 (`N_neg>=10` かつ `N_pos>=10`) を満たさない construct は結果を\n'''
new_doc = '''    negative / positive の二母集団も互いに素でなければならず、同一 instance ID\n    を両側へ再ラベルした場合は `kind=\"cross_class\"` の同例外で fail-closed にする。\n    outcome 値は actual `bool` のみを受理する。NaN・error string・数値 sentinel 等の\n    非 bool は `InvalidControlOutcomeError` で拒否し、truthy 値を positive-control の\n    成功として誤認しない。missing/invalid は caller が failure polarity に写像して渡す。\n\n    最小数 (`N_neg>=10` かつ `N_pos>=10`) を満たさない construct は結果を\n'''
if old_doc in obs:
    obs = obs.replace(old_doc, new_doc, 1)
elif new_doc not in obs:
    raise SystemExit("detection doc marker not found")
obs_path.write_text(obs.rstrip() + "\n")

vocab = vocab_path.read_text()
old_campaign = '''def campaign_closed(\n    terminal: Mapping[MeterId, TerminalStatus], expected: Iterable[MeterId]\n) -> bool:\n    \"\"\"CAMPAIGN_CLOSED（手続的閉鎖）: `expected` の全 meter が何らかの終端 status に\n    到達しているか（値そのものは問わない。INVALID/NOT_EVALUABLE/DIAGNOSTIC_ONLY も\n    正当な終端であるため = D1）。\n    \"\"\"\n    return all(meter in terminal for meter in expected)\n'''
new_campaign = '''def campaign_closed(\n    terminal: Mapping[MeterId, TerminalStatus],\n    expected: Iterable[MeterId] | None = None,\n) -> bool:\n    \"\"\"Derive procedural campaign closure from the frozen ``MeterId`` set.\n\n    The required meter population is not a runtime choice: every frozen ``MeterId``\n    must have a terminal status. ``expected`` is retained only as a compatibility\n    assertion for existing callers; when supplied it must be an exact, duplicate-free\n    copy of the frozen set and can never shrink the closure requirement.\n    \"\"\"\n    frozen = tuple(MeterId)\n    if expected is not None:\n        supplied = tuple(expected)\n        supplied_set = set(supplied)\n        if len(supplied) != len(supplied_set) or supplied_set != set(frozen):\n            return False\n    return all(meter in terminal for meter in frozen)\n'''
if old_campaign in vocab:
    vocab = vocab.replace(old_campaign, new_campaign, 1)
elif new_campaign not in vocab:
    raise SystemExit("campaign_closed block not found")
vocab_path.write_text(vocab.rstrip() + "\n")

obs_tests = obs_test_path.read_text()
old_import = '''    DetectionResult,\n    DuplicateInstanceIdError,\n    ErrorTerms,\n'''
new_import = '''    DetectionResult,\n    DuplicateInstanceIdError,\n    ErrorTerms,\n    InvalidControlOutcomeError,\n'''
if old_import in obs_tests:
    obs_tests = obs_tests.replace(old_import, new_import, 1)
elif new_import not in obs_tests:
    raise SystemExit("observables import marker not found")
obs_regressions = '''\n\n@pytest.mark.parametrize("invalid", [float("nan"), "meter-error", 1, None])\ndef test_detection_rates_rejects_non_boolean_positive_outcomes(invalid: object) -> None:\n    pos = _keyed("pos", [True] * 9) + [("pos-invalid", invalid)]  # type: ignore[list-item]\n    with pytest.raises(InvalidControlOutcomeError) as excinfo:\n        detection_rates(_keyed("neg", [False] * 10), pos)\n    assert excinfo.value.kind == "pos"\n    assert excinfo.value.instance_id == "pos-invalid"\n\n\ndef test_detection_rates_rejects_truthy_non_boolean_negative_outcome() -> None:\n    neg = {f"neg{i}": False for i in range(10)}\n    neg["neg9"] = "error"  # type: ignore[assignment]\n    with pytest.raises(InvalidControlOutcomeError) as excinfo:\n        detection_rates(neg, _keyed("pos", [True] * 10))\n    assert excinfo.value.kind == "neg"\n    assert excinfo.value.instance_id == "neg9"\n'''
if "test_detection_rates_rejects_non_boolean_positive_outcomes" not in obs_tests:
    obs_tests = obs_tests.rstrip() + obs_regressions + "\n"
obs_test_path.write_text(obs_tests.rstrip() + "\n")

vocab_tests = vocab_test_path.read_text()
vocab_regressions = '''\n\ndef test_campaign_closed_empty_expected_cannot_shrink_frozen_meter_set() -> None:\n    assert campaign_closed({}, []) is False\n\n\ndef test_campaign_closed_incomplete_expected_is_rejected() -> None:\n    incomplete = list(MeterId)[:-1]\n    terminal = {meter: TerminalStatus.DIAGNOSTIC_ONLY for meter in incomplete}\n    assert campaign_closed(terminal, incomplete) is False\n\n\ndef test_campaign_closed_derives_full_meter_set_when_expected_omitted() -> None:\n    terminal = {meter: TerminalStatus.INVALID for meter in MeterId}\n    assert campaign_closed(terminal) is True\n'''
if "test_campaign_closed_empty_expected_cannot_shrink_frozen_meter_set" not in vocab_tests:
    vocab_tests = vocab_tests.rstrip() + vocab_regressions + "\n"
vocab_test_path.write_text(vocab_tests.rstrip() + "\n")
