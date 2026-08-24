"""Shared vocabulary between the agent and the world.

The agent has to be able to *name* what it wants to do, and the world has to
understand the request. That shared protocol lives here rather than in either
side, so `agent/` can import it without importing the simulator.

Nothing in this module is hidden state. It is the set of moves available in the
game, which both players necessarily know. The answer key -- which move is right
for a given payment, and how likely it is to work -- stays in `sim/`.
"""

from __future__ import annotations

from enum import Enum


class Action(str, Enum):
    """What can be done about a failed payment.

    Deliberately small. A recovery system with fifty possible moves cannot be
    evaluated; five moves plus a timestamp covers what a payments team actually
    does and keeps every decision scoreable against an alternative.
    """

    RETRY = "retry"
    """Re-present the same instrument. Silent, cheap, no burden on the customer."""

    SWITCH_RAIL = "switch_rail"
    """Offer a different payment method. Requires the customer to act."""

    NUDGE = "nudge"
    """Contact the customer with a fresh payment link. Costs money and goodwill."""

    ESCALATE = "escalate"
    """Hand to a human -- risk review, engineering, or collections."""

    STOP = "stop"
    """Give up deliberately.

    Not a failure state. On a `card_expired` or a risk block, stopping is the
    highest-value move available, and a policy that cannot choose it will burn
    its budget proving that a dead instrument is still dead.
    """


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


ACTIVE_ACTIONS = (Action.RETRY, Action.SWITCH_RAIL, Action.NUDGE)
"""Actions that attempt a recovery. ESCALATE and STOP recover nothing directly;
their value is in what they prevent."""
