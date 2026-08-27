"""test_af01_freeze_verifier.py — AF01 v1.0 凍結検証（DESIGN_RUN10 §29 手順 6/7）。

§28 最低テストのうち本ファイルが担当する項目:

```text
19 AF01 complete bundle present and payload ledger verified
20 AF01 spec / generator / manifest / C4 canonical Body hashes fixed
21 AF01 75 unit WAVs and 9 E0 fixtures pass technical integrity
22 AF01 deterministic rebuild reproduces payload ledger
26 E0 parameter truth manifest fixed
```

bundle 実体（音声 8.9 MB）はリポジトリへ置かないため、実体を要する検査は
合成 fixture（tmp_path 上に台帳どおりの中身を作る）で経路を検証する。
実 bundle に対する検査は `af01_freeze_verifier.py --bundle-root` で実行する。
"""
from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import af01_freeze_verifier as v  # noqa: E402
import run10_schema as m  # noqa: E402


@pytest.fixture(scope="module")
def ledger() -> dict:
    return v.load_pinned_ledger()


# --- §28-19: 台帳自身の実バイト検証（bundle 不要） --------------------------


def test_pinned_ledger_matches_frozen_payload_ledger_sha() -> None:
    """§28-19 / §21 R10-G2: 同梱台帳の実バイト sha256 が凍結値と一致する。"""
    report = v.verify_ledger_bytes()
    assert report.verdict == v.VERDICT_PASS
    assert report.checks["payload_ledger_sha256"] == "PASS"
    assert (
        m.compute_file_sha256(v.PINNED_LEDGER_PATH)
        == m.AF01_FROZEN_HASHES["af01_payload_ledger_sha256"]
    )


def test_tampered_ledger_is_reported_as_input_drift(tmp_path: Path) -> None:
    """§21 R10-G2: 台帳が 1 バイトでも違えば AF01_INPUT_DRIFT。"""
    tampered = tmp_path / "PAYLOAD_SHA256SUMS.txt"
    original = v.PINNED_LEDGER_PATH.read_bytes()
    tampered.write_bytes(original.replace(b"AF01.json", b"AF02.json"))
    report = v.verify_ledger_bytes(tampered)
    assert report.verdict == v.VERDICT_DRIFT


def test_absent_ledger_is_reported_as_unavailable(tmp_path: Path) -> None:
    """台帳が無い状態を PASS と取り違えない。"""
    report = v.verify_ledger_bytes(tmp_path / "missing.txt")
    assert report.verdict == v.VERDICT_UNAVAILABLE


# --- §28-20 / §28-21 / §28-26: 台帳の構造と canonical 4 点 ------------------


def test_ledger_structure_matches_freeze_registration(ledger: dict) -> None:
    """§28-21 / §7.3: 75 unit WAV = 25 alias × C3/C4/G4、9 E0 fixture、6 aggregate probe。"""
    checks, problems = v.check_ledger_structure(ledger)
    assert problems == []
    assert set(checks.values()) == {"PASS"}
    unit_wavs = [p for p in ledger if p.split("/")[0] in m.AF01_PITCHES and p.endswith(".wav")]
    assert len(unit_wavs) == m.AF01_UNIT_FILE_COUNT == 75
    for pitch in m.AF01_PITCHES:
        assert len([p for p in unit_wavs if p.startswith(f"{pitch}/")]) == m.AF01_ALIAS_COUNT


def test_canonical_four_hashes_are_fixed(ledger: dict) -> None:
    """§28-20: spec / generator / manifest / canonical C4 Body の 4 点が凍結値と一致。"""
    for name, pin_key in v.CANONICAL_ENTRIES.items():
        assert ledger[name] == m.AF01_FROZEN_HASHES[pin_key], name


def test_e0_truth_manifest_is_present_and_pinned(ledger: dict) -> None:
    """§28-26 / §7.6: E0 ground truth manifest が台帳に存在する。"""
    truth = "E0_calibration/E0_calibration_truth.json"
    assert truth in ledger
    e0_wavs = [p for p in ledger if p.startswith("E0_calibration/") and p.endswith(".wav")]
    assert len(e0_wavs) == m.AF01_E0_CALIBRATION_CASES == 9
    # §7.6 の凍結校正軸（F1 low/base/high・tilt shallow/base/steep・noise zero/base/high）。
    names = sorted(Path(p).stem for p in e0_wavs)
    assert names == [
        "01_f1_low",
        "02_f1_base",
        "03_f1_high",
        "04_tilt_shallow",
        "05_tilt_base",
        "06_tilt_steep",
        "07_noise_zero",
        "08_noise_base",
        "09_noise_high",
    ]


def test_structure_check_detects_missing_fixture(ledger: dict) -> None:
    """構造検査が常時 PASS を返す張りぼてでないこと。"""
    broken = dict(ledger)
    del broken["E0_calibration/09_noise_high.wav"]
    checks, problems = v.check_ledger_structure(broken)
    assert checks["e0_fixture_count"] == "FAIL"
    assert problems


# --- 台帳の構文規約 ---------------------------------------------------------


def test_ledger_round_trips_byte_identically() -> None:
    """台帳の parse / render が実バイトを保存する（再構成が決定論的）。"""
    raw = v.PINNED_LEDGER_PATH.read_bytes()
    entries = v.parse_payload_ledger(raw.decode("utf-8"))
    assert v.render_payload_ledger(entries) == raw


@pytest.mark.parametrize(
    "text, pattern",
    [
        ("deadbeef  a.wav\n", "sha256 が不正"),
        ("", "台帳が空"),
        (f"{'a' * 64} b.wav\n", "形式でない"),
        (f"{'a' * 64}  b.wav\n{'b' * 64}  a.wav\n", "ソートでない"),
        (f"{'a' * 64}  a.wav\n{'b' * 64}  a.wav\n", "重複"),
    ],
)
def test_malformed_ledger_fails_closed(text: str, pattern: str) -> None:
    """台帳の構文・順序・重複を fail-closed で拒否する。"""
    with pytest.raises(v.Af01LedgerError, match=pattern):
        v.parse_payload_ledger(text)


# --- §28-19: bundle 実体照合の経路（合成 fixture） -------------------------


def _build_synthetic_bundle(root: Path, entries: dict) -> None:
    """台帳どおりの sha256 を持つ bundle は作れないため、独自台帳を持つ bundle を作る。"""
    for relative in entries:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode("utf-8"))


def test_verify_bundle_detects_missing_and_mismatched_files(tmp_path: Path) -> None:
    """§29 手順 6: 実体不足・ハッシュ不一致を検出する。"""
    entries = {
        "C4/a.wav": hashlib.sha256(b"C4/a.wav").hexdigest(),
        "C4/i.wav": hashlib.sha256(b"C4/i.wav").hexdigest(),
    }
    root = tmp_path / "bundle"
    root.mkdir()
    _build_synthetic_bundle(root, entries)
    ledger_path = root / "PAYLOAD_SHA256SUMS.txt"
    ledger_path.write_bytes(v.render_payload_ledger(entries))

    # 凍結 sha と一致しないため、まず台帳段階で DRIFT になる（fail-closed の順序）。
    report = v.verify_bundle(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["payload_ledger_sha256"] == "FAIL"


def test_verify_bundle_reports_unavailable_for_missing_root(tmp_path: Path) -> None:
    """bundle 不在を PASS と取り違えない（§29 手順 6 未実行の正直な表現）。"""
    report = v.verify_bundle(tmp_path / "nope")
    assert report.verdict == v.VERDICT_UNAVAILABLE
    assert not report.passed


# --- §28-22: 決定論的 replay の経路 ----------------------------------------


def _authenticated_replay_bundle(tmp_path: Path, generator_body: str, payload: dict) -> Path:
    """generator を台帳へ正しく載せた bundle（実行前認証を通る状態）を作る。"""
    root = tmp_path / "bundle"
    root.mkdir()
    generator = root / "generator_AF01_SF1.py"
    generator.write_text(generator_body, encoding="utf-8")
    entries = dict(payload)
    entries["generator_AF01_SF1.py"] = m.compute_file_sha256(generator)
    (root / "PAYLOAD_SHA256SUMS.txt").write_bytes(v.render_payload_ledger(entries))
    return root


def test_deterministic_replay_detects_generator_drift(tmp_path: Path, monkeypatch) -> None:
    """§28-22 / §29 手順 7: 再生成 payload が凍結台帳と違えば DRIFT。

    実 generator は bundle 側にあるため、ここでは replay 機構そのものを
    合成 generator で検証する（台帳照合の向きが正しいこと）。
    """
    body = textwrap.dedent(
        """
        from pathlib import Path
        Path("C4").mkdir(exist_ok=True)
        Path("C4/a.wav").write_bytes(b"drifted")
        """
    ).strip() + "\n"
    root = _authenticated_replay_bundle(
        tmp_path, body, {"C4/a.wav": hashlib.sha256(b"frozen").hexdigest()}
    )
    monkeypatch.setattr(
        v,
        "verify_ledger_bytes",
        lambda path=None: v.Af01VerificationReport(
            verdict=v.VERDICT_PASS, checks={"payload_ledger_sha256": "PASS"}
        ),
    )
    monkeypatch.setitem(
        m.AF01_FROZEN_HASHES,
        "af01_generator_sha256",
        m.compute_file_sha256(root / "generator_AF01_SF1.py"),
    )
    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["generator_authenticated_before_run"] == "PASS"
    assert report.checks["generator_run"] == "PASS"
    assert report.checks["deterministic_payload_replay"] == "FAIL"
    assert report.mismatches


def test_deterministic_replay_passes_when_payload_is_identical(
    tmp_path: Path, monkeypatch
) -> None:
    """replay 機構が常時 DRIFT を返す張りぼてでないこと。"""
    body = textwrap.dedent(
        """
        from pathlib import Path
        Path("C4").mkdir(exist_ok=True)
        Path("C4/a.wav").write_bytes(b"frozen")
        """
    ).strip() + "\n"
    root = _authenticated_replay_bundle(
        tmp_path, body, {"C4/a.wav": hashlib.sha256(b"frozen").hexdigest()}
    )
    monkeypatch.setattr(
        v,
        "verify_ledger_bytes",
        lambda path=None: v.Af01VerificationReport(
            verdict=v.VERDICT_PASS, checks={"payload_ledger_sha256": "PASS"}
        ),
    )
    monkeypatch.setitem(
        m.AF01_FROZEN_HASHES,
        "af01_generator_sha256",
        m.compute_file_sha256(root / "generator_AF01_SF1.py"),
    )
    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_PASS


def test_deterministic_replay_reports_unavailable_without_generator(tmp_path: Path) -> None:
    """generator 不在を PASS と取り違えない。"""
    report = v.verify_deterministic_replay(tmp_path)
    assert report.verdict == v.VERDICT_UNAVAILABLE


# --- CLI --------------------------------------------------------------------


def test_cli_ledger_only_mode_passes(capsys) -> None:
    """`af01_freeze_verifier.py`（引数なし）は台帳自己検証＋構造検査で PASS する。"""
    assert v.main([]) == 0
    printed = capsys.readouterr().out
    assert '"verdict": "PASS"' in printed


# --- generator の実行前認証（PR #330 Codex 第 1 巡 P1） --------------------


def _replay_bundle(tmp_path: Path, generator_body: str, ledger: dict) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "generator_AF01_SF1.py").write_text(generator_body, encoding="utf-8")
    (root / "PAYLOAD_SHA256SUMS.txt").write_bytes(v.render_payload_ledger(ledger))
    return root


def _pass_ledger_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        v,
        "verify_ledger_bytes",
        lambda path=None: v.Af01VerificationReport(
            verdict=v.VERDICT_PASS, checks={"payload_ledger_sha256": "PASS"}
        ),
    )


def test_replay_refuses_to_execute_a_drifted_generator(tmp_path: Path, monkeypatch) -> None:
    """台帳と実バイトが違う generator を **実行せずに** DRIFT にする。

    drift を検出するはずの検証器が、drift した任意の Python を実行する経路に
    なってはならない（AGENTS.md「hash した bytes と実行された bytes の間の窓」）。
    """
    sentinel = tmp_path / "executed"
    body = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"
    ledger = {
        "C4/a.wav": hashlib.sha256(b"frozen").hexdigest(),
        "generator_AF01_SF1.py": m.AF01_FROZEN_HASHES["af01_generator_sha256"],
    }
    root = _replay_bundle(tmp_path, body, ledger)
    _pass_ledger_gate(monkeypatch)

    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["generator_authenticated_before_run"] == "FAIL"
    assert not sentinel.exists(), "認証に失敗した generator を実行してはならない"


def test_replay_refuses_when_ledger_generator_sha_disagrees_with_pin(
    tmp_path: Path, monkeypatch
) -> None:
    """台帳の generator sha が凍結 pin と違えば実行しない。"""
    sentinel = tmp_path / "executed"
    body = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"
    root = _replay_bundle(tmp_path, body, {"generator_AF01_SF1.py": "0" * 64})
    _pass_ledger_gate(monkeypatch)

    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["generator_ledger_matches_frozen_pin"] == "FAIL"
    assert not sentinel.exists()


def test_replay_refuses_when_generator_is_absent_from_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    """台帳に generator が載っていない bundle は実行しない。"""
    root = _replay_bundle(tmp_path, "pass\n", {"C4/a.wav": "0" * 64})
    _pass_ledger_gate(monkeypatch)
    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["generator_in_ledger"] == "FAIL"


def test_replay_detects_generator_self_mutation(tmp_path: Path, monkeypatch) -> None:
    """実行中に自身を書き換える generator を実行後の再 hash で捕まえる。"""
    body = (
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[0]).write_text('mutated\\n')\n"
    )
    root = _replay_bundle(tmp_path, body, {"generator_AF01_SF1.py": "0" * 64})
    digest = m.compute_file_sha256(root / "generator_AF01_SF1.py")
    (root / "PAYLOAD_SHA256SUMS.txt").write_bytes(
        v.render_payload_ledger({"generator_AF01_SF1.py": digest})
    )
    _pass_ledger_gate(monkeypatch)
    monkeypatch.setitem(m.AF01_FROZEN_HASHES, "af01_generator_sha256", digest)

    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.checks["generator_unchanged_after_run"] == "FAIL"


def test_replay_exclusion_set_is_closed_and_minimal() -> None:
    """replay の除外集合が黙って広がらないよう閉世界で固定する。

    除外が増えると「再生成できていないのに PASS」になる。generator 自身の
    ソースは定義上 replay の入力であり、それ以外の除外は実測の裏付けとともに
    明示登録することを要求する。
    """
    assert v.REPLAY_INPUT_ONLY_ENTRIES == ("generator_AF01_SF1.py",)


def test_replay_reports_unreproduced_payload_as_drift(tmp_path: Path, monkeypatch) -> None:
    """generator が台帳の一部を出力しなければ DRIFT（欠落を黙って落とさない）。"""
    body = 'from pathlib import Path\nPath("a.txt").write_text("x")\n'
    root = _authenticated_replay_bundle(
        tmp_path,
        body,
        {"a.txt": hashlib.sha256(b"x").hexdigest(), "b.txt": "0" * 64},
    )
    monkeypatch.setattr(
        v,
        "verify_ledger_bytes",
        lambda path=None: v.Af01VerificationReport(
            verdict=v.VERDICT_PASS, checks={"payload_ledger_sha256": "PASS"}
        ),
    )
    monkeypatch.setitem(
        m.AF01_FROZEN_HASHES,
        "af01_generator_sha256",
        m.compute_file_sha256(root / "generator_AF01_SF1.py"),
    )
    report = v.verify_deterministic_replay(root)
    assert report.verdict == v.VERDICT_DRIFT
    assert report.missing == ["b.txt"]
