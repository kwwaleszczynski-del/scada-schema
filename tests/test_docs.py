"""The committed documents are what the code and the study produce: SPEC.md, README.md, docs/data.js and the project page."""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from scada_schema.spec import render_spec

ROOT = Path(__file__).resolve().parents[1]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def study():
    p = ROOT / "results" / "study.json"
    if not p.exists():
        pytest.skip("results/study.json not built (python -m scada_schema study)")
    return json.loads(p.read_text(encoding="utf-8"))


def test_spec_is_generated_from_the_code():
    assert (ROOT / "SPEC.md").read_text(encoding="utf-8") == render_spec()


def test_readme_is_the_template_rendered_from_the_study(study):
    r = script("render_readme")
    text = r.render((ROOT / "docs" / "README.template.md").read_text(encoding="utf-8"), study)
    assert (ROOT / "README.md").read_text(encoding="utf-8") == text
    assert "{{" not in text


def test_readme_has_no_hand_typed_result_numbers():
    """Numbers in the prose must come from placeholders: the template may contain only counts that are part of a name or a version."""
    tpl = (ROOT / "docs" / "README.template.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", tpl, flags=re.S)
    prose = re.sub(r"^\s*\d+\.\s", "", prose, flags=re.M)  # list numbering
    prose = re.sub(r"\{\{[^}]+\}\}|\]\([^)]*\)|`[^`]*`|https?://\S+|doi \S+|10\.\d+/[\w.]+", "", prose)
    allowed = {"10", "60", "2", "61400", "26", "0.25", "0.75", "12", "1.4", "0.5", "0.5.1", "3", "4", "5", "6", "1", "2.3", "15", "7", "9", "11", "0", "34"}
    found = {m for m in re.findall(r"\b\d+(?:\.\d+)*\b", prose)} - allowed
    # CC licence versions and years appear in the data section
    found -= {"4.0", "2018", "2016", "2021", "2020", "2019", "2025", "2026"}
    assert not found, f"hand-typed numbers in the README template: {sorted(found)}"


def test_pages_data_is_built_from_the_study(study):
    b = script("build_pages")
    assert (ROOT / "docs" / "data.js").read_text(encoding="utf-8") == b.build_data(study)
    for f in (ROOT / "results" / "qc").glob("*.html"):
        assert (ROOT / "docs" / "qc" / f.name).read_bytes() == f.read_bytes()


def test_study_covers_five_sources_and_every_table_is_valid(study):
    t = study["totals"]
    assert t["sources"] >= 5 and t["schema_valid"] == t["datasets"] and t["profiles"] == ["pv", "wind"]
    for d in study["datasets"]:
        assert d["validation"]["errors"] == 0 and d["qc"]["checks_run"]


def test_project_page_uses_only_data_from_data_js():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "window.DATA" in html and "plotly" in html.lower()
    assert "integrity=" in html  # the CDN script is pinned


@pytest.mark.data
@pytest.mark.parametrize("name", ["care_A", "pvdaq_34"])
def test_study_numbers_are_reproducible(study, name, tmp_path):
    for var in ("CARE", "PVDAQ"):
        if not os.environ.get(f"SCADA_SCHEMA_{var}_DIR"):
            pytest.skip(f"set SCADA_SCHEMA_{var}_DIR")
    from scada_schema import study as st

    spec = next(s for s in st.SPECS if s.name == name)
    entry, _ = st.study_dataset(spec, tmp_path)
    want = next(d for d in study["datasets"] if d["name"] == name)
    for key in ("rows", "devices", "signals", "first", "last", "validation", "qc"):
        assert entry[key] == want[key], key
