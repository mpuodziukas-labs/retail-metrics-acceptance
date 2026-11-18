"""Load the CSV into sqlite, apply the SQL views, run the quality gate, emit metrics."""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "online_retail_sample.csv"
BASELINE = ROOT / "data" / "quality_baseline.json"
SQL_DIR = ROOT / "sql"

CHECKS = [
    ("duplicate_order_lines", "uniqueness",
     "SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) AS n FROM raw_orders "
     "GROUP BY InvoiceNo, StockCode, InvoiceDate, Quantity, UnitPrice, CustomerID "
     "HAVING COUNT(*) > 1)"),
    ("null_join_key", "completeness",
     "SELECT COUNT(*) FROM raw_orders "
     "WHERE StockCode IS NULL AND Quantity > 0 AND UnitPrice > 0"),
    ("negative_price", "validity",
     "SELECT COUNT(*) FROM raw_orders WHERE UnitPrice < 0"),
    ("orphan_rows", "integrity",
     "SELECT (SELECT COUNT(*) FROM raw_orders WHERE Quantity > 0 AND UnitPrice > 0) "
     "- (SELECT COUNT(*) FROM clean_orders)"),
]


def build_db(csv_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_orders (InvoiceNo TEXT, StockCode TEXT, Description TEXT, "
        "Quantity INTEGER, InvoiceDate TEXT, UnitPrice REAL, CustomerID TEXT, Country TEXT)")
    with open(csv_path, newline="") as fh:
        rows = [(r["InvoiceNo"], r["StockCode"] or None, r["Description"] or None,
                 int(r["Quantity"]), r["InvoiceDate"], float(r["UnitPrice"]),
                 r["CustomerID"] or None, r["Country"]) for r in csv.DictReader(fh)]
    conn.executemany("INSERT INTO raw_orders VALUES (?,?,?,?,?,?,?,?)", rows)
    for f in sorted(SQL_DIR.glob("*.sql")):
        conn.executescript(f.read_text())
    return conn


def load_baseline():
    return json.loads(BASELINE.read_text())


def run_quality(conn, baseline):
    out = []
    for name, dim, sql in CHECKS:
        observed = conn.execute(sql).fetchone()[0]
        out.append({"name": name, "dimension": dim, "observed": observed,
                    "allowed": baseline[name], "ok": observed <= baseline[name]})
    return out


def _rows(conn, sql):
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def compute_metrics(conn, quality):
    return {
        "headline": _rows(conn, "SELECT * FROM headline_metrics")[0],
        "monthly": _rows(
            conn,
            "SELECT r.month, r.revenue, r.orders, r.aov, r.prev_revenue, r.mom_change_pct, "
            "r.running_total, c.cancellation_rate_pct "
            "FROM monthly_revenue r JOIN monthly_cancellation c ON r.month = c.month "
            "ORDER BY r.month"),
        "top_products": _rows(
            conn,
            "SELECT p.country, p.row_num AS rank, p.stock_code, p.description, p.revenue "
            "FROM product_country_revenue p "
            "JOIN (SELECT country, SUM(revenue) AS total FROM product_country_revenue "
            "      GROUP BY country ORDER BY total DESC, country LIMIT 3) t "
            "  ON p.country = t.country "
            "WHERE p.row_num <= 3 ORDER BY t.total DESC, p.country, p.row_num"),
        "cohorts": _rows(
            conn, "SELECT * FROM cohort_retention ORDER BY cohort_month, month_index"),
        "quality": quality,
    }


def dump(metrics):
    return json.dumps(metrics, sort_keys=True, indent=2) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(CSV))
    ap.add_argument("--out", default=str(ROOT / "out" / "metrics.json"))
    ap.add_argument("--no-quality", action="store_true")
    args = ap.parse_args(argv)
    conn = build_db(args.csv)
    checks = [] if args.no_quality else run_quality(conn, load_baseline())
    for c in checks:
        print(f"{'ok ' if c['ok'] else 'RED'} {c['name']} ({c['dimension']}): "
              f"observed {c['observed']}, allowed {c['allowed']}")
    if not all(c["ok"] for c in checks):
        print("GATE RED: no metrics written")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(compute_metrics(conn, checks)))
    print(f"GATE GREEN: wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
