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

    @property
    def delay(self) -> str:
        """How long after the decision the action was scheduled to run."""
        mins = (self.scheduled_at - self.ts).total_seconds() / 60.0
        if mins < 1:
            return "now"
        if mins < 60:
            return f"+{mins:.0f}m"
        if mins < 48 * 60:
            return f"+{mins / 60:.1f}h"
        return f"+{mins / 1440:.1f}d"

    def render(self) -> str:
        """Five labelled lines. Written to be read aloud over a demo.

        Note the two timestamps. The decision and the action are separated in
        time -- often by hours or days -- and collapsing them into one field
        made the log appear out of order, because entries are sequenced by when
        they *ran* while the obvious timestamp to print is when they were
        *decided*. Both are shown.
        """
        money = f"Rs {self.amount_paise / 100:,.0f}"
        cost = f"Rs {self.cost_paise / 100:.2f}"

        head = (
            f"[{self.seq:04d}] {self.payment_id}  {money:>12}  "
            f"{self.reason}"
        )
        diag = (
            f"    decided   {self.ts:%d %b %H:%M}  "
            f"{self.diagnosed_class or 'n/a'} (conf {self.confidence:.2f})"
        )
        prop = f"    proposed  {self.proposed_action}  {self.delay}"
        if self.channel:
            prop += f" via {self.channel}"

        if self.was_overruled:
            ruling = (
                f"    RULING    {self.verdict.upper()} [{self.rule}] "
                f"-> {self.final_action}\n"
                f"              {self.policy_explanation}"
            )
        elif self.verdict == "defer":
            ruling = (
                f"    RULING    DEFERRED [{self.rule}] "
                f"-> {self.scheduled_at:%d %b %H:%M}\n"
                f"              {self.policy_explanation}"
            )
        else:
            ruling = "    ruling    allowed"

        when = f"{self.scheduled_at:%d %b %H:%M}"
        if self.succeeded:
            result = f"    RESULT    {when}  RECOVERED {money}  ({cost})"
        elif self.executed:
            result = f"    result    {when}  no recovery  ({cost})"
        elif self.final_action == "escalate":
            result = f"    result    {when}  escalated to a human  ({cost})"
        else:
            result = f"    result    {when}  stopped  ({cost})"

        return "\n".join([head, diag, prop, ruling, result])

    def render_compact(self) -> str:
        flag = "!" if self.was_overruled else ("$" if self.succeeded else " ")
        return (
            f"{flag}[{self.seq:04d}] {self.scheduled_at:%d %b %H:%M} "
            f"{self.payment_id} {self.amount_paise / 100:>8,.0f} "
            f"{self.reason[:26]:<26} {self.final_action:<11} "
            f"{self.rule}"
        )


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

    def render(
        self,
        limit: int | None = None,
        only_overruled: bool = False,
        compact: bool = False,
    ) -> str:
        rows = self.overruled() if only_overruled else self.entries
        # Order by when the action ran, not when it was decided. A recovery
        # scheduled for next Tuesday belongs next Tuesday in the log, and
        # sequencing by decision time made the trail look scrambled.
        rows = sorted(rows, key=lambda e: e.scheduled_at)
        if limit:
            rows = rows[:limit]
        if compact:
            return "\n".join(r.render_compact() for r in rows)
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
