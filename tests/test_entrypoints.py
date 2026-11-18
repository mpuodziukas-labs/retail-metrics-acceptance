"""regen and selftest run as plain scripts from any working directory."""
import json
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


def _fresh_copy(tmp_path):
    dst = tmp_path / "copy"
    for name in ("pipeline", "data", "sql"):
        shutil.copytree(ROOT / name, dst / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "README.md", dst / "README.md")
    (dst / "docs").mkdir()
    return dst


def _exec(copy, name, cwd, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(copy / "pipeline" / name)],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def _plant_duplicate_in(copy):
    from pipeline import selftest
    bad = copy / "bad.csv"
    selftest.plant_duplicate(copy / "data" / "online_retail_sample.csv", bad)
    shutil.move(str(bad), str(copy / "data" / "online_retail_sample.csv"))


def test_run_script_defaults_read_the_committed_csv_and_write_out_metrics_json(tmp_path):
    copy = _fresh_copy(tmp_path)
    done = _exec(copy, "run.py", tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == "GATE GREEN: wrote metrics.json"
    assert (copy / "out" / "metrics.json").read_bytes() == (ROOT / "out" / "metrics.json").read_bytes()


def test_run_script_exits_non_zero_when_the_gate_is_red(tmp_path):
    copy = _fresh_copy(tmp_path)
    _plant_duplicate_in(copy)
    done = _exec(copy, "run.py", tmp_path)
    assert done.returncode == 1
    assert done.stdout.splitlines()[-1] == "GATE RED: no metrics written"
    assert not (copy / "out").exists()


def test_regen_script_exits_non_zero_and_writes_nothing_when_the_gate_is_red(tmp_path):
    copy = _fresh_copy(tmp_path)
    _plant_duplicate_in(copy)
    done = _exec(copy, "regen.py", tmp_path)
    assert done.returncode == 1
    assert done.stdout.strip() == "GATE RED: regen refused"
    assert not (copy / "out").exists() and not (copy / "dashboard").exists()
    assert (copy / "README.md").read_bytes() == (ROOT / "README.md").read_bytes()


def test_regen_refuses_a_readme_with_two_results_blocks(tmp_path):
    copy = _fresh_copy(tmp_path)
    text = (copy / "README.md").read_text(encoding="utf-8")
    start, end = text.index("<!-- RESULTS:START -->"), text.index("<!-- RESULTS:END -->")
    block = text[start:end + len("<!-- RESULTS:END -->")]
    doubled = text + "\n\n" + block + "\n"
    (copy / "README.md").write_text(doubled, encoding="utf-8")
    done = _exec(copy, "regen.py", tmp_path)
    assert done.returncode != 0
    assert "README markers missing" in done.stderr
    assert not (copy / "out").exists() and not (copy / "dashboard").exists()
    assert (copy / "README.md").read_text(encoding="utf-8") == doubled


NON_ASCII = {"LC_ALL": "C", "PYTHONUTF8": "0"}


def _with_non_ascii_description(copy):
    """Give every line of the top product a description with non-ASCII characters."""
    import csv
    metrics = json.loads((ROOT / "out" / "metrics.json").read_text(encoding="utf-8"))
    code = metrics["top_products"][0]["stock_code"]
    path = copy / "data" / "online_retail_sample.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    for r in rows:
        if r["StockCode"] == code:
            r["Description"] = "Café £50 voucher"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_regen_round_trips_non_ascii_text_without_a_utf_8_locale(tmp_path):
    copy = _fresh_copy(tmp_path)
    _with_non_ascii_description(copy)
    readme = (copy / "README.md").read_text(encoding="utf-8")
    (copy / "README.md").write_text(readme + "\nCafé £ outside the block\n",
                                    encoding="utf-8")
    done = _exec(copy, "regen.py", tmp_path, NON_ASCII)
    assert done.returncode == 0, done.stderr
    assert "Café £50 voucher" in (copy / "dashboard" / "index.html").read_text(
        encoding="utf-8")
    assert "Café £ outside the block" in (copy / "README.md").read_text(
        encoding="utf-8")


@pytest.mark.parametrize("name", ["run.py", "selftest.py"])
def test_scripts_read_the_committed_csv_as_utf_8_without_a_utf_8_locale(tmp_path, name):
    copy = _fresh_copy(tmp_path)
    done = _exec(copy, name, tmp_path, NON_ASCII)
    assert done.returncode == 0, done.stderr


def test_selftest_script_exits_non_zero_when_the_gate_misses_a_fault(tmp_path):
    copy = _fresh_copy(tmp_path)
    path = copy / "data" / "quality_baseline.json"
    allowed = json.loads(path.read_text(encoding="utf-8"))
    allowed["duplicate_order_lines"] = 99
    path.write_text(json.dumps(allowed), encoding="utf-8")
    done = _exec(copy, "selftest.py", tmp_path)
    assert done.returncode == 1
    assert done.stdout.splitlines()[-1] == "SELFTEST FAIL"
    assert "selftest duplicate order line: MISSED" in done.stdout


@pytest.mark.parametrize("target", ["out/x.json", "dashboard/x.html", "docs/x.md", "README.md"])
def test_published_file_guard_fails_a_test_run_that_writes_there(tmp_path, target):
    copy = tmp_path / "copy"
    for name in ("pipeline", "tests"):
        shutil.copytree(ROOT / name, copy / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("README.md", "pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"):
        if (ROOT / name).exists():
            shutil.copy(ROOT / name, copy / name)
    (copy / "docs").mkdir(exist_ok=True)
    (copy / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")
    probe = copy / "tests" / "test_zz_probe.py"
    probe.write_text(
        "from pathlib import Path\n\n"
        "def test_writes():\n"
        f"    p = Path({str(copy)!r}) / {target!r}\n"
        "    p.parent.mkdir(exist_ok=True)\n"
        "    with open(p, 'a') as fh:\n"
        "        fh.write('x')\n", encoding="utf-8")
    run_it = lambda: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(copy),
         str(probe)], cwd=copy, capture_output=True, text=True, timeout=120)
    done = run_it()
    assert done.returncode != 0, done.stdout
    assert "a test wrote into out/, dashboard/, docs/ or README.md" in done.stdout
    probe.write_text("def test_quiet():\n    assert True\n", encoding="utf-8")
    assert run_it().returncode == 0


def _guard_copy(tmp_path, probe_body, seed_readme=True):
    copy = tmp_path / "copy"
    shutil.copytree(ROOT / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "pipeline", copy / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pytest.ini", copy / "pytest.ini")
    (copy / "out").mkdir()
    (copy / "out" / "metrics.json").write_text('{"a": 1}\n', encoding="utf-8")
    if seed_readme:
        (copy / "README.md").write_text("readme\n", encoding="utf-8")
    probe = copy / "tests" / "test_zz_probe.py"
    probe.write_text(probe_body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(copy),
         str(probe)], cwd=copy, capture_output=True, text=True, timeout=120)


def test_published_file_guard_catches_an_in_place_edit_of_out_metrics_json(tmp_path):
    done = _guard_copy(
        tmp_path,
        "from pathlib import Path\n\n"
        "def test_edit():\n"
        "    p = Path(__file__).resolve().parent.parent / 'out' / 'metrics.json'\n"
        "    p.write_text('{\"a\": 2}\\n', encoding='utf-8')\n")
    assert done.returncode != 0, done.stdout
    assert "a test wrote into out/, dashboard/, docs/ or README.md" in done.stdout


def test_published_file_guard_refuses_an_empty_watch_set(tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(ROOT / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "pipeline", copy / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pytest.ini", copy / "pytest.ini")
    (copy / "tests" / "test_zz_probe.py").write_text(
        "def test_quiet():\n    assert True\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(copy),
         str(copy / "tests" / "test_zz_probe.py")],
        cwd=copy, capture_output=True, text=True, timeout=120)
    assert done.returncode != 0, done.stdout


def _replace_csv(copy, mutate):
    import csv
    path = copy / "data" / "online_retail_sample.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(mutate(rows))


def _all_cancelled(rows):
    return [dict(r, Quantity="-" + r["Quantity"].lstrip("-")) for r in rows]


def _zero_price_only(rows):
    return [dict(r, UnitPrice="0.0") for r in rows]


@pytest.mark.parametrize("mutate", [_all_cancelled, _zero_price_only, lambda rows: []],
                         ids=["all-cancelled", "zero-price-only", "header-only"])
@pytest.mark.parametrize("script", ["run.py", "regen.py"])
def test_scripts_refuse_a_file_with_no_clean_order_lines(tmp_path, mutate, script):
    copy = _fresh_copy(tmp_path)
    _replace_csv(copy, mutate)
    readme = (copy / "README.md").read_bytes()
    done = _exec(copy, script, tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "no clean order lines" in done.stdout and "GREEN" not in done.stdout
    assert not (copy / "out").exists() and not (copy / "dashboard").exists()
    assert (copy / "README.md").read_bytes() == readme


def test_run_script_names_the_row_and_column_of_a_bad_cell_without_a_traceback_chain(tmp_path):
    copy = _fresh_copy(tmp_path)
    _replace_csv(copy, lambda rows: [dict(r, UnitPrice="") if i == 4 else r
                                     for i, r in enumerate(rows)])
    done = _exec(copy, "run.py", tmp_path)
    assert done.returncode == 1
    assert done.stderr.strip().splitlines()[-1].startswith(
        "ValueError: row 6, column UnitPrice: ''")
    assert not (copy / "out").exists()


def test_run_script_output_is_identical_under_the_strictest_c_locale(tmp_path):
    default = _fresh_copy(tmp_path / "a")
    strict = _fresh_copy(tmp_path / "b")
    assert _exec(default, "run.py", tmp_path).returncode == 0
    env = {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}
    done = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(strict / "pipeline" / "run.py"), "--out",
         str(strict / "x.json")], cwd=tmp_path, capture_output=True, text=True, timeout=120,
        env={**{k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, **env})
    assert done.returncode == 0, done.stderr
    assert (strict / "x.json").read_bytes() == (default / "out" / "metrics.json").read_bytes()
