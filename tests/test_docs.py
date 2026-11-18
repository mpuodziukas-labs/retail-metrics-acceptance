import json
import re
from pathlib import Path

import pytest

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
    rows = []
    for l in (ROOT / "SPEC.md").read_text().splitlines():
        if l.startswith("|") and l.rstrip().endswith("|"):
            status = l.strip().strip("|").split("|")[-1].strip()
            if status.startswith(("PROVEN", "PARTIAL", "GAP")):
                rows.append((status.split()[0].rstrip(":"), l))
    return rows


def _sort_key(line):
    nums = [int(n) for n in re.findall(r"(\d+)/10", line)]
    assert len(nums) == 3, line
    return (max(nums), sum(nums))


def _of(status):
    return [l for st, l in _trace_rows() if st == status]


def test_trace_table_proven_rows_have_existing_tests():
    assert (len(_of("PROVEN")), len(_of("PARTIAL")), len(_of("GAP"))) == (11, 1, 5)
    order = [st for st, _ in _trace_rows()]
    assert order == sorted(order, key=("PROVEN", "PARTIAL", "GAP").index), order
    for l in _of("PROVEN") + _of("PARTIAL"):
        _assert_ids_exist(l)
    for l in _of("GAP"):
        assert not TEST_ID.search(l)


def test_trace_table_partial_row_states_its_limit():
    (row,) = _of("PARTIAL")
    assert row.startswith("| Communication |")
    assert "written output, numbers tested; no live presentation" in row


def test_trace_table_ordered_by_highest_count():
    for status in ("PROVEN", "PARTIAL", "GAP"):
        keys = [_sort_key(l) for l in _of(status)]
        assert keys == sorted(keys, reverse=True), keys


def test_trace_table_dashboard_counts():
    (row,) = [l for l in _of("PROVEN") if l.startswith("| Dashboards / reporting |")]
    assert "data 10/10, business 6/10, operations 9/10" in row


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


def test_regen_overwrites_stale_outputs(tmp_path, monkeypatch):
    """Start from stale generated files in a scratch root: regen must rewrite all four."""
    for d in ("out", "dashboard", "docs"):
        (tmp_path / d).mkdir()
    for p in ("out/metrics.json", "dashboard/index.html", "docs/recommendation.md"):
        (tmp_path / p).write_text("stale")
    readme = (ROOT / "README.md").read_text()
    stale = re.sub(r"(<!-- RESULTS:START -->\n).*?(\n<!-- RESULTS:END -->)",
                   lambda mo: mo.group(1) + "stale" + mo.group(2), readme, flags=re.S)
    assert stale != readme
    (tmp_path / "README.md").write_text(stale)
    monkeypatch.setattr(regen, "ROOT", tmp_path)
    assert regen.main() == 0
    m = _metrics()
    assert (tmp_path / "out/metrics.json").read_text() == run.dump(m)
    assert (tmp_path / "dashboard/index.html").read_text() == render.dashboard(m)
    assert (tmp_path / "docs/recommendation.md").read_text() == render.recommendation(m)
    assert (tmp_path / "README.md").read_text() == readme


def _scratch_root(tmp_path, readme_text):
    for d in ("out", "dashboard", "docs"):
        (tmp_path / d).mkdir()
    for p in ("out/metrics.json", "dashboard/index.html", "docs/recommendation.md"):
        (tmp_path / p).write_text("stale")
    (tmp_path / "README.md").write_text(readme_text)


def test_regen_refuses_when_readme_markers_missing(tmp_path, monkeypatch):
    _scratch_root(tmp_path, "no markers here\n")
    monkeypatch.setattr(regen, "ROOT", tmp_path)
    with pytest.raises(AssertionError):
        regen.main()
    assert (tmp_path / "README.md").read_text() == "no markers here\n"


def test_regen_refuses_when_gate_is_red(tmp_path, monkeypatch):
    readme = (ROOT / "README.md").read_text()
    _scratch_root(tmp_path, readme)
    monkeypatch.setattr(regen, "ROOT", tmp_path)
    red = [{"name": "x", "dimension": "d", "observed": 1, "allowed": 0, "ok": False}]
    monkeypatch.setattr(run, "run_quality", lambda conn, baseline: red)
    assert regen.main() == 1
    for p in ("out/metrics.json", "dashboard/index.html", "docs/recommendation.md"):
        assert (tmp_path / p).read_text() == "stale"
    assert (tmp_path / "README.md").read_text() == readme


def test_regen_done_message_names_every_rewritten_file(tmp_path, monkeypatch, capsys):
    readme = (ROOT / "README.md").read_text()
    stale = re.sub(r"(<!-- RESULTS:START -->\n).*?(\n<!-- RESULTS:END -->)",
                   lambda mo: mo.group(1) + "stale" + mo.group(2), readme, flags=re.S)
    _scratch_root(tmp_path, stale)
    before = {p: (tmp_path / p).read_text() for p in
              ("out/metrics.json", "dashboard/index.html", "docs/recommendation.md", "README.md")}
    monkeypatch.setattr(regen, "ROOT", tmp_path)
    assert regen.main() == 0
    rewritten = [p for p, text in before.items() if (tmp_path / p).read_text() != text]
    assert len(rewritten) == 4
    assert capsys.readouterr().out == "regen done: " + ", ".join(rewritten) + "\n"


def test_dashboard_escapes_html_in_values():
    m = _metrics()
    m["top_products"][0]["description"] = 'Tom & <u>"X"</u>'
    html = render.dashboard(m)
    assert "Tom &amp; &lt;u&gt;&quot;X&quot;&lt;/u&gt;" in html
    assert "<u>" not in html and "Tom & " not in html


def test_results_block_counts_only_passing_quality_checks():
    m = _metrics()
    m["quality"][0] = dict(m["quality"][0], ok=False)
    assert "| Quality checks passing | 3/4 |" in render.results_block(m)


def test_results_block_total_follows_number_of_checks():
    m = _metrics()
    m["quality"] = m["quality"][:3]
    assert "| Quality checks passing | 3/3 |" in render.results_block(m)


def _regen_without(tmp_path, monkeypatch, missing):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text((ROOT / "README.md").read_text())
    for d in {"out", "dashboard"} - {missing}:
        (tmp_path / d).mkdir()
    monkeypatch.setattr(regen, "ROOT", tmp_path)
    assert regen.main() == 0


def test_regen_creates_missing_out_folder(tmp_path, monkeypatch):
    _regen_without(tmp_path, monkeypatch, "out")
    assert (tmp_path / "out/metrics.json").is_file()


def test_regen_creates_missing_dashboard_folder(tmp_path, monkeypatch):
    _regen_without(tmp_path, monkeypatch, "dashboard")
    assert (tmp_path / "dashboard/index.html").is_file()


def _snapshot(root):
    return {p: (root / p).read_text() for p in regen.OUTPUTS}


@pytest.mark.parametrize("name", ["dashboard", "recommendation", "results_block"])
def test_regen_render_failure_leaves_previous_outputs_untouched(tmp_path, monkeypatch, name):
    _scratch_root(tmp_path, (ROOT / "README.md").read_text())
    before = _snapshot(tmp_path)
    monkeypatch.setattr(regen, "ROOT", tmp_path)

    def boom(m):
        raise RuntimeError("render failed")
    monkeypatch.setattr(render, name, boom)
    with pytest.raises(RuntimeError):
        regen.main()
    assert _snapshot(tmp_path) == before


HEADER = "InvoiceNo,StockCode,Description,Quantity,InvoiceDate,UnitPrice,CustomerID,Country\n"


@pytest.mark.parametrize("body", ["", "C1,S1,Alpha,-1,2011-01-05 10:00:00,5.0,c1,France\n"],
                         ids=["header_only", "all_cancelled"])
def test_regen_refuses_when_there_are_no_clean_order_lines(tmp_path, monkeypatch, capsys, body):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text(HEADER + body)
    root = tmp_path / "root"
    root.mkdir()
    _scratch_root(root, (ROOT / "README.md").read_text())
    before = _snapshot(root)
    monkeypatch.setattr(run, "CSV", csv_path)
    monkeypatch.setattr(regen, "ROOT", root)
    assert regen.main() == 1
    assert capsys.readouterr().out == "GATE RED: regen refused, no clean order lines\n"
    assert _snapshot(root) == before
