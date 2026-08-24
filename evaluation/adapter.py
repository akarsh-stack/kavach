"""Translates simulator state into what a merchant would actually see.

This module is the *only* place the two halves of the project meet, and it lives
outside `agent/` deliberately: it imports both sides, so neither has to import
the other. The agent stays provably blind to the simulator (enforced by
`tests/test_observability_boundary.py`) while still receiving a realistically
populated view of the world.

Every field it fills in has to be justifiable as merchant-available. The two
that deserve explaining:

  * **Customer history** comes from the merchant's own payment records -- their
    database, their customers. Counting how many times someone has paid you
    before is not privileged information.

  * **Failure clustering** (`recent_failures_same_entity`) is computed from the
    merchant's own webhook stream. Anyone watching their own failed payments can
    see that nine of them just hit the same bank. This is the signal that lets a
    good agent detect an outage the downtime feed has not reported yet -- which
    matters, because a third of outages in this world are never reported at all
    and the rest are reported late.

What it does *not* pass through: outage severity, expected duration, the true
recovery class, or anything about the customer's actual intent or balance.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

from agent.observe import CustomerLedger, Observation
from sim.world import FailureEvent, World

CLUSTER_WINDOW = timedelta(hours=1)


class ObservationBuilder:
    """Builds Observations, with the merchant-side indices precomputed once."""

    def __init__(
        self,
        world: World,
        failures: list[FailureEvent],
        customer_history: dict[str, list[tuple[datetime, bool, int]]],
    ) -> None:
        self.world = world
        self.history = customer_history
        self._ledgers: dict[str, CustomerLedger] = {}

        # Index the merchant's own failure stream by entity and by reason, so
        # cluster lookups are a binary search rather than a scan over 1,500
        # events per decision.
        self._by_entity: dict[str, list[datetime]] = {}
        self._by_reason: dict[str, list[datetime]] = {}
        for ev in failures:
            entity = ev.psp if (ev.method.value == "upi" and ev.psp) else ev.issuer
            self._by_entity.setdefault(entity, []).append(ev.failed_at)
            self._by_reason.setdefault(ev.reason, []).append(ev.failed_at)
        for lst in self._by_entity.values():
            lst.sort()
        for lst in self._by_reason.values():
            lst.sort()

    # -- ledgers -----------------------------------------------------------

    def ledger(self, customer_id: str, now: datetime) -> CustomerLedger:
        led = self._ledgers.get(customer_id)
        if led is None:
            recs = self.history.get(customer_id, [])
            first = recs[0][0] if recs else now
            led = CustomerLedger(customer_id=customer_id, first_seen=first)
            self._ledgers[customer_id] = led
        return led

    def reset(self) -> None:
        """Clear contact bookkeeping between policy runs.

        Without this, policy 2 would start with policy 1's contact history and
        hit the weekly caps immediately -- silently handicapping whichever
        policy happened to run second.
        """
        self._ledgers.clear()

    def _history_before(self, customer_id: str, when: datetime) -> tuple[int, int, int]:
        """(successful payments, failures, lifetime paise) strictly before `when`."""
        recs = self.history.get(customer_id, [])
        successes = failures = lifetime = 0
        for ts, ok, amount in recs:
            if ts >= when:
                break
            if ok:
                successes += 1
                lifetime += amount
            else:
                failures += 1
        return successes, failures, lifetime

    def _cluster(self, index: dict[str, list[datetime]], key: str, now: datetime) -> int:
        """Failures for `key` in the hour before `now`, excluding this one."""
        times = index.get(key)
        if not times:
            return 0
        lo = bisect_left(times, now - CLUSTER_WINDOW)
        hi = bisect_right(times, now)
        return max(0, hi - lo - 1)

    # -- the build ---------------------------------------------------------

    def build(
        self,
        event: FailureEvent,
        now: datetime,
        attempts_made: int,
        contacts_made: int,
        budget_remaining_paise: int,
    ) -> Observation:
        entity = event.psp if (event.method.value == "upi" and event.psp) else event.issuer
        led = self.ledger(event.customer_id, now)
        prior_ok, prior_fail, lifetime = self._history_before(event.customer_id, event.failed_at)

        visible = {e for e, _m in self.world.health.visible_downtimes(now)}

        return Observation(
            payment_id=event.payment_id,
            customer_id=event.customer_id,
            amount_paise=event.amount_paise,
            method=event.method.value,
            issuer=event.issuer,
            psp=event.psp,
            reason=event.reason,
            description=event.description,
            source=event.source.value,
            error_class=event.error_class.value,
            failed_at=event.failed_at,
            is_subscription=event.is_subscription,
            attempt_no=event.attempt_no,
            customer_prior_payments=prior_ok,
            customer_prior_failures=prior_fail,
            customer_contacts_this_week=led.contacts_in_week_before(now),
            customer_first_seen=led.first_seen,
            customer_lifetime_paise=lifetime,
            issuer_downtime_reported=event.issuer in visible,
            psp_downtime_reported=bool(event.psp and event.psp in visible),
            recent_failures_same_entity=self._cluster(self._by_entity, entity, event.failed_at),
            recent_failures_same_reason=self._cluster(
                self._by_reason, event.reason, event.failed_at
            ),
            now=now,
            attempts_made=attempts_made,
            contacts_made=contacts_made,
            budget_remaining_paise=budget_remaining_paise,
        )
