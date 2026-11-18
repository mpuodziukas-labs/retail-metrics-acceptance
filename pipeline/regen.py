"""Rebuild every generated number: metrics, dashboard, recommendation, README Results block."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import render, run  # noqa: E402

ROOT = run.ROOT
OUTPUTS = ["out/metrics.json", "dashboard/index.html", "docs/recommendation.md", "README.md"]


def main():
    conn = run.build_db(run.CSV)
    checks = run.run_quality(conn, run.load_baseline())
    if not all(c["ok"] for c in checks):
        print("GATE RED: regen refused")
        return 1
    m = run.compute_metrics(conn, checks)
    if not m["monthly"]:
        print("GATE RED: regen refused, no clean order lines")
        return 1
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = render.results_block(m)
    new, n = re.subn(r"(<!-- RESULTS:START -->\n).*?(\n<!-- RESULTS:END -->)",
                     lambda mo: mo.group(1) + block + mo.group(2), readme, flags=re.S)
    assert n == 1, "README markers missing"
    texts = [run.dump(m), render.dashboard(m), render.recommendation(m), new]
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "dashboard").mkdir(exist_ok=True)
    for path, text in zip(OUTPUTS, texts):
        (ROOT / path).write_text(text, encoding="utf-8")
    print("regen done: " + ", ".join(OUTPUTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
