"""Load the CSV into sqlite, apply the SQL views, run the quality gate, emit metrics."""
import argparse
import csv
import io
import json
import math
import re
import sqlite3
import sys
from datetime import datetime
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


COLUMNS = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice",
           "CustomerID", "Country"]
DATE_FORMAT =re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _int(raw):
    return int(raw)


def _price(raw):
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(raw)
    return value


def _date(raw):
    if not DATE_FORMAT.fullmatch(raw):
        raise ValueError(raw)
    datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return raw


def _cell(row_no, column, raw, parse):
    try:
        if not raw.isascii():
            raise ValueError(raw)
        return parse(raw)
    except ValueError:
        raise ValueError(f"row {row_no}, column {column}: {raw!r} is not a valid value") from None


def _read_rows(csv_path):
    data = Path(csv_path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        line = data[:err.start].count(b"\n") + 1
        raise ValueError(f"{csv_path}: not valid UTF-8 at line {line}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    names = reader.fieldnames
    if names is not None:
        for name in names:
            if names.count(name) > 1:
                raise ValueError(f"{csv_path}: duplicate column {name!r}")
        missing = [c for c in COLUMNS if c not in names]
        if missing:
            raise ValueError(f"{csv_path}: missing column(s): {', '.join(missing)}")
    rows = []
    for row_no, r in enumerate(reader, start=2):
        if None in r or None in r.values():
            found = len(names) + len(r[None]) if None in r else sum(v is not None for v in r.values())
            raise ValueError(f"{csv_path}: row {row_no}: expected {len(names)} fields, found {found}")
        rows.append((r["InvoiceNo"], r["StockCode"] or None, r["Description"] or None,
                     _cell(row_no, "Quantity", r["Quantity"], _int),
                     _cell(row_no, "InvoiceDate", r["InvoiceDate"], _date),
                     _cell(row_no, "UnitPrice", r["UnitPrice"], _price),
                     r["CustomerID"] or None, r["Country"]))
    return rows


def build_db(csv_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_orders (InvoiceNo TEXT, StockCode TEXT, Description TEXT, "
        "Quantity INTEGER, InvoiceDate TEXT, UnitPrice REAL, CustomerID TEXT, Country TEXT)")
    conn.executemany("INSERT INTO raw_orders VALUES (?,?,?,?,?,?,?,?)", _read_rows(csv_path))
    for f in sorted(SQL_DIR.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    return conn


def load_baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


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
    if not conn.execute("SELECT COUNT(*) FROM clean_orders").fetchone()[0]:
        print("no clean order lines; nothing to report")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(compute_metrics(conn, checks)), encoding="utf-8")
    print(f"GATE GREEN: wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
