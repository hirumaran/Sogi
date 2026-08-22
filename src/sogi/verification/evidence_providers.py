"""Structured verification evidence providers.

Filename matching is preliminary evidence, not proof. A criterion should
become SATISFIED only when Sogi can connect it to *executed* evidence: which
tests actually ran, and whether they passed.

The :class:`TestReportProvider` wraps pytest-style commands with a JUnit XML
report and parses exact executed test identities (node ids and outcomes).
Providers degrade honestly: when no structured report is available the
evidence set is empty and criteria fall back to UNVERIFIED rather than being
claimed as satisfied.
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: Marker inserted into pytest commands so the report is written somewhere
#: deterministic without clobbering repository state.
_JUNIT_FLAG = "--junitxml"


@dataclass(frozen=True)
class ExecutedTest:
    """One test identity observed in a structured execution report."""

    nodeid: str
    outcome: str  # passed | failed | error | skipped

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"


def pytest_command_wants_report(command: str) -> bool:
    """True when the command is pytest-like and lacks an explicit report."""
    return bool(command) and "pytest" in command.lower() and _JUNIT_FLAG not in command


def instrument_pytest_command(command: str, report_path: Path) -> str:
    """Append a JUnit XML report flag to a pytest command."""
    return f"{command} {_JUNIT_FLAG}={report_path} -p no:cacheprovider"


def parse_junit_xml(path: Path) -> tuple[ExecutedTest, ...]:
    """Extract executed test identities from a JUnit XML report."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return ()
    tests: list[ExecutedTest] = []
    for case in tree.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        nodeid = _to_nodeid(classname, name)
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        tests.append(ExecutedTest(nodeid=nodeid, outcome=outcome))
    return tuple(tests)


def _to_nodeid(classname: str, name: str) -> str:
    # JUnit classnames are dotted module paths; reconstruct a pytest-ish
    # node id so downstream term matching sees familiar shapes.
    base = classname if "/" in classname else classname.replace(".", "/")
    suffix = ".py" if base and not base.endswith(".py") else ""
    return f"{base}{suffix}::{name}" if base else name


def make_temp_report() -> Path:
    import os

    descriptor, name = tempfile.mkstemp(suffix=".xml")
    os.close(descriptor)  # pytest creates the file; we only need the path
    return Path(name)
