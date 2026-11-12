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


def _spec_names():
    from scada_schema import study as st

    return [sp.name for sp in st.SPECS]


@pytest.mark.data
@pytest.mark.parametrize("name", _spec_names())
def test_study_numbers_are_reproducible(study, name, tmp_path):
    """Every dataset of the study is loaded and checked again and must give the numbers of results/study.json (needs the full files)."""
    from scada_schema import study as st

    spec = next(sp for sp in st.SPECS if sp.name == name)
    var = f"SCADA_SCHEMA_{spec.loader.upper()}_DIR"
    if not os.environ.get(var):
        pytest.skip(f"set {var}")
    entry, _ = st.study_dataset(spec, tmp_path)
    want = next(d for d in study["datasets"] if d["name"] == name)
    for key in ("rows", "devices", "signals", "first", "last", "validation", "qc", "removed", "demo"):
        assert entry[key] == want[key], key


# ----------------------------------------------------------------------------------------------- release metadata

# built from pieces so that this file does not itself contain what it forbids
PRIVATE = re.compile("(?<![a-z])" + "er" + "gy" + "(?![a-z])|@" + "er" + "gy|" + "konrad" + r"\." + "waleszczynski", re.I)


def _release():
    return json.loads((ROOT / "release.json").read_text(encoding="utf-8"))


def test_version_is_the_same_everywhere():
    from scada_schema import __version__

    v = _release()["version"]
    assert v == __version__
    assert f"version: {v}" in (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert f"## [{v}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_dois_are_well_formed_and_consistent_once_assigned():
    rel = _release()
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    for key in ("doi_concept", "doi_version"):
        if rel[key] is not None:
            assert re.fullmatch(r"10\.5281/zenodo\.\d+", rel[key]), f"{key} is not a Zenodo DOI"
            assert rel[key] in cff, f"{key} is not in CITATION.cff"
    if rel["doi_version"]:
        assert rel["doi_concept"], "a version DOI needs the concept DOI"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    placeholder = "A DOI is assigned by Zenodo when the first release is archived"
    assert (placeholder in readme) == (rel["doi_concept"] is None)


def test_pypi_flag_matches_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    on_pypi = _release()["published_on_pypi"]
    assert ("Not on PyPI yet" not in readme) == on_pypi
    assert ("img.shields.io/pypi/v/scada-schema" in readme) == on_pypi


def test_release_workflow_uses_trusted_publishing_and_no_token():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "id-token: write" in wf and "name: pypi" in wf and "PYPI_PUBLISH" in wf and "pypa/gh-action-pypi-publish" in wf
    assert "secrets." not in wf and "password" not in wf.lower() and "TOKEN" not in wf.replace("token anywhere", "")


def test_release_metadata_is_valid_and_private_free():
    z = json.loads((ROOT / ".zenodo.json").read_text(encoding="utf-8"))
    assert z["upload_type"] == "software" and z["license"] == "MIT" and z["creators"] == [{"name": "Waleszczyński, Konrad"}]
    assert any(r["identifier"].startswith("10.5281/zenodo.") and r["relation"] == "requires" for r in z["related_identifiers"])
    for f in (".zenodo.json", "CITATION.cff", "pyproject.toml", "release.json", "docs/RELEASING.md"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert not PRIVATE.search(text), f
        emails = [e for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text) if not e.endswith("users.noreply.github.com")]
        assert not emails, (f, emails)
