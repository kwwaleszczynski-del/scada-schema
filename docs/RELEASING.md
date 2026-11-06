# Releasing scada-schema

Nothing is published automatically. `.github/workflows/release.yml` builds and checks the sdist and wheel for every published GitHub release;
uploading to PyPI (Trusted Publishing, no token anywhere in the repository) happens only when the repository variable `PYPI_PUBLISH` is `true`.
The version, the DOIs and whether the package is on PyPI live in `release.json`; the README, `CITATION.cff` and the tests read them from there.

## First release (0.1.0), in this order

1. **Zenodo.** Log in at https://zenodo.org with GitHub, open https://zenodo.org/account/settings/github/ and switch the repository
   `kwwaleszczynski-del/scada-schema` on. Zenodo then archives every published GitHub release and takes the metadata from `.zenodo.json`.
2. **PyPI, pending trusted publisher.** At https://pypi.org/manage/account/publishing/ add a pending publisher with exactly:
   project name `scada-schema`; owner `kwwaleszczynski-del`; repository `scada-schema`; workflow `release.yml`; environment `pypi`.
3. **GitHub environment and variable.** In the repository settings: Environments, new environment named `pypi`; then Variables, new repository
   variable `PYPI_PUBLISH` with the value `true`.
4. **Date.** In `CHANGELOG.md` replace `unreleased` in `## [0.1.0] - unreleased` by the release date; in `release.json` set `date_released` to the same
   date, and in `CITATION.cff` add the line `date-released: "YYYY-MM-DD"`. Commit and push to `main`; wait for the tests to pass.
5. **Release.** On GitHub: Releases, Draft a new release; tag `v0.1.0` (create it on publish, target `main`); title `scada-schema 0.1.0`; text from
   the changelog; Publish release. Three things happen: the `release` workflow builds and uploads to PyPI, Zenodo archives the release and
   assigns DOIs.
6. **After the release.** Check https://pypi.org/project/scada-schema/. In `release.json` set `published_on_pypi` to `true` and fill `doi_concept`
   (the DOI that Zenodo shows as "all versions") and `doi_version` (the DOI of version 0.1.0). In `CITATION.cff` add
   `doi: <doi_concept>` and an `identifiers` list with both DOIs (see `pse-opendata/CITATION.cff` for the form). Run
   `python scripts/render_readme.py`, commit, push. `tests/test_docs.py` fails if the README, `release.json` and `CITATION.cff` disagree.

## Later releases

Update `src/scada_schema/_version.py`, `release.json` (`version`, `doi_version`, `date_released`), `CITATION.cff` (`version`, `date-released`, DOI) and
`CHANGELOG.md`, run `python scripts/render_readme.py`, push, publish a release with the tag `vX.Y.Z`.

## Checked locally before a release

```bash
python -m build && twine check dist/*        # sdist and wheel
pip install dist/scada_schema-*.whl          # in a fresh virtual environment
scada-schema --version && scada-schema signals --profile pv
pytest -q                                    # with the open datasets: set SCADA_SCHEMA_<NAME>_DIR
```
