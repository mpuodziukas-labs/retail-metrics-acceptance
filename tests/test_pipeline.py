import csv
import json

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


def test_selftest_catches_both_faults_and_keeps_control_green():
    assert selftest.main() == 0


def test_metrics_json_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    run.main(["--csv", str(run.CSV), "--out", str(a)])
    run.main(["--csv", str(run.CSV), "--out", str(b)])
    assert a.read_bytes() == b.read_bytes()


def test_vendored_sample_checksum_matches_source_note():
    import hashlib
    sha = hashlib.sha256(run.CSV.read_bytes()).hexdigest()
    assert sha in (run.ROOT / "data" / "SOURCE.md").read_text()
