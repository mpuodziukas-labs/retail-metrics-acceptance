"""Plant two faults in copies of the sample; the quality gate must turn RED on each."""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import run  # noqa: E402


def _read(src):
    with open(src, newline="") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames, list(r)


def _write(fields, rows, dst):
    with open(dst, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def plant_duplicate(src, dst):
    """Append a second copy of an existing order line (duplicate key)."""
    fields, rows = _read(src)
    rows.append(dict(next(r for r in rows if int(r["Quantity"]) > 0)))
    _write(fields, rows, dst)


def plant_null_key(src, dst):
    """Blank the StockCode of one sold line: an inner join to dim_products drops its revenue."""
    fields, rows = _read(src)
    victim = next(r for r in rows if int(r["Quantity"]) > 0 and float(r["UnitPrice"]) > 0
                  and r["CustomerID"])
    victim["StockCode"] = ""
    _write(fields, rows, dst)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "m.json"
        for label, plant in (("duplicate order line", plant_duplicate),
                             ("NULL join key", plant_null_key)):
            bad = td / "bad.csv"
            plant(run.CSV, bad)
            code = run.main(["--csv", str(bad), "--out", str(out)])
            caught = code == 1 and not out.exists()
            print(f"selftest {label}: {'CAUGHT' if caught else 'MISSED'}")
            ok &= caught
        control = run.main(["--csv", str(run.CSV), "--out", str(out)]) == 0
        print(f"selftest clean sample stays green: {'yes' if control else 'NO'}")
        ok &= control
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
