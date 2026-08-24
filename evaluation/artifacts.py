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


def _primary(results: list[RunResult]) -> RunResult:
    """The policy the console is about: the agent, or the best available."""
    for name in ("agent", "rules_engine", "fixed_retry"):
        for r in results:
            if r.policy_name == name:
                return r
    return results[0]


def workflow(r: RunResult) -> dict:
    """Per-payment lifecycles: the operational view, not the research log.

    The audit trail is one row per *decision*. An operator does not think in
    decisions, they think in payments: what failed, what did the agent conclude,
    what is it going to do, when, and does anything need me. So the entries are
    folded back into payment lifecycles here, in Python, where the metrics
    already live -- the web layer stays a renderer and never re-derives numbers.

    States mirror the brief's own vocabulary: recovered / needs a human /
    stopped deliberately / still scheduled.
    """
    by_payment: dict[str, list] = {}
    for e in r.audit.entries:
        by_payment.setdefault(e.payment_id, []).append(e)

    payments = []
    for pid, entries in by_payment.items():
        entries.sort(key=lambda x: x.scheduled_at)
        first, last = entries[0], entries[-1]

        recovered = next((e for e in entries if e.succeeded), None)
        if recovered:
            state = "recovered"
        elif last.final_action == "escalate":
            state = "needs_human"
        elif last.final_action == "stop":
            state = "stopped"
        else:
            state = "scheduled"

        payments.append(
            {
                "payment_id": pid,
                "customer_id": first.customer_id,
                "amount_paise": first.amount_paise,
                "reason": first.reason,
                "description": first.description,
                "method": first.method,
                "issuer": first.issuer,
                "is_subscription": first.is_subscription,
                "failed_at": first.failed_at.isoformat() if first.failed_at else None,
                "diagnosis": first.diagnosed_class,
                "confidence": first.confidence,
                "rationale": first.rationale,
                "state": state,
                "recovered_paise": recovered.recovered_paise if recovered else 0,
                "cost_paise": sum(e.cost_paise for e in entries),
                "attempts": sum(1 for e in entries if e.executed),
                # The reason a human is being asked to look, in their words not
                # ours -- an escalation with no stated cause is just a queue.
                "handoff": (
                    last.policy_explanation
                    if state in ("needs_human", "stopped") and last.was_overruled
                    else ""
                ),
                "blocked_by": last.rule if last.was_overruled else "",
                "steps": [
                    {
                        "at": e.scheduled_at.isoformat(),
                        "action": e.final_action,
                        "proposed": e.proposed_action,
                        "channel": e.channel,
                        "rule": e.rule,
                        "verdict": e.verdict,
                        "executed": e.executed,
                        "succeeded": e.succeeded,
                        "cost_paise": e.cost_paise,
                        "explanation": e.policy_explanation,
                    }
                    for e in entries
                ],
            }
        )

    order = {"needs_human": 0, "scheduled": 1, "recovered": 2, "stopped": 3}
    payments.sort(key=lambda p: (order.get(p["state"], 9), -p["amount_paise"]))

    at_risk = sum(p["amount_paise"] for p in payments if p["state"] != "recovered")
    return {
        "policy": r.policy_name,
        "engine": r.engine,
        "is_stub": bool(r.uses_llm and r.engine == "stub"),
        "totals": {
            "payments": len(payments),
            "at_risk_paise": at_risk,
            "recovered_paise": sum(p["recovered_paise"] for p in payments),
            "spent_paise": sum(p["cost_paise"] for p in payments),
            "needs_human": sum(1 for p in payments if p["state"] == "needs_human"),
            "stopped": sum(1 for p in payments if p["state"] == "stopped"),
            "recovered_count": sum(1 for p in payments if p["state"] == "recovered"),
            "blocked_actions": len(r.audit.overruled()),
        },
        "vetoes_by_rule": r.audit.vetoes_by_rule(),
        "payments": payments,
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
        # The operational view, built for the primary policy: the agent if it
        # ran, otherwise the best real one. This is what the recovery console
        # renders -- the evaluation is the evidence behind it, not the product.
        "workflow": workflow(_primary(results)),
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
