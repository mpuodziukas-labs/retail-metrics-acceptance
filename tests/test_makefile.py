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
