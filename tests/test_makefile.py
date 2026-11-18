"""The Makefile and the CI workflow, read as text: targets declared, failures not swallowed."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _recipes():
    out, target = {}, None
    for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines():
        head = re.match(r"^([\w-]+):", line)
        if head:
            target = head.group(1)
            out[target] = []
        elif line.startswith("\t") and target:
            out[target].append(line.strip())
    return out


def test_every_target_is_declared_phony():
    phony = next(l for l in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
                 if l.startswith(".PHONY:"))
    assert set(phony.split()[1:]) == set(_recipes())


def test_selftest_target_runs_the_selftest_and_keeps_its_exit_code():
    assert _recipes()["selftest"] == ["python3 pipeline/selftest.py"]


def test_regen_target_runs_only_regen_and_keeps_its_exit_code():
    assert _recipes()["regen"] == ["python3 pipeline/regen.py"]


def test_ci_runs_the_selftest_and_does_not_ignore_its_result():
    lines = [l.strip() for l in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8").splitlines()]
    assert "- run: python3 pipeline/selftest.py" in lines
    assert not [l for l in lines if "||" in l or "continue-on-error" in l]


def test_test_target_runs_the_whole_suite_and_keeps_its_exit_code():
    assert _recipes()["test"] == ["pytest -q -p no:cacheprovider"]


def _ci():
    return (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


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


# ---- Makefile text: nothing that can hide a failure or rewrite published files -------------

def _make_lines():
    lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 8
    return lines


def test_makefile_has_no_ignore_directive():
    assert not [l for l in _make_lines() if re.match(r"\s*\.IGNORE\b", l)]


def test_makefile_has_no_special_target_except_phony():
    special = [l for l in _make_lines() if re.match(r"\.[A-Z_]+\s*:", l)]
    assert [l.split()[0] for l in special] == [".PHONY:"]


def test_makefile_does_not_replace_the_shell():
    assert not [l for l in _make_lines()
                if re.match(r"\s*(override\s+|export\s+)*\.?SHELL\w*\s*[:?+!]*=", l)]


def test_no_make_target_has_a_prerequisite_so_test_never_runs_regen():
    heads = {m.group(1): m.group(2).strip()
             for m in (re.match(r"^([\w-]+):(.*)$", l) for l in _make_lines()) if m}
    assert heads == {"test": "", "regen": "", "selftest": ""}


def test_no_recipe_line_starts_with_a_dash():
    recipes = [l for l in _make_lines() if l.startswith("\t")]
    assert len(recipes) == 3
    assert not [l for l in recipes if l.lstrip("\t @+").startswith("-")]


# ---- CI file text (no YAML library in this repo; read as text) -----------------------------

def _ci_lines():
    return [l.rstrip() for l in _ci().splitlines()]


def test_ci_checks_out_with_actions_checkout_v4():
    assert "      - uses: actions/checkout@v4" in _ci_lines()


def test_ci_sets_up_python_with_setup_python_v5_and_never_node():
    assert "      - uses: actions/setup-python@v5" in _ci_lines()
    assert "node" not in _ci().lower()


def test_ci_job_is_not_switched_off_by_a_condition():
    assert not re.search(r"^\s*(- )?if:", _ci(), re.M)


def test_ci_job_has_a_steps_list_with_the_steps_inside_it():
    lines = _ci_lines()
    jobs, job, steps = lines.index("jobs:"), lines.index("  test:"), lines.index("    steps:")
    assert jobs < job < steps
    assert lines[steps + 1].startswith("      - ")
    assert all(l.startswith("      ") for l in lines[steps + 1:] if l.strip())


def test_ci_pytest_step_runs_the_tests_folder_and_does_not_only_collect():
    assert not re.search(r"--collect-only|--co\b|PYTEST_ADDOPTS|addopts", _ci())
    assert not re.search(r"^\s*env:", _ci(), re.M)
    assert not re.search(r"addopts", (ROOT / "pytest.ini").read_text(encoding="utf-8"))
    step = [l.strip() for l in _ci_lines() if l.strip().startswith("- run: pytest")]
    assert step == ["- run: pytest -q"]
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8").splitlines()
    assert "testpaths = tests" in [l.strip() for l in ini]
