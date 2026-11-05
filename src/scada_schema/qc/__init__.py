"""Data quality control for canonical tables, with an HTML report.

    report = qc.run(df, meta)
    report.summary()          # one row per (check, signal)
    report.to_html("qc.html")

The checks are listed in [`scada_schema.qc.checks`][scada_schema.qc.checks]; thresholds are in
[`QCConfig`][scada_schema.qc.core.QCConfig].
"""

from scada_schema.qc import checks as _checks  # noqa: F401 - registers the checks
from scada_schema.qc.core import CHECKS, Finding, QCConfig, QCReport, run

__all__ = ["CHECKS", "Finding", "QCConfig", "QCReport", "run"]
