# Runbook

## Run

- `make test` runs all tests.
- `make regen` rebuilds `out/metrics.json`, `dashboard/index.html`, `docs/recommendation.md` and the README Results block. Run it twice: the second run changes nothing.
- `make selftest` plants two faults in copies of the sample and expects the gate to go RED on each.
- Needs Python 3.11 and pytest. Nothing else is installed.

## When the gate is RED

1. Read the printed line: `RED <check> (<dimension>): observed N, allowed M`.
2. `duplicate_order_lines`: find rows that share invoice, stock code, time, quantity, price and customer; confirm with the source owner whether they are double loads.
3. `null_join_key` or `orphan_rows`: a sold line has no stock code, so an inner join to the product dimension would drop its revenue. Fix the source row.
4. `negative_price`: a price below zero is invalid; fix or exclude at source.
5. No metrics file is written while the gate is RED.

## Re-baseline

Only when the source owner confirms a count is genuine (for example the three exact duplicate lines already in the sample): edit `data/quality_baseline.json`, say why in the commit message, run `make regen` and `make test`.
