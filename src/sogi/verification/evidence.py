"""Evidence mapping: acceptance criteria to observable verification results.

Sogi never treats "the test suite passed" as proof that every requirement is
met. Each criterion is evaluated independently:

- SATISFIED  — matching test evidence exists and the test checks passed
- VIOLATED   — matching test evidence exists but the test checks failed
- UNVERIFIED — no matching evidence, or matching evidence was never executed

The last case is deliberate: a relevant OAuth test that was never run is an
open question, and reporting it as satisfied would be fabrication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sogi.core.run_record import SATISFIED, UNVERIFIED, VIOLATED, RunRecord

if TYPE_CHECKING:
    from .verifier import CheckResult

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_/-]{2,}")
_STOP_WORDS = {
    "and",
    "are",
    "but",
    "change",
    "does",
    "fix",
    "for",
    "from",
    "have",
    "into",
    "not",
    "remain",
    "remains",
    "that",
    "the",
    "this",
    "with",
}


@dataclass(frozen=True)
class CriterionResult:
    """One acceptance criterion mapped to its verification evidence."""

    criterion: str
    status: str
    evidence: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion": self.criterion,
            "status": self.status,
            "evidence": list(self.evidence),
            "note": self.note,
        }


def criterion_terms(criterion: str) -> tuple[str, ...]:
    """Extract comparable terms from a criterion string."""
    seen: set[str] = set()
    terms: list[str] = []
    for match in _WORD.finditer(criterion.lower()):
        word = match.group(0).strip("/-")
        if word in _STOP_WORDS or word in seen:
            continue
        seen.add(word)
        terms.append(word)
    return tuple(terms)


def _matches(path: str, terms: tuple[str, ...]) -> bool:
    lowered = path.lower()
    return any(term in lowered for term in terms)


def _looks_like_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in path


def candidate_tests(record: RunRecord) -> tuple[str, ...]:
    """Return the run's known test files from compiled context."""
    if record.context is None:
        return ()
    if record.context.related_tests:
        return record.context.related_tests
    return tuple(path for path in record.context.related_files if _looks_like_test(path))


def matching_evidence(record: RunRecord, terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return known test files whose paths overlap with a criterion's terms.

    Deliberately strict: a test file with no term overlap is not claimed as
    evidence — inventing relevance would be fabrication, not verification.
    """
    candidates = candidate_tests(record)
    return tuple(path for path in candidates if _matches(path, terms))


def map_criteria(
    record: RunRecord,
    results: tuple[CheckResult, ...],
) -> tuple[CriterionResult, ...]:
    """Map every acceptance criterion to SATISFIED / VIOLATED / UNVERIFIED.

    Node-level evidence takes precedence: when structured reports captured
    executed test identities, a criterion is SATISFIED only if a matching
    test actually executed and passed — a passing suite whose relevant tests
    were never collected proves nothing.
    """
    executed_tests = tuple(item for result in results for item in result.executed_tests)
    test_results = [result for result in results if result.check.kind == "test"]
    executed_results = [result for result in test_results if result.success is not None]
    tests_executed = bool(executed_results)
    tests_passed = tests_executed and all(result.success for result in executed_results)

    mapped: list[CriterionResult] = []
    for criterion in record.task.acceptance_criteria:
        terms = criterion_terms(criterion)
        evidence_paths = matching_evidence(record, terms)
        node_evidence = _matching_nodes(executed_tests, terms)

        if node_evidence:
            skipped_nodes = [item for item in node_evidence if item.outcome == "skipped"]
            failed_nodes = [item for item in node_evidence if item.outcome in ("failed", "error")]
            if failed_nodes:
                mapped.append(
                    CriterionResult(
                        criterion=criterion,
                        status=VIOLATED,
                        evidence=tuple(item.nodeid for item in node_evidence),
                        note="Matching executed test(s) failed.",
                    )
                )
            elif skipped_nodes and len(skipped_nodes) == len(node_evidence):
                mapped.append(
                    CriterionResult(
                        criterion=criterion,
                        status=UNVERIFIED,
                        evidence=tuple(item.nodeid for item in node_evidence),
                        note="Matching test(s) exist but were skipped.",
                    )
                )
            else:
                mapped.append(
                    CriterionResult(
                        criterion=criterion,
                        status=SATISFIED,
                        evidence=tuple(item.nodeid for item in node_evidence),
                    )
                )
            continue

        # No structured identities: fall back to file-level mapping.
        if not evidence_paths:
            mapped.append(
                CriterionResult(
                    criterion=criterion,
                    status=UNVERIFIED,
                    note="No matching test evidence identified.",
                )
            )
        elif not tests_executed:
            mapped.append(
                CriterionResult(
                    criterion=criterion,
                    status=UNVERIFIED,
                    evidence=evidence_paths,
                    note="Relevant test exists but was not executed.",
                )
            )
        elif tests_passed:
            mapped.append(
                CriterionResult(
                    criterion=criterion,
                    status=SATISFIED,
                    evidence=evidence_paths,
                    note="File-level evidence only (no structured test report).",
                )
            )
        else:
            mapped.append(
                CriterionResult(
                    criterion=criterion,
                    status=VIOLATED,
                    evidence=evidence_paths,
                )
            )
    return tuple(mapped)


def _matching_nodes(
    executed_tests: tuple, terms: tuple[str, ...]
) -> tuple:
    """Executed tests whose node ids overlap a criterion's terms."""
    if not terms:
        return ()
    matched = tuple(
        item for item in executed_tests if any(term in item.nodeid.lower() for term in terms)
    )
    return matched
