import csv
import json
from pathlib import Path

import pytest

from pipeline import run, selftest


def test_load_row_count_equals_csv(conn, rows):
    assert conn.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0] == len(rows) == 15000


def test_load_maps_blank_to_null(conn, rows):
    blanks = sum(1 for r in rows if not r["CustomerID"])
    assert blanks > 3000
    assert conn.execute("SELECT COUNT(*) FROM raw_orders WHERE CustomerID IS NULL").fetchone()[0] == blanks
    assert conn.execute("SELECT COUNT(*) FROM raw_orders WHERE CustomerID = ''").fetchone()[0] == 0


def test_all_views_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert {"dim_products", "dim_customers", "clean_orders", "customer_order_windows",
            "monthly_revenue", "product_country_revenue", "customer_summary",
            "cohort_retention", "monthly_cancellation", "headline_metrics"} <= names


def test_quality_reports_four_named_dimensions(conn):
    checks = run.run_quality(conn, run.load_baseline())
    assert {c["name"]: c["dimension"] for c in checks} == {
        "duplicate_order_lines": "uniqueness", "null_join_key": "completeness",
        "negative_price": "validity", "orphan_rows": "integrity"}


def test_baseline_records_known_duplicates(conn):
    base = run.load_baseline()
    assert base["duplicate_order_lines"] == 3
    obs = {c["name"]: c["observed"] for c in run.run_quality(conn, base)}
    assert obs["duplicate_order_lines"] == 3 and obs["null_join_key"] == 0


def _write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _null_key_check(tmp_path, price):
    line = {"InvoiceNo": "N1", "StockCode": "", "Description": "x", "Quantity": "2",
            "InvoiceDate": "2011-01-05 10:00:00", "UnitPrice": price,
            "CustomerID": "c9", "Country": "France"}
    _write([line], tmp_path / "n.csv")
    conn = run.build_db(tmp_path / "n.csv")
    return {c["name"]: c for c in run.run_quality(conn, run.load_baseline())}["null_join_key"]


def test_null_key_line_with_zero_price_is_not_counted(tmp_path):
    check = _null_key_check(tmp_path, "0.0")
    assert check["observed"] == 0 and check["ok"]


def test_null_key_line_with_a_price_is_counted(tmp_path):
    check = _null_key_check(tmp_path, "3.5")
    assert check["observed"] == 1 and not check["ok"]


def test_negative_price_detected(tmp_path, rows):
    bad = [dict(r) for r in rows]
    bad[0]["UnitPrice"] = "-1.5"
    _write(bad, tmp_path / "neg.csv")
    conn = run.build_db(tmp_path / "neg.csv")
    red = {c["name"] for c in run.run_quality(conn, run.load_baseline()) if not c["ok"]}
    assert "negative_price" in red


def test_main_green_writes_metrics(tmp_path):
    out = tmp_path / "m.json"
    assert run.main(["--csv", str(run.CSV), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["headline"]["orders"] > 0


def test_main_red_exits_1_and_writes_nothing(tmp_path):
    bad, out = tmp_path / "dup.csv", tmp_path / "m.json"
    selftest.plant_duplicate(run.CSV, bad)
    assert run.main(["--csv", str(bad), "--out", str(out)]) == 1
    assert not out.exists()


def test_mutant_disabled_quality_check_lets_fault_through(tmp_path):
    bad = tmp_path / "null.csv"
    selftest.plant_null_key(run.CSV, bad)
    guarded = tmp_path / "g.json"
    mutant = tmp_path / "mut.json"
    assert run.main(["--csv", str(bad), "--out", str(guarded)]) == 1
    assert run.main(["--csv", str(bad), "--out", str(mutant), "--no-quality"]) == 0
    m = json.loads(mutant.read_text())
    assert m["headline"]["revenue_gap"] > 0, "silent revenue loss must be visible once the gate is off"


def test_selftest_catches_both_faults_and_keeps_control_green(capsys):
    assert selftest.main() == 0
    own = [l for l in capsys.readouterr().out.splitlines()
           if l.startswith(("selftest ", "SELFTEST"))]
    assert own == [
        "selftest duplicate order line: CAUGHT", "selftest NULL join key: CAUGHT",
        "selftest clean sample stays green: yes", "SELFTEST PASS"]


def test_metrics_json_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    run.main(["--csv", str(run.CSV), "--out", str(a)])
    run.main(["--csv", str(run.CSV), "--out", str(b)])
    assert a.read_bytes() == b.read_bytes()


def test_vendored_sample_checksum_matches_source_note():
    import hashlib
    sha = hashlib.sha256(run.CSV.read_bytes()).hexdigest()
    assert sha in (run.ROOT / "data" / "SOURCE.md").read_text()


def test_selftest_fails_when_gate_misses_a_planted_fault(monkeypatch, capsys):
    def blind_gate(argv):
        Path(argv[argv.index("--out") + 1]).write_text("{}")
        return 0
    monkeypatch.setattr(run, "main", blind_gate)
    assert selftest.main() == 1
    assert "MISSED" in capsys.readouterr().out


def test_selftest_fails_when_clean_control_goes_red(monkeypatch, capsys):
    monkeypatch.setattr(run, "main", lambda argv: 1)
    assert selftest.main() == 1
    assert "stays green: NO" in capsys.readouterr().out


def _line(**kw):
    line = {"InvoiceNo": "D1", "StockCode": "S1", "Description": "x", "Quantity": "2",
            "InvoiceDate": "2011-01-05 10:00:00", "UnitPrice": "2.0",
            "CustomerID": "c1", "Country": "France"}
    line.update(kw)
    return line


def _checks(tmp_path, lines):
    _write(lines, tmp_path / "q.csv")
    conn = run.build_db(tmp_path / "q.csv")
    return {c["name"]: c for c in run.run_quality(conn, run.load_baseline())}


def test_lines_differing_only_in_price_are_not_duplicates(tmp_path):
    lines = [_line(), _line(UnitPrice="3.0"), _line()]
    assert _checks(tmp_path, lines)["duplicate_order_lines"]["observed"] == 1


@pytest.mark.parametrize("column,other", [("InvoiceDate", "2011-01-06 10:00:00"),
                                          ("CustomerID", "c2")])
def test_lines_differing_only_in_one_key_column_are_not_duplicates(tmp_path, column, other):
    lines = [_line(), _line(**{column: other})]
    assert _checks(tmp_path, lines)["duplicate_order_lines"]["observed"] == 0


def test_null_key_ignores_a_non_sale_line(tmp_path):
    check = _checks(tmp_path, [_line(StockCode="", Quantity="-1", UnitPrice="3.5")])["null_join_key"]
    assert check["observed"] == 0 and check["ok"]


def test_main_creates_missing_output_folders(tmp_path):
    out = tmp_path / "a" / "b" / "m.json"
    assert run.main(["--csv", str(run.CSV), "--out", str(out)]) == 0
    assert out.exists()


def _stub_run(monkeypatch, fault_codes, write_on_fault=False):
    calls = []

    def fake(argv):
        if argv[argv.index("--csv") + 1] == str(run.CSV):
            return 0
        calls.append(argv)
        if write_on_fault:
            Path(argv[argv.index("--out") + 1]).write_text("{}")
        return fault_codes[len(calls) - 1]
    monkeypatch.setattr(run, "main", fake)


@pytest.mark.parametrize("codes", [(0, 1), (1, 0)])
def test_selftest_fails_when_only_one_fault_is_missed(monkeypatch, capsys, codes):
    _stub_run(monkeypatch, codes)
    assert selftest.main() == 1
    out = capsys.readouterr().out
    assert "MISSED" in out and "CAUGHT" in out and out.endswith("SELFTEST FAIL\n")


def test_selftest_counts_red_exit_that_wrote_metrics_as_missed(monkeypatch, capsys):
    _stub_run(monkeypatch, (1, 1), write_on_fault=True)
    assert selftest.main() == 1
    out = capsys.readouterr().out
    assert out.count("MISSED") == 2 and "SELFTEST FAIL" in out


def test_plant_null_key_skips_guest_lines(tmp_path):
    src, dst = tmp_path / "s.csv", tmp_path / "d.csv"
    _write([_line(InvoiceNo="G1", CustomerID=""), _line(InvoiceNo="K1")], src)
    selftest.plant_null_key(src, dst)
    with open(dst, newline="") as fh:
        got = {r["InvoiceNo"]: r["StockCode"] for r in csv.DictReader(fh)}
    assert got == {"G1": "S1", "K1": ""}


def test_csv_is_read_as_utf_8(tmp_path):
    path = tmp_path / "u.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_line()))
        w.writeheader()
        w.writerow(_line(Description="Caf\u00e9 \u00a350 voucher"))
    conn = run.build_db(path)
    assert conn.execute("SELECT Description FROM raw_orders").fetchone()[0] == "Caf\u00e9 \u00a350 voucher"


def test_vendored_sample_keeps_its_pound_sign(conn):
    desc = conn.execute(
        "SELECT Description FROM raw_orders WHERE Description LIKE '%Gift Voucher %'").fetchone()[0]
    assert "\u00a3" in desc


def test_sql_files_are_applied_in_name_order(tmp_path, monkeypatch):
    sql = tmp_path / "sql"
    sql.mkdir()
    (sql / "01_first.sql").write_text("DROP VIEW IF EXISTS v; CREATE VIEW v AS SELECT 1 AS n;")
    (sql / "02_second.sql").write_text("DROP VIEW IF EXISTS v; CREATE VIEW v AS SELECT 2 AS n;")
    (sql / "notes.txt").write_text("this is not sql")
    _write([_line()], tmp_path / "o.csv")
    monkeypatch.setattr(run, "SQL_DIR", sql)
    assert run.build_db(tmp_path / "o.csv").execute("SELECT n FROM v").fetchall() == [(2,)]


def test_plant_null_key_skips_zero_price_lines(tmp_path):
    src, dst = tmp_path / "s.csv", tmp_path / "d.csv"
    _write([_line(InvoiceNo="Z1", UnitPrice="0.0"), _line(InvoiceNo="K1")], src)
    selftest.plant_null_key(src, dst)
    with open(dst, newline="") as fh:
        got = {r["InvoiceNo"]: r["StockCode"] for r in csv.DictReader(fh)}
    assert got == {"Z1": "S1", "K1": ""}


def _bad_cell(tmp_path, column, value):
    """Two good lines, then one line whose cell is bad: spreadsheet row 4 (header is row 1)."""
    path = tmp_path / "bad.csv"
    _write([_line(InvoiceNo="A1"), _line(InvoiceNo="A2"), _line(**{column: value})], path)
    with pytest.raises(ValueError) as caught:
        run.build_db(path)
    assert type(caught.value) is ValueError
    return str(caught.value)


@pytest.mark.parametrize("column,value", [
    ("Quantity", ""), ("Quantity", "1.5"), ("Quantity", "2.0"), ("Quantity", "abc"),
    ("UnitPrice", ""), ("UnitPrice", "abc"), ("UnitPrice", "nan"), ("UnitPrice", "inf"),
    ("InvoiceDate", ""), ("InvoiceDate", "2011-1-5 10:00:00"), ("InvoiceDate", "2011-01-05"),
    ("InvoiceDate", "2011-13-05 10:00:00"), ("InvoiceDate", "2011-01-05T10:00:00")])
def test_loader_names_the_row_and_column_of_a_bad_cell(tmp_path, column, value):
    msg = _bad_cell(tmp_path, column, value)
    assert msg.startswith(f"row 4, column {column}: ") and repr(value) in msg


def test_loader_names_the_line_of_a_file_that_is_not_utf_8(tmp_path):
    path = tmp_path / "latin.csv"
    _write([_line(InvoiceNo="A1"), _line(InvoiceNo="A2", Description="y")], path)
    raw = path.read_bytes().replace(b",y,", b",caf\xe9,")
    path.write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        run.build_db(path)
    assert type(caught.value) is ValueError
    assert "not valid UTF-8" in str(caught.value) and "line 3" in str(caught.value)


def test_orphan_rows_check_alone_turns_the_gate_red(tmp_path, monkeypatch, capsys):
    # The null-key line is allowed through its own check, so only orphan_rows can stop the run.
    baseline = tmp_path / "baseline.json"
    allowed = dict(run.load_baseline(), null_join_key=1)
    baseline.write_text(json.dumps(allowed))
    monkeypatch.setattr(run, "BASELINE", baseline)
    bad, out = tmp_path / "null.csv", tmp_path / "m.json"
    selftest.plant_null_key(run.CSV, bad)
    assert run.main(["--csv", str(bad), "--out", str(out)]) == 1
    assert "RED orphan_rows (integrity): observed 1, allowed 0" in capsys.readouterr().out
    assert not out.exists()


def test_selftest_counts_any_exit_other_than_one_as_missed(monkeypatch, capsys):
    _stub_run(monkeypatch, (2, 2))
    assert selftest.main() == 1
    assert capsys.readouterr().out.count("MISSED") == 2


def test_selftest_control_run_must_exit_zero_not_just_non_one(monkeypatch, capsys):
    def fake(argv):
        return 2 if argv[argv.index("--csv") + 1] == str(run.CSV) else 1
    monkeypatch.setattr(run, "main", fake)
    assert selftest.main() == 1
    out = capsys.readouterr().out
    assert "clean sample stays green: NO" in out and out.endswith("SELFTEST FAIL\n")


def test_plant_duplicate_copies_a_sold_line_not_a_return(tmp_path):
    src, dst = tmp_path / "s.csv", tmp_path / "d.csv"
    _write([_line(InvoiceNo="C1", Quantity="-3"), _line(InvoiceNo="S1", Quantity="2")], src)
    selftest.plant_duplicate(src, dst)
    with open(dst, newline="") as fh:
        got = [(r["InvoiceNo"], r["Quantity"]) for r in csv.DictReader(fh)]
    assert got == [("C1", "-3"), ("S1", "2"), ("S1", "2")]


def test_every_text_io_call_in_pipeline_names_its_encoding():
    import ast
    root = Path(run.__file__).resolve().parent
    seen = 0
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("read_text", "write_text") or (name == "open" and not (
                    len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and "b" in str(node.args[1].value))):
                seen += 1
                assert any(k.arg == "encoding" for k in node.keywords), (
                    f"{path.name}:{node.lineno} {name}() has no encoding")
    assert seen >= 7
