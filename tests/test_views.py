"""View-level tests on a tiny hand-worked fixture. Every expected value below is
worked by hand from the fixture rows, never read back from the SQL under test."""
import csv

import pytest

from pipeline import run

HEADER = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
          "UnitPrice", "CustomerID", "Country"]
FIXTURE = [
    # c1 has two countries: earliest invoice says France, later ones say Spain.
    ["A1", "S1", "Alpha", 2, "2011-01-05 10:00:00", 5.0, "c1", "France"],
    ["A2", "S1", "Alpha", 1, "2011-02-10 10:00:00", 5.0, "c1", "Spain"],
    ["A3", "S2", "Beta", 3, "2011-02-11 09:00:00", 2.0, "c1", "Spain"],
    # c2: an invoice number starting with C but a positive quantity, then a normal one.
    ["C1", "S1", "Alpha", 1, "2011-02-12 10:00:00", 5.0, "c2", "Germany"],
    ["A4", "S2", "Beta", 1, "2011-02-13 10:00:00", 2.0, "c2", "Germany"],
    # Guest lines (no customer id) in Italy: two products tie on revenue 10.
    ["A5", "S3", "Gamma", 1, "2011-03-01 10:00:00", 10.0, "", "Italy"],
    ["A6", "S4", "Delta", 2, "2011-03-02 10:00:00", 5.0, "", "Italy"],
    ["A8", "S5", "Epsilon", 1, "2011-03-03 10:00:00", 3.0, "", "Italy"],
    # One cancelled invoice carrying two lines.
    ["C2", "S1", "Alpha", -1, "2011-03-05 10:00:00", 5.0, "", "Spain"],
    ["C2", "S2", "Beta", -1, "2011-03-05 10:00:00", 2.0, "", "Spain"],
]


# c3: invoice-number order is the reverse of date order (B9 is first by date, last by number).
REVERSED = [
    ["B9", "S1", "Alpha", 1, "2011-04-01 10:00:00", 4.0, "c3", "France"],
    ["B2", "S1", "Alpha", 2, "2011-04-02 10:00:00", 3.0, "c3", "France"],
    ["B5", "S2", "Beta", 1, "2011-04-03 10:00:00", 10.0, "c3", "France"],
]


def _build(tmp_path_factory, name, fixture_rows):
    path = tmp_path_factory.mktemp(name) / f"{name}.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(fixture_rows)
    return run.build_db(path)


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return _build(tmp_path_factory, "tiny", FIXTURE)


@pytest.fixture(scope="module")
def reversed_numbers(tmp_path_factory):
    return _build(tmp_path_factory, "reversed", REVERSED)


def q(conn, sql):
    return conn.execute(sql).fetchall()


def test_dim_customers_uses_earliest_invoice_country(tiny):
    got = q(tiny, "SELECT CustomerID, primary_country FROM dim_customers ORDER BY CustomerID")
    assert got == [("c1", "France"), ("c2", "Germany")]


def test_clean_orders_uses_canonical_customer_country(tiny):
    got = q(tiny, "SELECT InvoiceNo, country FROM clean_orders "
                  "WHERE CustomerID = 'c1' ORDER BY InvoiceNo")
    assert got == [("A1", "France"), ("A2", "France"), ("A3", "France")]


def test_clean_orders_guest_keeps_raw_country(tiny):
    got = q(tiny, "SELECT DISTINCT country FROM clean_orders WHERE CustomerID IS NULL")
    assert got == [("Italy",)]


def test_clean_orders_cancellation_flag(tiny):
    got = q(tiny, "SELECT InvoiceNo, is_cancellation FROM clean_orders "
                  "WHERE CustomerID = 'c2' ORDER BY InvoiceNo")
    assert got == [("A4", 0), ("C1", 1)]


def test_customer_windows_sequence_and_running_total(tiny):
    got = q(tiny, "SELECT InvoiceNo, order_seq, running_customer_revenue "
                  "FROM customer_order_windows WHERE CustomerID = 'c1' ORDER BY order_seq")
    assert got == [("A1", 1, 10.0), ("A2", 2, 15.0), ("A3", 3, 21.0)]


def test_running_revenue_follows_date_not_invoice_number(reversed_numbers):
    # By date: B9 = 1*4 = 4, then B2 = 2*3 = 6 (running 10), then B5 = 1*10 = 10 (running 20).
    got = q(reversed_numbers,
            "SELECT InvoiceNo, order_seq, running_customer_revenue "
            "FROM customer_order_windows WHERE CustomerID = 'c3' ORDER BY InvoiceDate")
    assert got == [("B9", 1, 4.0), ("B2", 2, 10.0), ("B5", 3, 20.0)]


def test_top_products_tie_break_rank_and_units(tiny):
    got = q(tiny, "SELECT stock_code, revenue, units, revenue_rank, row_num "
                  "FROM product_country_revenue WHERE country = 'Italy' ORDER BY row_num")
    assert got == [("S3", 10.0, 1, 1, 1), ("S4", 10.0, 2, 1, 2), ("S5", 3.0, 1, 3, 3)]


def test_monthly_cancellation_counts_invoices_not_lines(tiny):
    got = q(tiny, "SELECT invoices, cancelled_invoices, cancellation_rate_pct "
                  "FROM monthly_cancellation WHERE month = '2011-03'")
    assert got == [(4, 1, 25.0)]
