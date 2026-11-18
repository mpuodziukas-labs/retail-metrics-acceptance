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
    with open(path, "w", newline="", encoding="utf-8") as fh:
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


def _row(inv, stock, desc, qty, date, price, cust, country):
    return [inv, stock, desc, qty, date, price, cust, country]


def test_dim_products_has_no_row_for_null_stock_code(tmp_path_factory):
    db = _build(tmp_path_factory, "nokey", [
        _row("N1", "", "Ghost", 1, "2011-01-05 10:00:00", 3.0, "c1", "France"),
        _row("N2", "S1", "Alpha", 1, "2011-01-06 10:00:00", 3.0, "c1", "France")])
    assert q(db, "SELECT StockCode, canonical_description FROM dim_products") == [("S1", "Alpha")]


@pytest.mark.parametrize("descs", [("Zed", "Alpha"), ("Alpha", "Zed")])
def test_dim_products_picks_alphabetically_first_description(tmp_path_factory, descs):
    db = _build(tmp_path_factory, "desc", [
        _row(f"D{i}", "S1", d, 1, "2011-01-05 10:00:00", 2.0, "c1", "France")
        for i, d in enumerate(descs)])
    assert q(db, "SELECT StockCode, canonical_description FROM dim_products") == [("S1", "Alpha")]


@pytest.fixture
def c_inside(tmp_path_factory):
    # A5C1 has a C that is not its first character; C7 is a real cancellation prefix.
    return _build(tmp_path_factory, "cinside", [
        _row("A5C1", "S1", "Alpha", 1, "2011-05-01 10:00:00", 5.0, "c1", "France"),
        _row("C7", "S1", "Alpha", 1, "2011-05-02 10:00:00", 5.0, "c1", "France")])


def test_c_not_first_character_is_not_a_cancellation_flag(c_inside):
    got = q(c_inside, "SELECT InvoiceNo, is_cancellation FROM clean_orders ORDER BY InvoiceNo")
    assert got == [("A5C1", 0), ("C7", 1)]


def test_c_not_first_character_is_not_a_cancelled_invoice_in_monthly_rate(c_inside):
    got = q(c_inside, "SELECT invoices, cancelled_invoices, cancellation_rate_pct "
                      "FROM monthly_cancellation")
    assert got == [(2, 1, 50.0)]


def test_customer_windows_has_no_guest_rows(tiny):
    # tiny has 5 clean lines with a customer id and 3 clean guest lines.
    assert q(tiny, "SELECT COUNT(*), COUNT(CustomerID) FROM customer_order_windows") == [(5, 5)]


def _same_date(tmp_path_factory, order):
    inv = {"X1": 3.0, "X2": 2.0}  # one unit each; revenue 3 and 2
    return _build(tmp_path_factory, "samedate", [
        _row(n, "S2" if n == "X1" else "S1", "Alpha", 1, "2011-06-01 10:00:00", inv[n], "c1", "France")
        for n in order])


@pytest.mark.parametrize("order", [("X2", "X1"), ("X1", "X2")])
def test_order_seq_breaks_date_ties_by_invoice_number(tmp_path_factory, order):
    db = _same_date(tmp_path_factory, order)
    got = q(db, "SELECT InvoiceNo, order_seq FROM customer_order_windows ORDER BY order_seq")
    assert got == [("X1", 1), ("X2", 2)]


@pytest.mark.parametrize("order", [("X2", "X1"), ("X1", "X2")])
def test_running_revenue_breaks_date_ties_by_invoice_number(tmp_path_factory, order):
    db = _same_date(tmp_path_factory, order)
    got = q(db, "SELECT InvoiceNo, running_customer_revenue FROM customer_order_windows "
                "ORDER BY running_customer_revenue")
    assert got == [("X1", 3.0), ("X2", 5.0)]


def test_guest_revenue_is_zero_not_null_when_every_line_has_a_customer(reversed_numbers):
    assert q(reversed_numbers, "SELECT guest_revenue_share_pct FROM headline_metrics") == [(0.0,)]


def test_repeat_customers_is_zero_not_null_without_identified_customers(tmp_path_factory):
    db = _build(tmp_path_factory, "guestonly", [
        _row("G1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 4.0, "", "Italy")])
    assert q(db, "SELECT customers, repeat_customers FROM headline_metrics") == [(0, 0)]


def test_repeat_customers_is_zero_when_nobody_ordered_twice(tmp_path_factory):
    db = _build(tmp_path_factory, "once", [
        _row("O1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 4.0, "c1", "France")])
    assert q(db, "SELECT customers, repeat_customers FROM headline_metrics") == [(1, 0)]


@pytest.mark.parametrize("order", [("Spain", "France"), ("France", "Spain")])
def test_primary_country_ties_break_by_invoice_number_then_country(tmp_path_factory, order):
    # c1: same earliest date, invoices A1 (Spain) and A2 (France) -> lowest invoice, Spain.
    # c2: same date and invoice, Spain and France -> alphabetically first country, France.
    rows = []
    for country in order:
        inv = "A1" if country == "Spain" else "A2"
        rows.append(_row(inv, "S1", "Alpha", 1, "2011-07-01 10:00:00", 1.0, "c1", country))
        rows.append(_row("B1", "S1", "Alpha", 1, "2011-07-01 10:00:00", 1.0, "c2", country))
    db = _build(tmp_path_factory, "tie", rows)
    got = q(db, "SELECT CustomerID, primary_country FROM dim_customers ORDER BY CustomerID")
    assert got == [("c1", "Spain"), ("c2", "France")]


def test_guest_only_data_renders_with_zero_rates(tmp_path_factory):
    from pipeline import render
    db = _build(tmp_path_factory, "guestrender", [
        _row("G1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 4.0, "", "Italy")])
    m = run.compute_metrics(db, run.run_quality(db, run.load_baseline()))
    assert m["headline"]["repeat_purchase_rate_pct"] == 0
    assert m["headline"]["repeat_revenue_share_pct"] == 0
    block = render.results_block(m)
    assert "| Repeat-purchase rate | 0.00% |" in block
    assert "| Revenue share from repeat customers (identified customers) | 0.00% |" in block
    assert "| Month-1 cohort retention | 0.00% |" in block


def _metrics_of(db):
    return run.compute_metrics(db, run.run_quality(db, run.load_baseline()))


def test_single_month_dashboard_draws_one_point(tmp_path_factory):
    from pipeline import render
    db = _build(tmp_path_factory, "onemonth", [
        _row("M1", "S1", "Alpha", 2, "2011-06-01 10:00:00", 5.0, "c1", "France"),
        _row("M2", "S1", "Alpha", 1, "2011-06-09 10:00:00", 5.0, "c2", "France")])
    html = render.dashboard(_metrics_of(db))
    assert '<polyline points="10.0,40.0" class="ln" />' in html


@pytest.fixture
def no_description(tmp_path_factory):
    return _build(tmp_path_factory, "nodesc", [
        _row("P1", "S1", "", 1, "2011-06-01 10:00:00", 4.0, "c1", "France")])


def test_product_with_no_description_keeps_null_not_empty_string(no_description):
    assert q(no_description, "SELECT StockCode, canonical_description FROM dim_products") == [
        ("S1", None)]


def test_clean_orders_keeps_lines_whose_product_has_no_description(no_description):
    got = q(no_description, "SELECT InvoiceNo, description, line_revenue FROM clean_orders")
    assert got == [("P1", None, 4.0)]


@pytest.fixture
def lower_c(tmp_path_factory):
    # SPEC: a cancellation is an invoice number starting with C. c7 starts with a lower-case c.
    return _build(tmp_path_factory, "lowerc", [
        _row(n, "S1", "Alpha", 1, f"2011-05-0{i} 10:00:00", 5.0, "c1", "France")
        for i, n in enumerate(("A1", "B1", "C8", "C9", "c7"), 1)])


def test_lower_case_c_invoice_is_not_a_cancellation_flag(lower_c):
    got = q(lower_c, "SELECT InvoiceNo, is_cancellation FROM clean_orders ORDER BY InvoiceNo")
    assert got == [("A1", 0), ("B1", 0), ("C8", 1), ("C9", 1), ("c7", 0)]


def test_lower_case_c_invoice_is_not_a_cancelled_invoice_in_monthly_rate(lower_c):
    got = q(lower_c, "SELECT invoices, cancelled_invoices, cancellation_rate_pct "
                     "FROM monthly_cancellation")
    assert got == [(5, 2, 40.0)]


def test_lower_case_c_invoice_is_not_a_cancelled_invoice_in_headline(lower_c):
    got = q(lower_c, "SELECT invoices, cancelled_invoices, cancellation_rate_pct "
                     "FROM headline_metrics")
    assert got == [(5, 2, 40.0)]


def test_order_seq_is_dense_when_a_customer_has_tied_lines(tmp_path_factory):
    # X1 has two lines on one timestamp and X2 follows: three lines, so 1, 2, 3.
    db = _build(tmp_path_factory, "tiedlines", [
        _row("X1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 3.0, "c1", "France"),
        _row("X1", "S2", "Beta", 1, "2011-06-01 10:00:00", 2.0, "c1", "France"),
        _row("X2", "S1", "Alpha", 1, "2011-06-02 10:00:00", 4.0, "c1", "France")])
    got = q(db, "SELECT order_seq, running_customer_revenue FROM customer_order_windows "
                "ORDER BY order_seq")
    assert [r[0] for r in got] == [1, 2, 3]
    assert got[1:] == [(2, 5.0), (3, 9.0)]


def test_stock_code_case_is_preserved_in_top_products(tmp_path_factory):
    db = _build(tmp_path_factory, "lowercode", [
        _row("L1", "85123a", "Alpha", 1, "2011-06-01 10:00:00", 4.0, "c1", "France")])
    assert q(db, "SELECT stock_code FROM product_country_revenue") == [("85123a",)]


def test_raw_positive_revenue_is_zero_not_null_without_a_positive_line(tmp_path_factory):
    db = _build(tmp_path_factory, "nopositive", [
        _row("C1", "S1", "Alpha", -1, "2011-06-01 10:00:00", 5.0, "c1", "France")])
    assert q(db, "SELECT raw_positive_revenue FROM headline_metrics") == [(0.0,)]


@pytest.mark.parametrize("column", ["revenue_total", "raw_positive_revenue"])
def test_headline_revenue_is_rounded_to_two_places(tmp_path_factory, column):
    # 3 * 0.1111 = 0.3333 (0.33330000000000004 as a float): two places gives 0.33.
    db = _build(tmp_path_factory, "rounding", [
        _row("R1", "S1", "Alpha", 3, "2011-06-01 10:00:00", 0.1111, "c1", "France")])
    m = _metrics_of(db)
    assert m["headline"][column] == 0.33
    assert f'"{column}": 0.33,' in run.dump(m)


def test_rows_loaded_counts_a_row_with_no_invoice_number(tmp_path_factory):
    db = _build(tmp_path_factory, "noinvoice", [
        _row("O1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 4.0, "c1", "France")])
    db.execute("INSERT INTO raw_orders VALUES "
               "(NULL, 'S1', 'Alpha', 1, '2011-06-02 10:00:00', 4.0, 'c1', 'France')")
    assert q(db, "SELECT rows_loaded FROM headline_metrics") == [(2,)]


@pytest.fixture(scope="module")
def tied_countries(tmp_path_factory):
    """Four countries with equal revenue, each with two equal-revenue products; the
    rows are loaded in an order unrelated to the alphabet."""
    rows = []
    for country in ("Zed", "Mid", "Bar", "Alp"):
        rows.append(_row("I" + country, "S1", "Alpha", 1, "2011-06-01 10:00:00", 5.0, "", country))
        rows.append(_row("J" + country, "S2", "Beta", 1, "2011-06-02 10:00:00", 5.0, "", country))
    return _metrics_of(_build(tmp_path_factory, "tiedcountries", rows))["top_products"]


def test_top_products_keep_the_alphabetically_first_of_tied_countries(tied_countries):
    assert {t["country"] for t in tied_countries} == {"Alp", "Bar", "Mid"}


def test_top_products_list_tied_countries_alphabetically_then_by_rank(tied_countries):
    assert [(t["country"], t["rank"]) for t in tied_countries] == [
        ("Alp", 1), ("Alp", 2), ("Bar", 1), ("Bar", 2), ("Mid", 1), ("Mid", 2)]


def test_top_products_rank_counts_tied_products_one_by_one(tied_countries):
    first = [t for t in tied_countries if t["country"] == "Alp"]
    assert [(t["rank"], t["stock_code"]) for t in first] == [(1, "S1"), (2, "S2")]


@pytest.mark.parametrize("descs", [("", "Alpha"), ("Alpha", "")])
def test_dim_products_prefers_a_description_over_a_missing_one(tmp_path_factory, descs):
    db = _build(tmp_path_factory, "descnull", [
        _row(f"D{i}", "S1", d, 1, "2011-01-05 10:00:00", 2.0, "c1", "France")
        for i, d in enumerate(descs)])
    assert q(db, "SELECT StockCode, canonical_description FROM dim_products") == [("S1", "Alpha")]


def test_clean_orders_drops_zero_quantity_lines(tmp_path_factory):
    db = _build(tmp_path_factory, "zeroqty", [
        _row("Z1", "S1", "Alpha", 0, "2011-06-01 10:00:00", 5.0, "c1", "France"),
        _row("Z2", "S1", "Alpha", 1, "2011-06-02 10:00:00", 5.0, "c1", "France")])
    assert q(db, "SELECT InvoiceNo FROM clean_orders") == [("Z2",)]


def test_invoice_number_that_is_only_the_letter_c_is_a_cancellation_flag(tmp_path_factory):
    db = _build(tmp_path_factory, "barec", [
        _row("C", "S1", "Alpha", 1, "2011-06-01 10:00:00", 5.0, "c1", "France")])
    assert q(db, "SELECT InvoiceNo, is_cancellation FROM clean_orders") == [("C", 1)]


@pytest.mark.parametrize("view", ["monthly_cancellation", "headline_metrics"])
def test_invoice_number_that_is_only_the_letter_c_counts_as_cancelled(tmp_path_factory, view):
    db = _build(tmp_path_factory, "barecrate", [
        _row("C", "S1", "Alpha", 1, "2011-06-01 10:00:00", 5.0, "c1", "France"),
        _row("A1", "S1", "Alpha", 1, "2011-06-02 10:00:00", 5.0, "c1", "France")])
    got = q(db, f"SELECT invoices, cancelled_invoices, cancellation_rate_pct FROM {view}")
    assert got == [(2, 1, 50.0)]


def test_order_seq_restarts_for_each_customer(tiny):
    got = q(tiny, "SELECT CustomerID, InvoiceNo, order_seq FROM customer_order_windows "
                  "ORDER BY CustomerID, InvoiceDate")
    assert got == [("c1", "A1", 1), ("c1", "A2", 2), ("c1", "A3", 3),
                   ("c2", "C1", 1), ("c2", "A4", 2)]


def test_customer_windows_line_revenue_is_quantity_times_price(tiny):
    # c1: 2 * 5.0, 1 * 5.0, 3 * 2.0; c2: 1 * 5.0, 1 * 2.0.
    got = q(tiny, "SELECT CustomerID, InvoiceNo, line_revenue FROM customer_order_windows "
                  "ORDER BY CustomerID, InvoiceDate")
    assert got == [("c1", "A1", 10.0), ("c1", "A2", 5.0), ("c1", "A3", 6.0),
                   ("c2", "C1", 5.0), ("c2", "A4", 2.0)]


def test_top_products_revenue_is_rounded_to_two_places(tmp_path_factory):
    # 3 * 0.1111 = 0.3333: two places gives 0.33.
    db = _build(tmp_path_factory, "prodround", [
        _row("R1", "S1", "Alpha", 3, "2011-06-01 10:00:00", 0.1111, "c1", "France")])
    assert q(db, "SELECT stock_code, revenue FROM product_country_revenue") == [("S1", 0.33)]


def test_monthly_prev_revenue_is_rounded_to_two_places(tmp_path_factory):
    # 3 * 0.1111 is 0.33330000000000004 as a float; the previous-month column shows 0.33.
    db = _build(tmp_path_factory, "prevround", [
        _row("R1", "S1", "Alpha", 3, "2011-06-01 10:00:00", 0.1111, "c1", "France"),
        _row("R2", "S1", "Alpha", 1, "2011-07-01 10:00:00", 1.0, "c1", "France")])
    got = q(db, "SELECT month, revenue, prev_revenue FROM monthly_revenue ORDER BY month")
    assert got == [("2011-06", 0.33, None), ("2011-07", 1.0, 0.33)]


def test_raw_columns_are_stored_as_integer_quantity_and_real_price(tiny):
    # Prices such as 5.0 must stay real, and quantities must not become text.
    assert q(tiny, "SELECT DISTINCT typeof(Quantity), typeof(UnitPrice) FROM raw_orders") == [
        ("integer", "real")]


def test_month_without_cancellations_still_appears_with_a_zero_rate(tmp_path_factory):
    db = _build(tmp_path_factory, "nocancel", [
        _row("A1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 5.0, "c1", "France"),
        _row("A2", "S1", "Alpha", 1, "2011-07-01 10:00:00", 5.0, "c1", "France"),
        _row("C3", "S1", "Alpha", -1, "2011-07-02 10:00:00", 5.0, "c1", "France")])
    got = [(r["month"], r["cancellation_rate_pct"]) for r in _metrics_of(db)["monthly"]]
    assert got == [("2011-06", 0.0), ("2011-07", 50.0)]


def test_peak_month_tie_goes_to_the_earlier_month(tmp_path_factory):
    from pipeline import render
    db = _build(tmp_path_factory, "peaktie", [
        _row("P1", "S1", "Alpha", 1, "2011-06-01 10:00:00", 5.0, "c1", "France"),
        _row("P2", "S1", "Alpha", 1, "2011-07-01 10:00:00", 5.0, "c1", "France")])
    m = _metrics_of(db)
    assert "| Peak revenue month | 2011-06 (5.00) |" in render.results_block(m)
    assert "2011-06 is the highest revenue month" in render.recommendation(m)


def test_dashboard_tile_label_is_html_escaped(tiny, monkeypatch):
    from pipeline import render
    monkeypatch.setattr(render, "TILES", [("Net <b> & gross", "revenue_total", "money")])
    html = render.dashboard(_metrics_of(tiny))
    assert '<span class="k">Net &lt;b&gt; &amp; gross</span>' in html
    assert "Net <b>" not in html

