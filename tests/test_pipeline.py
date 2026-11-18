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
    with open(path, "w", newline="", encoding="utf-8") as fh:
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
    assert json.loads(out.read_text(encoding="utf-8"))["headline"]["orders"] > 0


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
    m = json.loads(mutant.read_text(encoding="utf-8"))
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
    assert sha in (run.ROOT / "data" / "SOURCE.md").read_text(encoding="utf-8")


def test_selftest_fails_when_gate_misses_a_planted_fault(monkeypatch, capsys):
    def blind_gate(argv):
        Path(argv[argv.index("--out") + 1]).write_text("{}", encoding="utf-8")
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
            Path(argv[argv.index("--out") + 1]).write_text("{}", encoding="utf-8")
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
    with open(dst, newline="", encoding="utf-8") as fh:
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
    (sql / "01_first.sql").write_text("DROP VIEW IF EXISTS v; CREATE VIEW v AS SELECT 1 AS n;", encoding="utf-8")
    (sql / "02_second.sql").write_text("DROP VIEW IF EXISTS v; CREATE VIEW v AS SELECT 2 AS n;", encoding="utf-8")
    (sql / "notes.txt").write_text("this is not sql", encoding="utf-8")
    _write([_line()], tmp_path / "o.csv")
    monkeypatch.setattr(run, "SQL_DIR", sql)
    assert run.build_db(tmp_path / "o.csv").execute("SELECT n FROM v").fetchall() == [(2,)]


def test_plant_null_key_skips_zero_price_lines(tmp_path):
    src, dst = tmp_path / "s.csv", tmp_path / "d.csv"
    _write([_line(InvoiceNo="Z1", UnitPrice="0.0"), _line(InvoiceNo="K1")], src)
    selftest.plant_null_key(src, dst)
    with open(dst, newline="", encoding="utf-8") as fh:
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
    baseline.write_text(json.dumps(allowed), encoding="utf-8")
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
    with open(dst, newline="", encoding="utf-8") as fh:
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


# ---- strict loader pins -------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "2011-01-05 10:00:00 ", " 2011-01-05 10:00:00", "2011-01-05 10:00:00\t",
    "2011-1-05 10:00:00", "2011-01-5 10:00:00", "2011-01-05 1:00:00", "2011-01-05 10:0:00",
    "2011-01-05 10:00:0", "11-01-05 10:00:00", "211-01-05 10:00:00"])
def test_loader_rejects_a_date_that_is_not_exactly_yyyy_mm_dd_hh_mm_ss(tmp_path, value):
    assert _bad_cell(tmp_path, "InvoiceDate", value) == (
        f"row 4, column InvoiceDate: {value!r} is not a valid value")


@pytest.mark.parametrize("value", [
    "inf", "-inf", "+inf", "Inf", "-Inf", "INF", "-INF", "infinity", "-Infinity", "INFINITY",
    "nan", "-nan", "+nan", "NaN", "NAN"])
def test_loader_rejects_a_non_finite_price_in_any_letter_case(tmp_path, value):
    assert _bad_cell(tmp_path, "UnitPrice", value) == (
        f"row 4, column UnitPrice: {value!r} is not a valid value")


@pytest.mark.parametrize("column,value", [("Quantity", "abc"), ("UnitPrice", "abc"),
                                          ("InvoiceDate", "yesterday")])
def test_bad_cell_message_is_exactly_row_column_value_is_not_a_valid_value(
        tmp_path, column, value):
    assert _bad_cell(tmp_path, column, value) == (
        f"row 4, column {column}: '{value}' is not a valid value")


def _load_error(path):
    with pytest.raises(ValueError) as caught:
        run.build_db(path)
    return caught.value


@pytest.mark.parametrize("column,value", [("Quantity", "abc"), ("UnitPrice", "inf"),
                                          ("InvoiceDate", "2011-01-05")])
def test_bad_cell_error_carries_no_exception_chain(tmp_path, column, value):
    path = tmp_path / "bad.csv"
    _write([_line(**{column: value})], path)
    err = _load_error(path)
    assert err.__cause__ is None and err.__suppress_context__ is True


_CSV_HEAD = b"InvoiceNo,StockCode,Description,Quantity,InvoiceDate,UnitPrice,CustomerID,Country"
_CSV_GOOD = b"A1,S1,x,2,2011-01-05 10:00:00,2.0,c1,France"
_CSV_LATIN = b"A2,S2,caf\xe9,2,2011-01-05 10:00:00,2.0,c1,France"


def _non_utf8_file(tmp_path, terminator):
    path = tmp_path / "latin.csv"
    path.write_bytes(terminator.join([_CSV_HEAD, _CSV_GOOD, _CSV_LATIN]) + terminator)
    return path


@pytest.mark.parametrize("terminator,line", [(b"\n", 3), (b"\r\n", 3), (b"\r", 1)])
def test_non_utf_8_error_counts_lines_by_line_feed(tmp_path, terminator, line):
    # A CR-only file has no LF, so the whole file is "line 1"; that is the pinned rule.
    path = _non_utf8_file(tmp_path, terminator)
    assert str(_load_error(path)) == f"{path}: not valid UTF-8 at line {line}"


def test_non_utf_8_error_in_the_header_names_line_1(tmp_path):
    path = tmp_path / "head.csv"
    path.write_bytes(_CSV_HEAD.replace(b"Country", b"Pa\xffs") + b"\n" + _CSV_GOOD + b"\n")
    assert str(_load_error(path)) == f"{path}: not valid UTF-8 at line 1"


def test_non_utf_8_error_carries_no_exception_chain(tmp_path):
    err = _load_error(_non_utf8_file(tmp_path, b"\n"))
    assert err.__cause__ is None and err.__suppress_context__ is True


@pytest.mark.parametrize("terminator,quoted", [
    (b"\n", b"two\nlines"), (b"\r\n", b"two\r\nlines"), (b"\n", b"two\rlines")],
    ids=["lf-in-lf-file", "crlf-in-crlf-file", "lone-cr-in-lf-file"])
def test_a_quoted_newline_in_a_description_loads_as_one_row(tmp_path, terminator, quoted):
    path = tmp_path / "q.csv"
    second = b'A2,S2,"' + quoted + b'",3,2011-01-06 10:00:00,1.5,c2,France'
    path.write_bytes(terminator.join([_CSV_HEAD, _CSV_GOOD, second]) + terminator)
    got = run.build_db(path).execute(
        "SELECT InvoiceNo, Description FROM raw_orders ORDER BY InvoiceNo").fetchall()
    assert got == [("A1", "x"), ("A2", quoted.decode())]


def test_a_file_with_carriage_return_only_line_ends_loads_every_row(tmp_path):
    path = tmp_path / "cr.csv"
    path.write_bytes(b"\r".join([_CSV_HEAD, _CSV_GOOD, _CSV_GOOD.replace(b"A1", b"A2")]) + b"\r")
    conn = run.build_db(path)
    assert conn.execute("SELECT InvoiceNo FROM raw_orders ORDER BY 1").fetchall() == [
        ("A1",), ("A2",)]


def test_a_blank_invoice_number_loads_as_empty_text_not_null(tmp_path):
    path = tmp_path / "blank.csv"
    _write([_line(InvoiceNo="")], path)
    conn = run.build_db(path)
    assert conn.execute("SELECT typeof(InvoiceNo), InvoiceNo FROM raw_orders").fetchall() == [
        ("text", "")]


# ---- empty-result refusal -----------------------------------------------------------------

def _main_on(tmp_path, lines, capsys):
    path, out = tmp_path / "e.csv", tmp_path / "m.json"
    _write(lines, path)
    code = run.main(["--csv", str(path), "--out", str(out)])
    return code, capsys.readouterr().out, out


def test_no_clean_lines_prints_the_exact_refusal_and_writes_nothing(tmp_path, capsys):
    code, text, out = _main_on(tmp_path, [_line(InvoiceNo="C1", Quantity="-2")], capsys)
    assert code == 1 and not out.exists()
    assert text.endswith("ok  orphan_rows (integrity): observed 0, allowed 0\n"
                         "no clean order lines; nothing to report\n")


def test_a_failed_gate_is_reported_before_the_no_clean_lines_refusal(tmp_path, capsys):
    # One negative-price line: the gate is RED and clean_orders is empty. The gate wins.
    code, text, out = _main_on(tmp_path, [_line(UnitPrice="-1.0")], capsys)
    assert code == 1 and not out.exists()
    assert "RED negative_price (validity): observed 1, allowed 0\n" in text
    assert text.endswith("GATE RED: no metrics written\n")
    assert "no clean order lines" not in text


def test_a_file_with_exactly_one_clean_line_is_not_refused(tmp_path, capsys):
    code, text, out = _main_on(tmp_path, [_line()], capsys)
    assert code == 0, text
    assert text.endswith("GATE GREEN: wrote m.json\n")
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["headline"]["orders"] == 1 and len(m["monthly"]) == 1


# ---- the oracle fixture reads the CSV as UTF-8 --------------------------------------------

def test_oracle_rows_are_read_as_utf_8(rows, conn):
    seen = {r["Description"] for r in rows if r["Description"]}
    loaded = {d for (d,) in conn.execute(
        "SELECT DISTINCT Description FROM raw_orders WHERE Description IS NOT NULL")}
    assert seen == loaded
    assert any("£" in d for d in seen)


# ---- loader defects found by probes: ASCII-only cells, field count, header, BOM -----------

@pytest.mark.parametrize("column,value", [
    ("Quantity", "٥"), ("Quantity", "５"), ("UnitPrice", "٢.٥"),
    ("UnitPrice", "２.5"), ("InvoiceDate", "٢٠١١-01-05 10:00:00"),
    ("InvoiceDate", "2011-01-0٥ 10:00:00")])
def test_loader_rejects_non_ascii_digits_in_a_numeric_or_date_cell(tmp_path, column, value):
    assert _bad_cell(tmp_path, column, value) == (
        f"row 4, column {column}: {value!r} is not a valid value")


def _file_with(tmp_path, head, *body):
    path = tmp_path / "f.csv"
    path.write_bytes(b"\n".join([head, *body]) + b"\n")
    return path


def test_a_row_with_more_fields_than_the_header_names_the_file_and_row(tmp_path):
    # An unquoted decimal comma in the price shifts every later column.
    bad = b"A3,S3,x,2,2011-01-05 10:00:00,1,5,c1,France"
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, _CSV_GOOD.replace(b"A1", b"A2"), bad)
    err = _load_error(path)
    assert str(err) == f"{path}: row 4: expected 8 fields, found 9"
    assert type(err) is ValueError


def test_a_row_with_fewer_fields_than_the_header_names_the_file_and_row(tmp_path):
    bad = b"A3,S3,x,2,2011-01-05 10:00:00,1.0,c1"
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, _CSV_GOOD.replace(b"A1", b"A2"), bad)
    err = _load_error(path)
    assert str(err) == f"{path}: row 4: expected 8 fields, found 7"
    assert type(err) is ValueError


def test_a_duplicate_column_name_is_rejected(tmp_path):
    head = _CSV_HEAD.replace(b"Country", b"Quantity")
    path = _file_with(tmp_path, head, _CSV_GOOD)
    err = _load_error(path)
    assert str(err) == f"{path}: duplicate column 'Quantity'"
    assert type(err) is ValueError


def test_a_missing_required_column_is_named(tmp_path):
    head = _CSV_HEAD.replace(b"Country", b"Ctry").replace(b"UnitPrice", b"Price")
    path = _file_with(tmp_path, head, _CSV_GOOD)
    err = _load_error(path)
    assert str(err) == f"{path}: missing column(s): UnitPrice, Country"
    assert type(err) is ValueError


def test_an_extra_unknown_column_is_accepted_and_ignored(tmp_path):
    plain = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD)
    extra = tmp_path / "extra.csv"
    extra.write_bytes(_CSV_HEAD + b",Extra\n" + _CSV_GOOD + b",zzz\n")
    sql = "SELECT * FROM raw_orders ORDER BY InvoiceNo"
    assert run.build_db(extra).execute(sql).fetchall() == run.build_db(plain).execute(
        sql).fetchall()


_REQUIRED = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice",
             "CustomerID", "Country"]


def _head_without(*names):
    return ",".join(f"Other{i}" if c in names else c for i, c in enumerate(_REQUIRED)).encode()


@pytest.mark.parametrize("column", _REQUIRED)
def test_a_header_missing_exactly_one_required_column_names_it(tmp_path, column):
    path = _file_with(tmp_path, _head_without(column), _CSV_GOOD)
    err = _load_error(path)
    assert str(err) == f"{path}: missing column(s): {column}"
    assert type(err) is ValueError


def test_missing_columns_are_named_in_header_order_not_alphabetically(tmp_path):
    path = _file_with(tmp_path, _head_without("CustomerID", "Quantity"), _CSV_GOOD)
    assert str(_load_error(path)) == f"{path}: missing column(s): Quantity, CustomerID"


def test_the_field_count_message_uses_the_header_width_not_a_fixed_eight(tmp_path):
    head = _CSV_HEAD + b",Extra"
    short = _file_with(tmp_path, head, _CSV_GOOD + b",z", _CSV_GOOD.replace(b"A1", b"A2"))
    assert str(_load_error(short)) == f"{short}: row 3: expected 9 fields, found 8"
    long_ = _file_with(tmp_path, head, _CSV_GOOD + b",z,q,r")
    assert str(_load_error(long_)) == f"{long_}: row 2: expected 9 fields, found 11"


@pytest.mark.parametrize("bad,found", [
    (b"A3,,,2,2011-01-05 10:00:00,1.0", 6),
    (b"A3,S3,x,2,,,", 7),
    (b",,,,,,", 7),
    (b"A3", 1)])
def test_a_short_row_counts_blank_cells_as_fields(tmp_path, bad, found):
    path = _file_with(tmp_path, _CSV_HEAD, bad)
    assert str(_load_error(path)) == f"{path}: row 2: expected 8 fields, found {found}"


def test_a_long_row_counts_blank_cells_as_fields(tmp_path):
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD + b",,,")
    assert str(_load_error(path)) == f"{path}: row 2: expected 8 fields, found 11"


@pytest.mark.parametrize("good_before", [0, 1, 3])
@pytest.mark.parametrize("bad,found", [
    (b"A9,S9,x,2,2011-01-05 10:00:00,1.0,c1,France,extra", 9),
    (b"A9,S9,x,2,2011-01-05 10:00:00,1.0,c1", 7)])
def test_the_field_count_row_number_is_the_file_row_wherever_it_sits(tmp_path, good_before, bad,
                                                                       found):
    goods = [_CSV_GOOD.replace(b"A1", b"G%d" % i) for i in range(good_before)]
    path = _file_with(tmp_path, _CSV_HEAD, *goods, bad)
    assert str(_load_error(path)) == (
        f"{path}: row {good_before + 2}: expected 8 fields, found {found}")


def test_a_duplicated_unknown_extra_column_is_rejected(tmp_path):
    path = _file_with(tmp_path, _CSV_HEAD + b",Extra,Extra", _CSV_GOOD + b",1,2")
    err = _load_error(path)
    assert str(err) == f"{path}: duplicate column 'Extra'"
    assert type(err) is ValueError


@pytest.mark.parametrize("good_before", [0, 2])
def test_a_bad_cell_after_a_byte_order_mark_names_its_file_row(tmp_path, good_before):
    goods = [_CSV_GOOD.replace(b"A1", b"G%d" % i) for i in range(good_before)]
    bad = _CSV_GOOD.replace(b",2,", b",two,")
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + b"\n".join([_CSV_HEAD, *goods, bad]) + b"\n")
    assert str(_load_error(path)) == (
        f"row {good_before + 2}, column Quantity: 'two' is not a valid value")


def test_only_a_byte_order_mark_at_the_start_of_the_file_is_stripped(tmp_path):
    path = tmp_path / "mid.csv"
    path.write_bytes(b"\xef\xbb\xbf" + b"\n".join(
        [_CSV_HEAD, _CSV_GOOD, b"\xef\xbb\xbf" + _CSV_GOOD.replace(b"A1", b"A2")]) + b"\n")
    ids = [i for (i,) in run.build_db(path).execute(
        "SELECT InvoiceNo FROM raw_orders ORDER BY rowid")]
    assert ids == ["A1", "﻿A2"]


def test_a_utf_8_byte_order_mark_is_stripped_and_the_metrics_are_unchanged(tmp_path):
    bom = tmp_path / "bom.csv"
    bom.write_bytes(b"\xef\xbb\xbf" + run.CSV.read_bytes())
    plain_out, bom_out = tmp_path / "plain.json", tmp_path / "bom.json"
    assert run.main(["--csv", str(run.CSV), "--out", str(plain_out)]) == 0
    assert run.main(["--csv", str(bom), "--out", str(bom_out)]) == 0
    assert bom_out.read_bytes() == plain_out.read_bytes()


def test_a_negative_zero_price_is_a_zero_price_not_a_negative_one(tmp_path):
    checks = _checks(tmp_path, [_line(UnitPrice="-0.0"), _line(InvoiceNo="D2")])
    assert checks["negative_price"]["observed"] == 0
    assert checks["orphan_rows"]["observed"] == 0
    conn = run.build_db(tmp_path / "q.csv")
    assert conn.execute("SELECT InvoiceNo FROM clean_orders").fetchall() == [("D2",)]


# ---- loader defects: field limit, 64-bit Quantity, Python-only numerals, physical lines ----

def test_a_200000_character_description_loads_and_leaves_the_csv_field_limit_alone(tmp_path):
    before = csv.field_size_limit()
    path = tmp_path / "big.csv"
    _write([_line(Description="x" * 200_000)], path)
    conn = run.build_db(path)
    assert conn.execute("SELECT LENGTH(Description) FROM raw_orders").fetchone() == (200_000,)
    assert csv.field_size_limit() == before


def test_the_csv_field_limit_is_restored_when_a_load_with_a_big_cell_fails(tmp_path):
    before = csv.field_size_limit()
    path = tmp_path / "bigbad.csv"
    _write([_line(Description="x" * 200_000), _line(Quantity="abc")], path)
    assert str(_load_error(path)) == "row 3, column Quantity: 'abc' is not a valid value"
    assert csv.field_size_limit() == before


@pytest.mark.parametrize("value", [str(2**63), "99999999999999999999", str(-2**63 - 1)])
def test_quantity_outside_signed_64_bit_is_refused_by_name(tmp_path, value):
    assert _bad_cell(tmp_path, "Quantity", value) == (
        f"row 4, column Quantity: {value!r} is not a valid value")


@pytest.mark.parametrize("value", [str(2**63 - 1), str(-2**63)])
def test_quantity_at_the_signed_64_bit_boundary_loads_exactly(tmp_path, value):
    path = tmp_path / "edge.csv"
    _write([_line(Quantity=value)], path)
    got = run.build_db(path).execute("SELECT Quantity FROM raw_orders").fetchall()
    assert got == [(int(value),)]


PYTHON_ONLY_NUMERALS = ["1_0", "+5", " 5", "5 ", "\t5", "5\t", "1e3", "0x10", "inf", "nan"]


@pytest.mark.parametrize("column", ["Quantity", "UnitPrice"])
@pytest.mark.parametrize("value", PYTHON_ONLY_NUMERALS)
def test_loader_refuses_numerals_that_only_python_parses(tmp_path, column, value):
    assert _bad_cell(tmp_path, column, value) == (
        f"row 4, column {column}: {value!r} is not a valid value")


def test_a_blank_line_does_not_shift_the_reported_row_number(tmp_path):
    bad = b"A3,S3,x,abc,2011-01-05 10:00:00,1.0,c1,France"
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, b"", bad)
    assert str(_load_error(path)) == "row 4, column Quantity: 'abc' is not a valid value"


def test_a_blank_line_does_not_shift_the_row_number_of_a_field_count_error(tmp_path):
    bad = b"A3,S3,x,2,2011-01-05 10:00:00,1,5,c1,France"
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, b"", bad)
    assert str(_load_error(path)) == f"{path}: row 4: expected 8 fields, found 9"


def test_a_multi_line_record_reports_the_line_on_which_it_ends(tmp_path):
    bad = b'A3,S3,"two\nlines",abc,2011-01-05 10:00:00,1.0,c1,France'
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, bad)
    assert str(_load_error(path)) == "row 4, column Quantity: 'abc' is not a valid value"


def test_blank_lines_are_still_skipped_and_the_rows_load(tmp_path):
    path = _file_with(tmp_path, _CSV_HEAD, _CSV_GOOD, b"", _CSV_GOOD.replace(b"A1", b"A2"))
    assert run.build_db(path).execute("SELECT COUNT(*) FROM raw_orders").fetchone() == (2,)
