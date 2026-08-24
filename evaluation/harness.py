"""Runs one policy over one batch and returns everything needed to score it.

## Why a discrete-event loop rather than a per-payment loop

The obvious implementation walks the failures one at a time and runs each
payment's recovery to completion before starting the next. That is simpler and
it is wrong, because two of the constraints that matter most are *shared*:

  * a customer's weekly contact cap spans every payment they have with us
  * the batch recovery budget and ops escalation capacity are global

Running payment A's full four-day recovery before touching payment B -- which
failed an hour after A -- makes that bookkeeping time-inconsistent, and the
contact caps stop meaning anything. So every payment shares one clock and one
priority queue, and interventions interleave the way they would in production.

## Fairness

`World.reset()` and `ObservationBuilder.reset()` run before every policy, so
each meets an identical batch with identical customer state. Combined with the
world's common random numbers -- draws keyed by `(payment_id, purpose, attempt)`
rather than streamed -- two policies face the same luck at the same decision
points. Any difference in the result is a difference in judgement.
"""

from __future__ import annotations

import heapq
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agent.audit import AuditLog, make_entry
from agent.observe import Observation
from agent.policy import Limits, PolicyEngine, Proposal, Ruling, Verdict
from core.actions import ACTIVE_ACTIONS, Action
from economics.costs import DEFAULT, CostModel, Ledger
from evaluation.adapter import ObservationBuilder
from evaluation.baselines import Policy
from sim.taxonomy import RecoveryClass
from sim.world import FailureEvent, World

MAX_EVENTS_PER_PAYMENT = 8
"""Safety valve. A policy that never stops would otherwise loop forever; this
bounds it and the breach is reported rather than swallowed."""


@dataclass
class RunResult:
    policy_name: str
    engine: str
    enforce_guardrails: bool
    uses_llm: bool

    ledger: Ledger
    audit: AuditLog

    decisions: int = 0
    payments_touched: int = 0
    runaway_payments: int = 0
    decision_failures: int = 0

    correct_class: int = 0
    classified: int = 0
    correct_class_heldout: int = 0
    classified_heldout: int = 0
    correct_class_ambiguous: int = 0
    classified_ambiguous: int = 0

    llm_usage: dict | None = None
    policy_report: dict = field(default_factory=dict)

    def class_accuracy(self) -> float:
        return self.correct_class / self.classified if self.classified else 0.0

    def heldout_accuracy(self) -> float:
        return (
            self.correct_class_heldout / self.classified_heldout
            if self.classified_heldout
            else 0.0
        )

    def ambiguous_accuracy(self) -> float:
        return (
            self.correct_class_ambiguous / self.classified_ambiguous
            if self.classified_ambiguous
            else 0.0
        )


@dataclass(order=True)
class _Event:
    when: datetime
    seq: int
    payment_id: str = field(compare=False)
    pending: tuple[Observation, Ruling] | None = field(compare=False, default=None)


def run_policy(
    policy: Policy,
    world: World,
    failures: list[FailureEvent],
    customer_history: dict,
    limit: int | None = None,
    costs: CostModel | None = None,
    limits: Limits | None = None,
    batch_budget_paise: int | None = None,
    wave: int = 1,
    max_workers: int = 12,
) -> RunResult:
    """Run `policy` over the batch. `wave > 1` decides concurrently (see below)."""
    costs = costs or DEFAULT
    batch = failures[:limit] if limit else failures

    world.reset()
    builder = ObservationBuilder(world, batch, customer_history)
    engine = PolicyEngine(limits=limits, costs=costs, batch_budget_paise=batch_budget_paise)

    ledger = Ledger()
    audit = AuditLog(policy_name=policy.name, engine=policy.engine)
    result = RunResult(
        policy_name=policy.name,
        engine=policy.engine,
        enforce_guardrails=policy.enforce_guardrails,
        uses_llm=policy.uses_llm,
        ledger=ledger,
        audit=audit,
    )

    events: dict[str, FailureEvent] = {e.payment_id: e for e in batch}
    attempts: dict[str, int] = {pid: 0 for pid in events}
    contacts: dict[str, int] = {pid: 0 for pid in events}
    seen: dict[str, int] = {pid: 0 for pid in events}
    done: set[str] = set()

    queue: list[_Event] = []
    counter = 0
    for ev in batch:
        counter += 1
        heapq.heappush(queue, _Event(ev.failed_at, counter, ev.payment_id))

    pool = ThreadPoolExecutor(max_workers=max_workers) if wave > 1 else None

    try:
        while queue:
            # Pull a wave of due events. wave=1 is exact sequential simulation.
            # wave>1 decides concurrently, which is what makes an LLM run
            # finish in minutes rather than half an hour. The approximation:
            # payments inside one wave all observe the state as of the start of
            # the wave, so a customer with two failures in the same wave sees
            # the pre-wave contact count for both. Rare, and it can only ever
            # make a policy look *worse* on contact caps, never better -- so it
            # cannot flatter our own agent.
            popped: list[_Event] = []
            while queue and len(popped) < wave:
                popped.append(heapq.heappop(queue))

            to_decide = [e for e in popped if e.pending is None and e.payment_id not in done]

            observations: dict[str, Observation] = {}
            for e in to_decide:
                ev = events[e.payment_id]
                observations[e.payment_id] = builder.build(
                    ev,
                    now=e.when,
                    attempts_made=attempts[e.payment_id],
                    contacts_made=contacts[e.payment_id],
                    budget_remaining_paise=(
                        batch_budget_paise - engine.spent_paise
                        if batch_budget_paise is not None
                        else 10**12
                    ),
                )

            if pool and len(to_decide) > 1:
                proposals = dict(
                    zip(
                        (e.payment_id for e in to_decide),
                        pool.map(
                            policy.decide, [observations[e.payment_id] for e in to_decide]
                        ),
                    )
                )
            else:
                proposals = {
                    e.payment_id: policy.decide(observations[e.payment_id]) for e in to_decide
                }

            for e in popped:
                if e.payment_id in done:
                    continue

                if e.pending is not None:
                    obs, ruling = e.pending
                else:
                    obs = observations[e.payment_id]
                    proposal = proposals[e.payment_id]
                    ruling = engine.review(obs, proposal)

                    # Count what the guardrails WOULD have stopped, even when
                    # this policy does not enforce them. This is where the
                    # policy_violations column comes from.
                    if ruling.blocked and not policy.enforce_guardrails:
                        ledger.policy_violations += 1

                    if not policy.enforce_guardrails:
                        ruling = _as_proposed(proposal, ruling)

                    if ruling.at > e.when:
                        counter += 1
                        heapq.heappush(
                            queue, _Event(ruling.at, counter, e.payment_id, (obs, ruling))
                        )
                        continue

                _apply(
                    result,
                    world,
                    builder,
                    engine,
                    ledger,
                    audit,
                    costs,
                    obs,
                    ruling,
                    attempts,
                    contacts,
                    done,
                )

                seen[e.payment_id] += 1
                if seen[e.payment_id] >= MAX_EVENTS_PER_PAYMENT:
                    result.runaway_payments += 1
                    done.add(e.payment_id)

                if e.payment_id not in done:
                    counter += 1
                    heapq.heappush(queue, _Event(ruling.at, counter, e.payment_id))
    finally:
        if pool:
            pool.shutdown(wait=True)

    _finalise(result, world, ledger, costs, engine, policy)
    return result


def _as_proposed(proposal: Proposal, ruling: Ruling) -> Ruling:
    """Strip the policy layer's intervention, keeping the measurement."""
    return Ruling(
        verdict=Verdict.ALLOW,
        action=proposal.action,
        at=proposal.at,
        channel=proposal.channel,
        rule=ruling.rule,
        explanation=f"guardrails not enforced for this policy (would have: {ruling.rule})"
        if ruling.blocked
        else "",
        proposed_action=proposal.action,
    )


def _apply(
    result: RunResult,
    world: World,
    builder: ObservationBuilder,
    engine: PolicyEngine,
    ledger: Ledger,
    audit: AuditLog,
    costs: CostModel,
    obs: Observation,
    ruling: Ruling,
    attempts: dict[str, int],
    contacts: dict[str, int],
    done: set[str],
) -> None:
    pid = obs.payment_id
    result.decisions += 1

    _score_classification(result, world, obs, ruling)

    ledger.record_action(ruling.action, ruling.channel, costs)
    engine.commit(ruling)
    cost = costs.action_cost(ruling.action, ruling.channel)

    executed = False
    succeeded = False
    recovered = 0

    if ruling.action in ACTIVE_ACTIONS:
        executed = True
        attempts[pid] += 1
        if ruling.action.value in ("nudge", "switch_rail"):
            contacts[pid] += 1
            builder.ledger(obs.customer_id, ruling.at).record_contact(ruling.at)

        truth_class = world.truth_of(pid).true_class
        if truth_class in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX):
            ledger.wasted_attempts += 1
        if truth_class == RecoveryClass.HARD_STOP:
            # Re-presenting a risk decline carries regulatory exposure, not just
            # waste. Charged only for HARD_STOP: retrying our own integration
            # bug is merely stupid, not a compliance problem.
            ledger.compliance_exposure_paise += costs.compliance_exposure_paise

        outcome = world.execute(pid, ruling.action, ruling.at, ruling.channel)
        succeeded = outcome.succeeded
        if succeeded:
            recovered = outcome.amount_recovered_paise
            ledger.record_recovery(recovered, costs)
            done.add(pid)
        if outcome.customer_churned:
            ledger.churn_cost_paise += costs.churn_paise
    else:
        # ESCALATE and STOP end this payment's recovery either way.
        world.execute(pid, ruling.action, ruling.at, None)
        done.add(pid)

    audit.append(
        make_entry(
            seq=audit.next_seq(),
            obs=obs,
            decision_class=ruling.diagnosed_class,
            confidence=ruling.confidence,
            proposed=Action(ruling.proposed_action),
            rationale=ruling.rationale,
            verdict=ruling.verdict.value,
            rule=ruling.rule,
            policy_explanation=ruling.explanation,
            final=ruling.action,
            scheduled_at=ruling.at,
            channel=ruling.channel,
            executed=executed,
            succeeded=succeeded,
            recovered_paise=recovered,
            cost_paise=cost,
        )
    )


def _score_classification(
    result: RunResult, world: World, obs: Observation, ruling: Ruling
) -> None:
    """Score the diagnosis separately from the plan.

    Only the first decision on a payment counts, so a policy cannot inflate its
    accuracy by revisiting easy payments repeatedly.
    """
    if obs.attempts_made > 0:
        return
    diagnosed = ruling.diagnosed_class
    if not diagnosed or diagnosed in ("unknown", "n/a"):
        return

    truth = world.truth_of(obs.payment_id)
    correct = diagnosed == truth.true_class.value

    result.classified += 1
    result.correct_class += int(correct)

    if truth.is_ambiguous:
        result.classified_ambiguous += 1
        result.correct_class_ambiguous += int(correct)

    from agent.knowledge import KNOWN_REASONS

    if obs.reason not in KNOWN_REASONS:
        result.classified_heldout += 1
        result.correct_class_heldout += int(correct)


def _finalise(
    result: RunResult,
    world: World,
    ledger: Ledger,
    costs: CostModel,
    engine: PolicyEngine,
    policy: Policy,
) -> None:
    annoyance = sum(c.annoyance for c in world.pop.customers.values())
    ledger.annoyance_cost_paise = costs.annoyance_cost(annoyance)

    result.payments_touched = len({e.payment_id for e in result.audit.entries})
    result.policy_report = engine.report()
    result.decision_failures = getattr(policy, "decision_failures", 0)

    client = getattr(policy, "client", None)
    if client is not None:
        result.llm_usage = client.usage.summary()
