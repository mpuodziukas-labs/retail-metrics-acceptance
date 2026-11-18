import json
import re
from pathlib import Path

from pipeline import regen, render, run

ROOT = Path(__file__).resolve().parent.parent
TEST_ID = re.compile(r"`(tests/[\w./]+)::(\w+)`")


def _metrics():
    conn = run.build_db(run.CSV)
    return run.compute_metrics(conn, run.run_quality(conn, run.load_baseline()))


def _assert_ids_exist(text):
    ids = TEST_ID.findall(text)
    assert ids, "no test id"
    for f, name in ids:
        assert re.search(rf"^def {name}\(", (ROOT / f).read_text(), re.M), (f, name)


def test_readme_section_order():
    heads = re.findall(r"^#{1,2} .+$", (ROOT / "README.md").read_text(), re.M)
    assert heads[1:] == ["## Run it in 60 seconds", "## Results",
                         "## Honesty Statement", "## Limitations"]


def test_readme_results_block_drift():
    text = (ROOT / "README.md").read_text()
    m = re.search(r"<!-- RESULTS:START -->\n(.*?)\n<!-- RESULTS:END -->", text, re.S)
    assert m, "markers missing"
    assert m.group(1) == render.results_block(_metrics())


def test_dashboard_file_matches_render():
    assert (ROOT / "dashboard/index.html").read_text() == render.dashboard(_metrics())


def test_metrics_file_matches_pipeline():
    assert json.loads((ROOT / "out/metrics.json").read_text()) == json.loads(
        json.dumps(_metrics(), sort_keys=True))


def test_recommendation_matches_regen():
    text = (ROOT / "docs/recommendation.md").read_text()
    assert text == render.recommendation(_metrics())
    assert text.count("\n## ") == 3


def test_recommendation_has_no_invented_money():
    text = (ROOT / "docs/recommendation.md").read_text()
    assert "saving" not in text.lower() and "save " not in text.lower()
    assert f'{_metrics()["headline"]["repeat_revenue_share_pct"]:.2f}%' in text


def test_every_story_names_an_existing_test():
    text = (ROOT / "docs/requirements.md").read_text()
    stories = re.split(r"^## US\d+", text, flags=re.M)[1:]
    assert 6 <= len(stories) <= 8
    for s in stories:
        assert all(w in s for w in ("Given", "When", "Then", "As a"))
        _assert_ids_exist(s)


def test_process_map_present():
    text = (ROOT / "docs/requirements.md").read_text()
    assert "```mermaid" in text and "flowchart" in text


def test_runbook_sections_present():
    text = (ROOT / "docs/RUNBOOK.md").read_text()
    for h in ("## Run", "## When the gate is RED", "## Re-baseline"):
        assert h in text


def _trace_rows():
    return [l for l in (ROOT / "SPEC.md").read_text().splitlines()
            if l.startswith("|") and l.rstrip().endswith(("PROVEN |", "GAP |"))]


def _sort_key(line):
    nums = [int(n) for n in re.findall(r"(\d+)/10", line)]
    assert len(nums) == 3, line
    return (max(nums), sum(nums))


def test_trace_table_proven_rows_have_existing_tests():
    rows = _trace_rows()
    proven = [l for l in rows if l.rstrip().endswith("PROVEN |")]
    gaps = [l for l in rows if l.rstrip().endswith("GAP |")]
    assert len(proven) == 12 and len(gaps) == 5
    assert rows == proven + gaps, "PROVEN rows must come first"
    for l in proven:
        _assert_ids_exist(l)
    for l in gaps:
        assert not TEST_ID.search(l)


def test_trace_table_ordered_by_highest_count():
    for group in ("PROVEN |", "GAP |"):
        keys = [_sort_key(l) for l in _trace_rows() if l.rstrip().endswith(group)]
        assert keys == sorted(keys, reverse=True), keys


def test_no_em_dash_in_repo_files():
    for p in ROOT.rglob("*"):
        if (p.is_file() and ".git" not in p.parts and ".pytest_cache" not in p.parts
                and p.suffix in {".md", ".py", ".sql", ".html", ".yml", ".json", ".txt"}
                and "LICENSE-dataset" not in p.name):
            assert chr(0x2014) not in p.read_text(errors="ignore"), p


def test_regen_is_idempotent_on_disk():
    before = {p: (ROOT / p).read_bytes() for p in regen.OUTPUTS}
    regen.main()
    assert {p: (ROOT / p).read_bytes() for p in regen.OUTPUTS} == before
