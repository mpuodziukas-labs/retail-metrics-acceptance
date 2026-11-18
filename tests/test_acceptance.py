"""Acceptance tests: one or more per user story in docs/requirements.md.
Each story id (US1..US8) appears in the test names below."""
import html.parser
import re
from collections import defaultdict

import pytest

from pipeline import render, run, selftest
from tests.conftest import (by_customer_months, clean_lines, customer_country,
                            month, revenue)


def monthly_expected(rows):
    out = defaultdict(float)
    for r in clean_lines(rows):
        out[month(r)] += revenue(r)
    return dict(sorted(out.items()))


# US1 finance lead: monthly revenue, month-over-month change, running total
def test_us1_monthly_revenue_matches_independent_sum(metrics, rows):
    exp = monthly_expected(rows)
    got = {m["month"]: m["revenue"] for m in metrics["monthly"]}
    assert list(got) == list(exp) and len(got) == 13
    for k, v in exp.items():
        assert got[k] == pytest.approx(v, abs=0.01)


def test_us1_month_over_month_change(metrics, rows):
    exp = list(monthly_expected(rows).items())
    mm = metrics["monthly"]
    assert mm[0]["mom_change_pct"] is None
    for i in range(1, len(exp)):
        want = (exp[i][1] - exp[i - 1][1]) / exp[i - 1][1] * 100
        assert mm[i]["mom_change_pct"] == pytest.approx(want, abs=0.01)


def test_us1_running_total_ends_at_total_revenue(metrics, rows):
    total = sum(revenue(r) for r in clean_lines(rows))
    assert metrics["monthly"][-1]["running_total"] == pytest.approx(total, abs=0.01)
    assert metrics["headline"]["revenue_total"] == pytest.approx(total, abs=0.01)


# US2 merchandising lead: top three products per country
def test_us2_top3_products_per_country(metrics, rows):
    cc = customer_country(rows)
    per = defaultdict(lambda: defaultdict(float))
    for r in clean_lines(rows):
        per[cc.get(r["CustomerID"], r["Country"])][r["StockCode"]] += revenue(r)
    assert metrics["top_products"], "no top products produced"
    countries = {t["country"] for t in metrics["top_products"]}
    assert len(countries) == 3
    for c in countries:
        want = sorted(per[c].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        got = [(t["stock_code"], t["revenue"]) for t in metrics["top_products"]
               if t["country"] == c]
        assert [g[0] for g in got] == [w[0] for w in want]
        for g, w in zip(got, want):
            assert g[1] == pytest.approx(w[1], abs=0.01)


# US3 CRM lead: repeat-purchase rate
def test_us3_repeat_purchase_rate(metrics, rows):
    inv, _ = by_customer_months(rows)
    repeat = [c for c, d in inv.items() if len(d) >= 2]
    h = metrics["headline"]
    assert h["customers"] == len(inv)
    assert h["repeat_customers"] == len(repeat)
    assert h["repeat_purchase_rate_pct"] == pytest.approx(100 * len(repeat) / len(inv), abs=0.01)
    share = sum(sum(inv[c].values()) for c in repeat) / sum(sum(d.values()) for d in inv.values())
    assert h["repeat_revenue_share_pct"] == pytest.approx(100 * share, abs=0.01)


# US4 CRM lead: monthly cohort retention
def test_us4_cohort_retention(metrics, rows):
    _, months = by_customer_months(rows)
    cohorts = defaultdict(list)
    for cid, ms in months.items():
        cohorts[min(ms)].append(cid)
    idx = lambda a, b: (int(b[:4]) - int(a[:4])) * 12 + int(b[5:]) - int(a[5:])
    table = {(c["cohort_month"], c["month_index"]): c for c in metrics["cohorts"]}
    for cm, members in cohorts.items():
        assert table[(cm, 0)]["retention_pct"] == 100.0
        assert table[(cm, 0)]["cohort_size"] == len(members)
        for m in sorted({m for c in members for m in months[c]}):
            active = sum(1 for c in members if m in months[c])
            assert table[(cm, idx(cm, m))]["active_customers"] == active
            assert table[(cm, idx(cm, m))]["retention_pct"] == pytest.approx(
                100 * active / len(members), abs=0.01)
    assert sum(c["cohort_size"] for c in metrics["cohorts"] if c["month_index"] == 0) \
        == metrics["headline"]["customers"]


# US5 operations lead: cancellation rate
def test_us5_cancellation_rate(metrics, rows):
    invoices = {r["InvoiceNo"] for r in rows}
    cancelled = {i for i in invoices if i.startswith("C")}
    h = metrics["headline"]
    assert (h["invoices"], h["cancelled_invoices"]) == (len(invoices), len(cancelled))
    assert h["cancellation_rate_pct"] == pytest.approx(100 * len(cancelled) / len(invoices), abs=0.01)
    by_m = defaultdict(set)
    for r in rows:
        by_m[month(r)].add(r["InvoiceNo"])
    for m in metrics["monthly"]:
        inv = by_m[m["month"]]
        want = 100 * sum(1 for i in inv if i.startswith("C")) / len(inv)
        assert m["cancellation_rate_pct"] == pytest.approx(want, abs=0.01)


# US6 finance lead: average order value
def test_us6_average_order_value(metrics, rows):
    cl = clean_lines(rows)
    total = sum(revenue(r) for r in cl)
    orders = len({r["InvoiceNo"] for r in cl})
    assert metrics["headline"]["orders"] == orders
    assert metrics["headline"]["aov"] == pytest.approx(total / orders, abs=0.01)
    assert metrics["monthly"][0]["aov"] == pytest.approx(
        metrics["monthly"][0]["revenue"] / metrics["monthly"][0]["orders"], abs=0.01)


# US7 data steward: bad data stops the pipeline
def test_us7_quality_gate_green_on_vendored_sample(metrics):
    assert all(c["ok"] for c in metrics["quality"])
    assert metrics["headline"]["revenue_gap"] == 0


def test_us7_planted_duplicate_turns_gate_red(tmp_path):
    bad = tmp_path / "dup.csv"
    selftest.plant_duplicate(run.CSV, bad)
    conn = run.build_db(bad)
    red = {c["name"] for c in run.run_quality(conn, run.load_baseline()) if not c["ok"]}
    assert red == {"duplicate_order_lines"}


def test_us7_planted_null_key_turns_gate_red(tmp_path):
    bad = tmp_path / "null.csv"
    selftest.plant_null_key(run.CSV, bad)
    conn = run.build_db(bad)
    red = {c["name"] for c in run.run_quality(conn, run.load_baseline()) if not c["ok"]}
    assert {"null_join_key", "orphan_rows"} <= red


# US8 executive: dashboard shows exactly the metrics file
class _Collect(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.tagged, self.stray = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.stack.append((tag, a.get("data-k"), a.get("data-f"), []))

    def handle_endtag(self, tag):
        t, k, f, text = self.stack.pop()
        if k:
            self.tagged.append((k, f, "".join(text)))

    def handle_data(self, data):
        if self.stack and any(s[1] for s in self.stack):
            self.stack[-1][3].append(data)
        elif re.search(r"\d", data) and not (self.stack and self.stack[-1][0] in ("style", "title")):
            self.stray.append(data)


def _lookup(m, path):
    cur = m
    for p in path.split("."):
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur


FORMATS = {"money": lambda v: f"{v:,.2f}", "money0": lambda v: f"{v:,.0f}",
           "int": lambda v: f"{int(v):,}", "pct": lambda v: f"{v:.2f}%",
           "str": lambda v: str(v)}


def test_us8_dashboard_numbers_match_metrics(metrics):
    page = render.dashboard(metrics)
    p = _Collect()
    p.feed(page)
    assert len(p.tagged) > 100, "dashboard shows too few tagged numbers"
    assert p.stray == [], f"numbers outside tagged elements: {p.stray}"
    assert "<script" not in page and "http://" not in page and "https://" not in page
    for k, f, shown in p.tagged:
        assert shown == FORMATS[f](_lookup(metrics, k)), (k, shown)
    assert {k.split(".")[0] for k, _, _ in p.tagged} >= {"headline", "monthly", "cohorts", "top_products"}
