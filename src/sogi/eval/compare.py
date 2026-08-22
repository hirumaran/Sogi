"""Arm comparison: derived view over raw JSONL trial results."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArmSummary:
    arm: str
    trials: int = 0
    successes: int = 0
    verified_trials: int = 0
    verified_successes: int = 0
    mean_duration: float | None = None
    total_input_tokens: int | None = None  # None when the host reported nothing
    total_output_tokens: int | None = None
    total_cost_usd: float | None = None
    verification_outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.successes / self.trials if self.trials else 0.0

    @property
    def verified_success_rate(self) -> float | None:
        if not self.verified_trials:
            return None
        return self.verified_successes / self.verified_trials

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "trials": self.trials,
            "agent_exit_success_rate": round(self.success_rate, 4),
            # Kept for compatibility with existing JSON consumers. This is
            # process success, not task correctness.
            "success_rate": round(self.success_rate, 4),
            "verified_trials": self.verified_trials,
            "verified_success_rate": (
                round(self.verified_success_rate, 4)
                if self.verified_success_rate is not None
                else None
            ),
            "verified_success_ci95": _wilson_interval(
                self.verified_successes, self.verified_trials
            ),
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
        verified = item.get("verified_success")
        if verified is None and item.get("verification_outcome") is not None:
            verified = item.get("verification_outcome") == "PASS"
        if verified is not None:
            summary.verified_trials += 1
            if verified is True:
                summary.verified_successes += 1
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


def _wilson_interval(successes: int, trials: int) -> list[float] | None:
    """95% Wilson score interval for a binomial verified-success rate."""
    if trials <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / trials + z * z / (4 * trials * trials))
        / denominator
    )
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


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
    if baseline.verified_success_rate is not None and sogi.verified_success_rate is not None:
        report["verified_success_delta"] = round(
            sogi.verified_success_rate - baseline.verified_success_rate, 4
        )
    else:
        report["verified_success_delta"] = None
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
        verified_rate = data.get("verified_success_rate")
        if verified_rate is None:
            verified = "verified success unavailable"
        else:
            interval = data.get("verified_success_ci95")
            verified = (
                f"verified success {verified_rate:.1%} (95% CI {interval[0]:.1%}–{interval[1]:.1%})"
            )
        lines.append(f"  {data['arm']}: {data['trials']} trials, {verified}")
        lines.append(f"    agent exit success {data['agent_exit_success_rate']:.1%}")
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
