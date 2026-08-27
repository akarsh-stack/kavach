"""A thin, real Razorpay client. Test mode only.

## Why this exists, and what it is not

The evaluation runs against a simulator, and it has to: Razorpay's test mode
cannot produce the thing this project is about. You cannot make
`bank_technical_error` happen on demand, cannot simulate an issuer outage, and
cannot generate fifteen hundred failures across sixty-six reason codes with
realistic clustering. Anyone who says "just use the real API" has not thought
about where the failure distribution comes from.

But that leaves a fair objection: *has any of this ever touched real Razorpay?*
A closed loop invites suspicion, and it should.

So this module proves reach, not scale. One real order, one real failed payment,
one real error payload — fed through the same `Observation` the agent consumes
in the evaluation. It proves three things a simulation cannot:

  * our `Observation` schema matches Razorpay's actual payment object
  * the reason strings in `sim/taxonomy.py` are the ones a live API returns
  * the tool layer can drive the real API, not just a fake one

**It is deliberately outside the evaluation path.** Nothing in `evaluation/`
imports this. The reproducible numbers cannot break because a network call
failed, and a demo cannot die because an API was slow — which was the original
reason for skipping live integration entirely, and the concern is contained
rather than dismissed.

## Test mode only

`RAZORPAY_KEY_ID` must start with `rzp_test_`. A live key is refused outright by
`_require_test_mode()`. This module creates orders and reads payments; it has no
capture, refund, or payout path, so there is no code here that could move money
even against a live key.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

API = "https://api.razorpay.com/v1"


class RazorpayUnavailable(RuntimeError):
    pass


def _require_test_mode(key_id: str) -> None:
    """Refuse anything that is not a test key.

    A guard rather than a comment: this is a payments project, and the failure
    mode of getting it wrong is not a stack trace.
    """
    if not key_id.startswith("rzp_test_"):
        raise RazorpayUnavailable(
            f"refusing to run against a non-test key ({key_id[:12]}...). "
            f"This module is test-mode only."
        )


@dataclass
class LiveFailure:
    """A real failed payment, as Razorpay returned it."""

    payment_id: str
    order_id: str
    amount_paise: int
    method: str
    status: str
    error_code: str
    error_reason: str
    error_description: str
    error_source: str
    error_step: str
    created_at: datetime
    raw: dict

    @property
    def in_our_taxonomy(self) -> bool:
        """Whether the live reason string appears in our transcribed taxonomy.

        The single most useful assertion this module can make. If a real
        Razorpay failure carries a reason we do not have, our taxonomy is
        incomplete and we would rather find out from the API than from a judge.
        """
        from sim.taxonomy import BY_REASON

        return self.error_reason in BY_REASON


class RazorpayLive:
    def __init__(self, key_id: str | None = None, key_secret: str | None = None) -> None:
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not self.key_id or not self.key_secret:
            raise RazorpayUnavailable(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set. "
                "Test-mode keys: dashboard.razorpay.com -> Test Mode -> API Keys."
            )
        _require_test_mode(self.key_id)
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        self._auth = f"Basic {token}"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}{path}",
            data=data,
            method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RazorpayUnavailable(f"Razorpay {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RazorpayUnavailable(f"cannot reach Razorpay: {exc.reason}") from exc

    # -- the three calls we need -------------------------------------------

    def create_order(self, amount_paise: int, receipt: str) -> dict:
        return self._call(
            "POST",
            "/orders",
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"source": "kavach"},
            },
        )

    def payments_for_order(self, order_id: str) -> list[dict]:
        return self._call("GET", f"/orders/{order_id}/payments").get("items", [])

    def recent_payments(self, count: int = 20) -> list[dict]:
        return self._call("GET", f"/payments?count={count}").get("items", [])

    # -- what we are actually here for -------------------------------------

    def wait_for_failure(self, order_id: str, timeout_s: float = 180.0) -> LiveFailure:
        """Poll until a failed payment appears against this order."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for p in self.payments_for_order(order_id):
                if p.get("status") == "failed":
                    return self.to_failure(p)
            time.sleep(2)
        raise RazorpayUnavailable(
            f"no failed payment on {order_id} within {timeout_s:.0f}s"
        )

    @staticmethod
    def to_failure(p: dict) -> LiveFailure:
        return LiveFailure(
            payment_id=p["id"],
            order_id=p.get("order_id") or "",
            amount_paise=p.get("amount") or 0,
            method=p.get("method") or "",
            status=p.get("status") or "",
            error_code=p.get("error_code") or "",
            error_reason=p.get("error_reason") or "",
            error_description=p.get("error_description") or "",
            error_source=p.get("error_source") or "",
            error_step=p.get("error_step") or "",
            created_at=datetime.fromtimestamp(p.get("created_at") or 0),
            raw=p,
        )


# A fixed offset from the failure, not wall-clock. `datetime.now()` puts a
# moving value into the prompt, so every replay was a cache miss and a fresh
# model call -- which meant `--replay` was not actually offline and could return
# a different decision each time. Fifteen minutes is a plausible moment for a
# recovery service to pick the payment up.
REPLAY_OFFSET_MINUTES = 15


def to_observation(f: LiveFailure, now: datetime | None = None):
    """Turn a real Razorpay failure into the Observation the agent consumes.

    Deliberately the *same* dataclass the evaluation uses. If Razorpay's payload
    did not carry what the agent needs, this function could not be written --
    which is exactly the point of writing it.

    Fields a live single payment cannot supply (customer history, downtime
    signal, failure clustering) are set to their empty values rather than
    invented. The agent handles that: it is the same shape a merchant sees for a
    first-time customer.
    """
    from agent.observe import Observation

    from datetime import timedelta

    now = now or (f.created_at + timedelta(minutes=REPLAY_OFFSET_MINUTES))
    return Observation(
        payment_id=f.payment_id,
        customer_id="live",
        amount_paise=f.amount_paise,
        method=f.method or "card",
        issuer="UNKNOWN",
        psp=None,
        reason=f.error_reason,
        description=f.error_description,
        source=f.error_source,
        error_class=f.error_code or "BAD_REQUEST_ERROR",
        failed_at=f.created_at,
        is_subscription=False,
        attempt_no=1,
        customer_prior_payments=0,
        customer_prior_failures=0,
        customer_contacts_this_week=0,
        customer_first_seen=f.created_at,
        customer_lifetime_paise=0,
        issuer_downtime_reported=False,
        psp_downtime_reported=False,
        recent_failures_same_entity=0,
        recent_failures_same_reason=0,
        now=now,
        attempts_made=0,
        contacts_made=0,
        budget_remaining_paise=10**9,
    )
