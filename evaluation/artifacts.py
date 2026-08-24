"""Persist run results as JSON, so the web layer never imports the simulator.

The dashboard is a separate process in a different language. That is not
incidental -- it means the thing a judge looks at is reading the same recorded
artefact anyone else can read, rather than re-deriving numbers live from objects
only Python can see. An artefact on disk can be diffed, checked into the repo,
and disagreed with.

Every artefact records the engine that produced it and whether guardrails were
enforced, because a results file that does not say what produced it is worse
than no results file.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

from evaluation.harness import RunResult

RUNS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "runs"


def _ledger(r: RunResult) -> dict:
    led = r.ledger
    return {
        "gross_recovered_paise": led.gross_recovered_paise,
        "mdr_paise": led.mdr_paise,
        "attempt_cost_paise": led.attempt_cost_paise,
        "message_cost_paise": led.message_cost_paise,
        "escalation_cost_paise": led.escalation_cost_paise,
        "annoyance_cost_paise": led.annoyance_cost_paise,
        "churn_cost_paise": led.churn_cost_paise,
        "compliance_exposure_paise": led.compliance_exposure_paise,
        "spend_paise": led.spend_paise(),
        "net_direct_paise": led.net_direct_paise(),
        "net_paise": led.net_paise(),
        "retries": led.retries,
        "switches": led.switches,
        "nudges": led.nudges,
        "escalations": led.escalations,
        "stops": led.stops,
        "recovered_count": led.recovered_count,
        "wasted_attempts": led.wasted_attempts,
        "policy_violations": led.policy_violations,
    }


def result_to_dict(r: RunResult) -> dict:
    return {
        "policy": r.policy_name,
        "engine": r.engine,
        "enforce_guardrails": r.enforce_guardrails,
        "uses_llm": r.uses_llm,
        # A stub result must be self-identifying wherever it travels. The
        # dashboard reads this flag and refuses to render it as a model result.
        "is_stub": bool(r.uses_llm and r.engine == "stub"),
        "ledger": _ledger(r),
        "decisions": r.decisions,
        "payments_touched": r.payments_touched,
        "runaway_payments": r.runaway_payments,
        "decision_failures": r.decision_failures,
        "diagnosis": {
            "overall": r.class_accuracy(),
            "overall_n": r.classified,
            "heldout": r.heldout_accuracy(),
            "heldout_n": r.classified_heldout,
            "ambiguous": r.ambiguous_accuracy(),
            "ambiguous_n": r.classified_ambiguous,
        },
        "llm_usage": r.llm_usage,
        "policy_report": r.policy_report,
        "audit_summary": r.audit.summary(),
    }


def save_run(
    results: list[RunResult],
    stats: dict,
    batch_size: int,
    seed: int,
    name: str = "latest",
    audit_limit: int = 400,
) -> pathlib.Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "batch_size": batch_size,
        "batch": {
            "total_value_rupees": stats["total_value_rupees"],
            "recoverable_value_rupees": stats["recoverable_value_rupees"],
            "failures_in_month": stats.get("failures"),
            "success_rate_pct": stats.get("success_rate_pct"),
            "ambiguous_failures": stats.get("ambiguous_failures"),
            "outage_linked_failures": stats.get("outage_linked_failures"),
            "downtime": stats.get("downtime"),
        },
        "policies": [result_to_dict(r) for r in results],
        "audits": {
            r.policy_name: {
                "summary": r.audit.summary(),
                # Blocked decisions first: they are the compliance answer and
                # the most interesting thing in the log, and truncating the
                # trail must never silently drop them.
                "entries": [
                    e.to_json()
                    for e in sorted(
                        r.audit.entries,
                        key=lambda x: (not x.was_overruled, x.scheduled_at),
                    )[:audit_limit]
                ],
                "total_entries": len(r.audit.entries),
            }
            for r in results
        },
    }

    path = RUNS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def save_sensitivity(points, subject: str, name: str = "sensitivity") -> pathlib.Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "points": [
            {
                "prob_scale": p.prob_scale,
                "annoyance_mult": p.annoyance_mult,
                "exposure_mult": p.exposure_mult,
                "nets": p.nets,
                "winner": p.winner,
            }
            for p in points
        ],
    }
    path = RUNS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load(name: str = "latest") -> dict | None:
    path = RUNS_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(p.stem for p in RUNS_DIR.glob("*.json"))
