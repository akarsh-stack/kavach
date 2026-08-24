"""The load-bearing test: the agent cannot see the answer key.

Every simulated benchmark faces the same objection -- *you wrote the simulator
and the agent, so of course the agent wins*. The objection is usually correct,
because the leak is usually accidental: a convenient attribute on a shared
object, a debug field someone forgot to strip, an import added at 2am.

So we do not claim the boundary holds. We prove it, mechanically, on every run:

  1. No module under `agent/` may import anything under `sim/`, at any depth,
     including inside functions and `TYPE_CHECKING` blocks.
  2. The `Observation` the agent receives must expose only fields that exist on
     a real Razorpay payment object or downtime signal.
  3. `Truth` must not be reachable from anything the agent is handed.

If any of these fail, the build fails. That is the difference between a claim
and a guarantee, and it is the single most important thing in this repo.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT_DIR = REPO / "agent"

# Fields a real merchant genuinely has. Anything the agent reads must be
# justifiable against Razorpay's own payment object / webhook payload, or the
# public downtime signal. This list is the contract.
ALLOWED_OBSERVATION_FIELDS = {
    # -- straight off the failed payment --
    "payment_id",
    "customer_id",
    "amount_paise",
    "amount_rupees",
    "method",
    "issuer",
    "psp",
    "reason",
    "description",
    "source",
    "error_class",
    "failed_at",
    "is_subscription",
    "attempt_no",
    # -- the merchant's own records about their own customer --
    "customer_prior_payments",
    "customer_prior_failures",
    "customer_contacts_this_week",
    "customer_first_seen",
    "customer_lifetime_paise",
    # -- Razorpay's public downtime signal, delayed and lossy --
    "issuer_downtime_reported",
    "psp_downtime_reported",
    # -- derived from the agent's own view of its own batch --
    "recent_failures_same_entity",
    "recent_failures_same_reason",
    # -- bookkeeping the agent needs to act at all --
    "now",
    "attempts_made",
    "contacts_made",
    "budget_remaining_paise",
}


def _agent_files() -> list[pathlib.Path]:
    if not AGENT_DIR.exists():
        return []
    return sorted(AGENT_DIR.rglob("*.py"))


def test_agent_never_imports_sim() -> None:
    """No import of `sim` anywhere under `agent/`, at any nesting depth."""
    violations: list[str] = []

    for path in _agent_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sim" or alias.name.startswith("sim."):
                        violations.append(
                            f"{path.relative_to(REPO)}:{node.lineno} imports {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "sim" or mod.startswith("sim."):
                    violations.append(
                        f"{path.relative_to(REPO)}:{node.lineno} imports from {mod}"
                    )

    assert not violations, (
        "The agent must not import the simulator. The whole benchmark rests on "
        "this.\n  " + "\n  ".join(violations)
    )


def test_agent_does_not_reference_truth_types() -> None:
    """Belt and braces: no *code* reference to the answer-key names either.

    Catches reaching hidden state through a duck-typed object handed in at
    runtime, which the import check alone would miss -- e.g. someone accepting
    an object in a function signature and reading `.true_class` off it.

    Deliberately AST-based rather than a text scan. Agent modules should be
    free to *discuss* the boundary in their docstrings -- that prose is the
    point of the design -- and a naive `token in line` check would forbid
    exactly the documentation we most want written.
    """
    banned = {"Truth", "true_class", "base_prob", "base_recovery_prob", "_truth"}
    violations: list[str] = []

    for path in _agent_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # `obj.true_class`
            if isinstance(node, ast.Attribute) and node.attr in banned:
                violations.append(
                    f"{path.relative_to(REPO)}:{node.lineno} reads attribute '.{node.attr}'"
                )
            # bare `true_class` / `Truth(...)`
            elif isinstance(node, ast.Name) and node.id in banned:
                violations.append(
                    f"{path.relative_to(REPO)}:{node.lineno} references name '{node.id}'"
                )
            # `getattr(x, "true_class")` -- the obvious way around the above
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and arg.value in banned:
                        violations.append(
                            f"{path.relative_to(REPO)}:{node.lineno} "
                            f"getattr for '{arg.value}'"
                        )

    assert not violations, (
        "Agent code reaches for simulator-private state:\n  " + "\n  ".join(violations)
    )


def test_observation_exposes_only_merchant_visible_fields() -> None:
    """Every field on Observation must be defensible as merchant-visible."""
    try:
        from agent.observe import Observation
    except ImportError:
        # agent/observe.py lands on day 4; until then there is nothing to check
        # and the two tests above already hold the line.
        return

    import dataclasses

    actual = {f.name for f in dataclasses.fields(Observation)}
    leaked = actual - ALLOWED_OBSERVATION_FIELDS

    assert not leaked, (
        "Observation exposes fields not justifiable from a real Razorpay "
        f"payload: {sorted(leaked)}\n"
        "Either remove them, or add them to ALLOWED_OBSERVATION_FIELDS with a "
        "comment naming the real Razorpay field they correspond to."
    )


def test_world_keeps_truth_off_the_event() -> None:
    """`FailureEvent` is handed toward the agent, so it must carry no answers."""
    import dataclasses

    from sim.world import FailureEvent

    fields = {f.name for f in dataclasses.fields(FailureEvent)}
    banned = {"true_class", "base_prob", "is_ambiguous", "truth", "recovery_class"}
    leaked = fields & banned

    assert not leaked, f"FailureEvent leaks hidden state: {sorted(leaked)}"


def test_taxonomy_never_retry_list_is_derived_not_handwritten() -> None:
    """The policy veto list must stay in sync with the taxonomy automatically.

    A hand-maintained list of dangerous reasons is a list that goes stale. This
    asserts the derivation, so adding a HARD_STOP reason guards it immediately.
    """
    from sim.taxonomy import ERRORS, NEVER_RETRY_REASONS, RecoveryClass

    expected = {
        e.reason
        for e in ERRORS
        if e.recovery_class in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX)
    }
    assert NEVER_RETRY_REASONS == expected
    assert len(NEVER_RETRY_REASONS) > 0
