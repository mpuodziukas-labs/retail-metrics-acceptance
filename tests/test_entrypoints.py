"""regen and selftest run as plain scripts from any working directory."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def scratch_copy(tmp_path_factory):
    """A throwaway copy of the files the scripts need; regen writes into the copy only."""
    dst = tmp_path_factory.mktemp("copy")
    for name in ("pipeline", "data", "sql"):
        shutil.copytree(ROOT / name, dst / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "README.md", dst / "README.md")
    (dst / "docs").mkdir()
    return dst


def _script(scratch_copy, name, cwd):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(scratch_copy / "pipeline" / name)],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def test_regen_runs_as_a_script_from_another_directory(scratch_copy, tmp_path):
    done = _script(scratch_copy, "regen.py", tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("regen done: out/metrics.json")
    assert (scratch_copy / "out" / "metrics.json").is_file()


def test_selftest_runs_as_a_script_from_another_directory(scratch_copy, tmp_path):
    done = _script(scratch_copy, "selftest.py", tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == "SELFTEST PASS"
