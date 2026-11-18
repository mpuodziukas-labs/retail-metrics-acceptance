"""Shared fixtures. Expected values are computed from the CSV with plain Python,
never from the SQL under test."""
import csv
from collections import defaultdict
from pathlib import Path

import pytest

from pipeline import run

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def rows():
    with open(run.CSV, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="session")
def conn():
    return run.build_db(run.CSV)


@pytest.fixture(scope="session")
def metrics(conn):
    return run.compute_metrics(conn, run.run_quality(conn, run.load_baseline()))


def month(r):
    return r["InvoiceDate"][:7]


def clean_lines(rows):
    """Lines that survive clean_orders: product key present, qty > 0, price > 0."""
    return [r for r in rows
            if r["StockCode"] and int(r["Quantity"]) > 0 and float(r["UnitPrice"]) > 0]


def revenue(r):
    return int(r["Quantity"]) * float(r["UnitPrice"])


def customer_country(rows):
    """Country on the customer's earliest line (all raw rows, as dim_customers)."""
    first = {}
    for r in rows:
        cid = r["CustomerID"]
        if cid and (cid not in first or r["InvoiceDate"] < first[cid][0]):
            first[cid] = (r["InvoiceDate"], r["Country"])
    return {k: v[1] for k, v in first.items()}


def by_customer_months(rows):
    """customer -> {invoice: revenue}, and customer -> set(active months)."""
    inv, months = defaultdict(lambda: defaultdict(float)), defaultdict(set)
    for r in clean_lines(rows):
        if r["CustomerID"]:
            inv[r["CustomerID"]][r["InvoiceNo"]] += revenue(r)
            months[r["CustomerID"]].add(month(r))
    return inv, months
