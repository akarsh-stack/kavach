"""The five policies. Three need no model; two do.

The comparison only means something if the opponents are real, so each of these
is the best honest version of its idea rather than a foil built to lose.

  1. `no_retry`     -- the floor. What happens if you do nothing.
  2. `fixed_retry`  -- what a great many merchants actually run: retry
                       everything three times on a backoff. No model, no rules,
                       no guardrails.
  3. `rules_engine` -- **the real opponent.** Razorpay's own published "Next
                       Steps", competently implemented, with the same guardrails
                       our agent gets. Beating this is the whole task.
  4. `naive_llm`    -- an LLM, the error string, and "recover the revenue". The
                       typical hackathon submission. No guardrails.
  5. `agent`        -- ours.

## On guardrails not being uniform

`fixed_retry` and `naive_llm` run without the policy layer, because running
without one is the defining characteristic of what they represent. That is not a
rigged comparison as long as it is stated, and it is stated everywhere the
results appear.

Crucially, the policy engine still *evaluates* their proposals even when it is
not enforcing them, so every action that would have been blocked is counted.
That is where the `policy_violations` column comes from, and it is the number
that shows what those policies actually cost: not lower recovery, but retried
risk declines and over-contacted customers.

We also run `agent_no_guardrails` as an ablation, so the guardrails' price is
visible rather than assumed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from agent.decide import NAIVE_SYSTEM_PROMPT, SYSTEM_PROMPT, Decision, build_user_message
from agent.knowledge import documented_class
from agent.llm import FatalLLMError, LLMClient, LLMUnavailable
from agent.observe import Observation
from agent.policy import Proposal
from core.actions import Action, Channel


class Policy(ABC):
    name: str = "unnamed"
    engine: str = "rules"
    enforce_guardrails: bool = True
    uses_llm: bool = False

    @abstractmethod
    def decide(self, obs: Observation) -> Proposal: ...

    def _p(
        self,
        obs: Observation,
        action: Action,
        delay_hours: float = 0.0,
        channel: Channel | None = None,
        rationale: str = "",
        recovery_class: str = "",
        confidence: float = 1.0,
    ) -> Proposal:
        from datetime import timedelta

        return Proposal(
            payment_id=obs.payment_id,
            action=action,
            at=obs.now + timedelta(hours=delay_hours),
            channel=channel,
            rationale=rationale,
            confidence=confidence,
            diagnosed_class=recovery_class,
        )


# ---------------------------------------------------------------------------
# 1. The floor
# ---------------------------------------------------------------------------


class NoRetryPolicy(Policy):
    """Do nothing. Establishes what the failures cost if left alone.

    Every other policy's recovery is measured against this, and its net is
    exactly zero by construction -- no revenue, but also no spend.
    """

    name = "no_retry"
    engine = "none"

    def decide(self, obs: Observation) -> Proposal:
        return self._p(
            obs, Action.STOP, rationale="no recovery attempted", recovery_class="n/a"
        )


# ---------------------------------------------------------------------------
# 2. The cron job
# ---------------------------------------------------------------------------


class FixedRetryPolicy(Policy):
    """Retry everything three times on a backoff, regardless of why it failed.

    Not a strawman -- this is genuinely what a large number of merchants run,
    because it is the thing you build in an afternoon. It recovers a real amount
    of money. It also re-presents expired cards, retries risk declines, and
    keeps hammering payments that were never going to succeed, which is what
    the cost column is for.
    """

    name = "fixed_retry"
    engine = "none"
    enforce_guardrails = False

    BACKOFF_HOURS = (1.0, 6.0, 24.0)

    def decide(self, obs: Observation) -> Proposal:
        if obs.attempts_made >= len(self.BACKOFF_HOURS):
            return self._p(obs, Action.STOP, rationale="retry schedule exhausted")
        delay = self.BACKOFF_HOURS[obs.attempts_made]
        return self._p(
            obs,
            Action.RETRY,
            delay_hours=delay,
            rationale=f"fixed backoff, attempt {obs.attempts_made + 1} at +{delay:g}h",
            recovery_class="unknown",
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# 3. The real opponent
# ---------------------------------------------------------------------------


class RulesEnginePolicy(Policy):
    """Razorpay's published guidance, competently implemented.

    This is the policy our agent has to beat, and it is deliberately good. It
    reads the same public rulebook the agent gets, maps each documented reason
    to the right action, escalates risk blocks and integration bugs correctly,
    and waits a sensible interval before each kind of retry.

    Two things it cannot do, which is precisely where the model has to earn its
    place:

      * **Unmapped reasons.** Roughly one failure in forty carries a reason the
        rulebook has no entry for. It falls back to a single cautious retry --
        which is what a real rulebook does, and is right about half the time.

      * **Timing that depends on the customer.** It waits a flat 48 hours on a
        funds failure. It cannot know that this particular customer is paid on
        the 2nd, or that a subscription renewal will tolerate a five-day wait
        while an impulse purchase will not.
    """

    name = "rules_engine"
    engine = "rules"

    PLAN: dict[str, tuple[Action, float, Channel | None]] = {
        # Give a transient fault time to clear before re-presenting.
        "retry_same": (Action.RETRY, 0.5, None),
        # Flat two days. Better than nothing, blind to the salary cycle.
        "retry_later_funds": (Action.RETRY, 48.0, None),
        "switch_rail": (Action.SWITCH_RAIL, 1.0, Channel.SMS),
        "nudge_customer": (Action.NUDGE, 1.0, Channel.SMS),
        "hard_stop": (Action.ESCALATE, 0.0, None),
        "merchant_fix": (Action.ESCALATE, 0.0, None),
    }

    def decide(self, obs: Observation) -> Proposal:
        klass = documented_class(obs.reason)

        if klass is None:
            # No entry in the rulebook. A real ops team retries once and gives
            # up rather than guessing.
            if obs.attempts_made == 0:
                return self._p(
                    obs,
                    Action.RETRY,
                    delay_hours=2.0,
                    rationale=f"'{obs.reason}' not in rulebook; one cautious retry",
                    recovery_class="unknown",
                    confidence=0.2,
                )
            return self._p(
                obs,
                Action.STOP,
                rationale=f"'{obs.reason}' not in rulebook; no mapping to act on",
                recovery_class="unknown",
                confidence=0.2,
            )

        action, delay, channel = self.PLAN[klass]

        # Escalate once, then stop -- do not queue the same payment repeatedly.
        if action == Action.ESCALATE and obs.attempts_made > 0:
            return self._p(obs, Action.STOP, rationale="already escalated", recovery_class=klass)

        if obs.attempts_made >= 3:
            return self._p(obs, Action.STOP, rationale="attempt cap", recovery_class=klass)

        return self._p(
            obs,
            action,
            delay_hours=delay * (1 + obs.attempts_made),
            channel=channel,
            rationale=f"documented next step for '{obs.reason}' -> {klass}",
            recovery_class=klass,
            confidence=0.85,
        )


# ---------------------------------------------------------------------------
# 4 & 5. The model-driven policies
# ---------------------------------------------------------------------------

_ACTION_BY_NAME = {a.value: a for a in Action}
_CHANNEL_BY_NAME = {c.value: c for c in Channel}


class LLMPolicy(Policy):
    """Shared machinery for the two model-driven policies."""

    uses_llm = True

    def __init__(self, client: LLMClient, system_prompt: str, name: str) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.name = name
        self.engine = client.engine
        self.decision_failures = 0

    def decide(self, obs: Observation) -> Proposal:
        try:
            d: Decision = self.client.complete(
                self.system_prompt, build_user_message(obs), Decision
            )
        except FatalLLMError:
            # Deliberately NOT caught -- quota exhaustion, a stale replay cache,
            # anything that means this run cannot produce valid results.
            # Continuing would yield a complete set of numbers in which the
            # agent quietly did nothing.
            raise
        except LLMUnavailable:
            # A model failure must not silently become a recovery decision.
            # Stopping is the safe default and it is recorded as a failure so
            # the report can say how often it happened.
            self.decision_failures += 1
            return self._p(
                obs,
                Action.STOP,
                rationale="model unavailable; stopped rather than guessed",
                recovery_class="unknown",
                confidence=0.0,
            )

        action = _ACTION_BY_NAME.get(d.action, Action.STOP)
        channel = _CHANNEL_BY_NAME.get(d.channel) if d.channel else None
        if action in (Action.NUDGE, Action.SWITCH_RAIL) and channel is None:
            channel = Channel.SMS

        return self._p(
            obs,
            action,
            delay_hours=max(0.0, min(d.delay_hours, 720.0)),
            channel=channel,
            rationale=d.rationale,
            recovery_class=d.recovery_class,
            confidence=d.confidence,
        )


class NaiveLLMPolicy(LLMPolicy):
    """An LLM, the error string, and 'recover the revenue'. No guardrails.

    The honest version of the obvious approach. We expect it to look strong on
    gross recovery -- it will try hard on everything -- and to pay for it in
    wasted attempts, contact-cap violations and retried risk declines.
    """

    enforce_guardrails = False

    def __init__(self, client: LLMClient) -> None:
        super().__init__(client, NAIVE_SYSTEM_PROMPT, "naive_llm")


class RecoveryAgentPolicy(LLMPolicy):
    """Ours: the full prompt, the rulebook, economics, and the policy layer."""

    enforce_guardrails = True

    def __init__(self, client: LLMClient, name: str = "agent") -> None:
        super().__init__(client, SYSTEM_PROMPT, name)


class AgentNoGuardrailsPolicy(RecoveryAgentPolicy):
    """Ablation: the same agent with the policy layer switched off.

    Isolates what the guardrails actually cost and save. If they turn out to
    cost net revenue, that is worth knowing and reporting rather than hiding --
    the argument for them is compliance, not profit, and we should be able to
    say which.
    """

    enforce_guardrails = False

    def __init__(self, client: LLMClient) -> None:
        super().__init__(client, name="agent_no_guardrails")


def build_all(client: LLMClient, include_ablation: bool = True) -> list[Policy]:
    policies: list[Policy] = [
        NoRetryPolicy(),
        FixedRetryPolicy(),
        RulesEnginePolicy(),
        NaiveLLMPolicy(client),
        RecoveryAgentPolicy(client),
    ]
    if include_ablation:
        policies.append(AgentNoGuardrailsPolicy(client))
    return policies


def build_non_llm() -> list[Policy]:
    """The three policies that need no credentials. Real, publishable numbers."""
    return [NoRetryPolicy(), FixedRetryPolicy(), RulesEnginePolicy()]
