"""What the quality gate prints: one line per check, then a verdict line."""
import re

from pipeline import run, selftest

CHECK_LINE = re.compile(r"^(ok |RED) (\w+) \((\w+)\): observed (\d+), allowed (\d+)$")


def _run(capsys, csv_path, out):
    code = run.main(["--csv", str(csv_path), "--out", str(out)])
    return code, capsys.readouterr().out.splitlines()


def test_green_run_prints_one_aligned_line_per_check_then_the_file_name(tmp_path, capsys):
    out = tmp_path / "m.json"
    code, lines = _run(capsys, run.CSV, out)
    assert code == 0
    found = [CHECK_LINE.match(l) for l in lines[:-1]]
    assert all(found), lines
    assert [mo.group(2) for mo in found] == [c[0] for c in run.CHECKS]
    assert {mo.group(1) for mo in found} == {"ok "}
    assert lines[-1] == "GATE GREEN: wrote m.json"


def test_red_run_marks_the_failing_check_and_says_nothing_was_written(tmp_path, capsys):
    bad = tmp_path / "dup.csv"
    selftest.plant_duplicate(run.CSV, bad)
    code, lines = _run(capsys, bad, tmp_path / "m.json")
    assert code == 1
    assert "RED duplicate_order_lines (uniqueness): observed 4, allowed 3" in lines
    assert lines[-1] == "GATE RED: no metrics written"
    assert all(CHECK_LINE.match(l) for l in lines[:-1])
