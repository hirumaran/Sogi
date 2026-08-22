"""Arm comparison: derived view over raw JSONL trial results."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArmSummary:
    arm: str
    trials: int = 0
    successes: int = 0
    mean_duration: float | None = None
    total_input_tokens: int | None = None  # None when the host reported nothing
    total_output_tokens: int | None = None
    total_cost_usd: float | None = None
    verification_outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.successes / self.trials if self.trials else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "trials": self.trials,
            "success_rate": round(self.success_rate, 4),
            "mean_duration_seconds": (
                round(self.mean_duration, 3) if self.mean_duration is not None else None
            ),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "verification_outcomes": self.verification_outcomes,
        }


def summarize(arm: str, results: list[dict[str, Any]]) -> ArmSummary:
    summary = ArmSummary(arm=arm)
    durations: list[float] = []
    for item in results:
        if item.get("arm") != arm:
            continue
        summary.trials += 1
        if item.get("success"):
            summary.successes += 1
        durations.append(float(item.get("duration_seconds", 0.0)))
        if item.get("input_tokens") is not None:
            summary.total_input_tokens = (summary.total_input_tokens or 0) + int(
                item["input_tokens"]
            )
        if item.get("output_tokens") is not None:
            summary.total_output_tokens = (summary.total_output_tokens or 0) + int(
                item["output_tokens"]
            )
        if item.get("cost_usd") is not None:
            summary.total_cost_usd = round(
                (summary.total_cost_usd or 0.0) + float(item["cost_usd"]), 6
            )
        outcome = item.get("verification_outcome")
        if outcome:
            summary.verification_outcomes[outcome] = (
                summary.verification_outcomes.get(outcome, 0) + 1
            )
    if durations:
        summary.mean_duration = statistics.fmean(durations)
    return summary


def load_results(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def compare(baseline_path: Path, sogi_path: Path) -> dict[str, Any]:
    baseline = summarize("baseline", load_results(baseline_path))
    sogi = summarize("sogi", load_results(sogi_path))
    report: dict[str, Any] = {"baseline": baseline.to_dict(), "sogi": sogi.to_dict()}
    # Token deltas are only meaningful when both arms reported usage.
    if baseline.total_output_tokens and sogi.total_output_tokens:
        report["output_token_delta_pct"] = round(
            100.0
            * (sogi.total_output_tokens - baseline.total_output_tokens)
            / baseline.total_output_tokens,
            2,
        )
    else:
        report["output_token_delta_pct"] = None
    report["note"] = (
        "Descriptive comparison of raw trials. No performance claim should be "
        "published without repeated trials and uncertainty analysis."
    )
    return report


def render(report: dict[str, Any]) -> str:
    lines = ["EXPERIMENT COMPARISON", "=====================", ""]
    for arm in ("baseline", "sogi"):
        data = report[arm]
        lines.append(
            f"  {data['arm']}: {data['trials']} trials, success {data['success_rate']:.1%}"
        )
        if data.get("mean_duration_seconds") is not None:
            lines.append(f"    mean duration {data['mean_duration_seconds']}s")
        if data.get("verification_outcomes"):
            lines.append(f"    verification: {data['verification_outcomes']}")
    delta = report.get("output_token_delta_pct")
    lines.append("")
    if delta is None:
        lines.append("  token delta: unavailable (usage not reported by host)")
    else:
        lines.append(f"  output-token delta (sogi vs baseline): {delta}%")
    lines.append(f"  note: {report['note']}")
    return "\n".join(lines)
