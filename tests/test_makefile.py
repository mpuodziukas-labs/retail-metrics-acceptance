"""The Makefile and the CI workflow, read as text: targets declared, failures not swallowed."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _recipes():
    out, target = {}, None
    for line in (ROOT / "Makefile").read_text().splitlines():
        head = re.match(r"^([\w-]+):", line)
        if head:
            target = head.group(1)
            out[target] = []
        elif line.startswith("\t") and target:
            out[target].append(line.strip())
    return out


def test_every_target_is_declared_phony():
    phony = next(l for l in (ROOT / "Makefile").read_text().splitlines()
                 if l.startswith(".PHONY:"))
    assert set(phony.split()[1:]) == set(_recipes())


def test_selftest_target_runs_the_selftest_and_keeps_its_exit_code():
    assert _recipes()["selftest"] == ["python3 pipeline/selftest.py"]


def test_regen_target_runs_only_regen_and_keeps_its_exit_code():
    assert _recipes()["regen"] == ["python3 pipeline/regen.py"]


def test_ci_runs_the_selftest_and_does_not_ignore_its_result():
    lines = [l.strip() for l in (ROOT / ".github/workflows/ci.yml").read_text().splitlines()]
    assert "- run: python3 pipeline/selftest.py" in lines
    assert not [l for l in lines if "||" in l or "continue-on-error" in l]


def test_test_target_runs_the_whole_suite_and_keeps_its_exit_code():
    assert _recipes()["test"] == ["pytest -q -p no:cacheprovider"]


def _ci():
    return (ROOT / ".github/workflows/ci.yml").read_text()


def test_ci_triggers_on_push_and_pull_request():
    assert re.search(r"^on: \[(push, pull_request|pull_request, push)\]$", _ci(), re.M)


def test_ci_runs_on_ubuntu_and_checks_out_the_repo_first():
    text = _ci()
    assert re.search(r"^    runs-on: ubuntu-", text, re.M)
    steps = [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]
    assert steps[0].startswith("- uses: actions/checkout@")


def test_ci_python_is_3_10_or_later():
    version = re.search(r'python-version: "(\d+)\.(\d+)"', _ci())
    assert version and (int(version.group(1)), int(version.group(2))) >= (3, 10)


def test_ci_installs_pytest_before_running_the_suite_and_the_selftest():
    steps = [l.strip() for l in _ci().splitlines() if l.strip().startswith("- run:")]
    assert steps == ["- run: pip install pytest", "- run: pytest -q",
                     "- run: python3 pipeline/selftest.py"]
