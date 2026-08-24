"""The audit trail. Every decision, what was proposed, what was allowed, what happened.

The brief asks for an audit trail, and the usual interpretation is a log of
actions taken. That is the less interesting half. This log records what was
*prevented* too -- the action the model proposed, the rule that blocked it, and
the substitution that ran instead.

That matters for two reasons. Operationally, "we retried 412 payments" is not an
answer to a compliance question; "we declined to retry 62 payments because they
were risk declines, here is each one" is. And for evaluation, the gap between
proposed and executed actions is the only direct measurement of how often the
model would have done something it should not -- which is a number nobody
reports about their own agent, and the one we most wanted to know.

Every entry is append-only and carries enough context to reconstruct the
decision without the simulator.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from core.actions import Action, Channel


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    ts: datetime
    payment_id: str
    customer_id: str
    amount_paise: int
    reason: str

    # -- what the model said -----------------------------------------------
    diagnosed_class: str
    confidence: float
    proposed_action: str
    rationale: str

    # -- what the policy layer ruled ---------------------------------------
    verdict: str
    rule: str
    policy_explanation: str

    # -- what actually ran -------------------------------------------------
    final_action: str
    scheduled_at: datetime
    channel: str | None
    executed: bool
    succeeded: bool
    recovered_paise: int
    cost_paise: int
    note: str = ""

    @property
    def was_overruled(self) -> bool:
        return self.proposed_action != self.final_action

    def to_json(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        d["scheduled_at"] = self.scheduled_at.isoformat()
        return d

    def render(self) -> str:
        """One human-readable line, plus a second when the policy intervened."""
        money = f"Rs{self.amount_paise / 100:,.0f}"
        head = (
            f"[{self.seq:04d}] {self.ts:%d %b %H:%M}  {self.payment_id}  {money:>10}  "
            f"{self.reason}"
        )
        diag = (
            f"         model: {self.diagnosed_class} "
            f"(conf {self.confidence:.2f}) -> {self.proposed_action}"
        )
        if self.was_overruled:
            body = (
                f"         POLICY {self.verdict.upper()} [{self.rule}] "
                f"-> {self.final_action}\n"
                f"                {self.policy_explanation}"
            )
        elif self.verdict == "defer":
            body = (
                f"         POLICY DEFER [{self.rule}] -> {self.scheduled_at:%d %b %H:%M}\n"
                f"                {self.policy_explanation}"
            )
        else:
            body = f"         allowed -> {self.final_action}"

        if self.executed:
            if self.succeeded:
                tail = f"         RECOVERED {money}  (cost Rs{self.cost_paise / 100:.2f})"
            else:
                tail = f"         no recovery  (cost Rs{self.cost_paise / 100:.2f})"
        else:
            tail = f"         not executed  (cost Rs{self.cost_paise / 100:.2f})"

        return "\n".join([head, diag, body, tail])


@dataclass
class AuditLog:
    policy_name: str
    engine: str = "unknown"
    """Which decision engine produced this run: 'anthropic', 'stub', or a
    non-LLM baseline name. Recorded so a set of results can never be
    misattributed to a model that did not produce it."""

    entries: list[AuditEntry] = field(default_factory=list)

    def append(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def next_seq(self) -> int:
        return len(self.entries) + 1

    # -- queries used by the report and the demo ---------------------------

    def overruled(self) -> list[AuditEntry]:
        return [e for e in self.entries if e.was_overruled]

    def vetoes_by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            if e.was_overruled and e.rule:
                out[e.rule] = out.get(e.rule, 0) + 1
        return dict(sorted(out.items()))

    def recoveries(self) -> list[AuditEntry]:
        return [e for e in self.entries if e.succeeded]

    def for_payment(self, payment_id: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.payment_id == payment_id]

    def blocked_risk_retries(self) -> list[AuditEntry]:
        """The compliance answer: attempts on risk declines that never ran."""
        return [e for e in self.entries if e.rule == "R1_RISK_BLOCK"]

    # -- output ------------------------------------------------------------

    def to_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for e in self.entries:
                fh.write(json.dumps(e.to_json()) + "\n")

    def render(self, limit: int | None = None, only_overruled: bool = False) -> str:
        rows = self.overruled() if only_overruled else self.entries
        if limit:
            rows = rows[:limit]
        return "\n\n".join(r.render() for r in rows)

    def summary(self) -> dict[str, object]:
        n = len(self.entries)
        overruled = self.overruled()
        return {
            "policy": self.policy_name,
            "engine": self.engine,
            "decisions": n,
            "executed": sum(1 for e in self.entries if e.executed),
            "recoveries": len(self.recoveries()),
            "overruled": len(overruled),
            "overruled_pct": round(100.0 * len(overruled) / n, 1) if n else 0.0,
            "vetoes_by_rule": self.vetoes_by_rule(),
            "risk_retries_blocked": len(self.blocked_risk_retries()),
        }


def make_entry(
    *,
    seq: int,
    obs,
    decision_class: str,
    confidence: float,
    proposed: Action,
    rationale: str,
    verdict: str,
    rule: str,
    policy_explanation: str,
    final: Action,
    scheduled_at: datetime,
    channel: Channel | None,
    executed: bool,
    succeeded: bool,
    recovered_paise: int,
    cost_paise: int,
    note: str = "",
) -> AuditEntry:
    return AuditEntry(
        seq=seq,
        ts=obs.now,
        payment_id=obs.payment_id,
        customer_id=obs.customer_id,
        amount_paise=obs.amount_paise,
        reason=obs.reason,
        diagnosed_class=decision_class,
        confidence=confidence,
        proposed_action=proposed.value,
        rationale=rationale,
        verdict=verdict,
        rule=rule,
        policy_explanation=policy_explanation,
        final_action=final.value,
        scheduled_at=scheduled_at,
        channel=channel.value if channel else None,
        executed=executed,
        succeeded=succeeded,
        recovered_paise=recovered_paise,
        cost_paise=cost_paise,
        note=note,
    )
