"""Payment failure taxonomy, transcribed from Razorpay's public error documentation.

Every `reason` string, `description` and `next_steps` value in ERRORS is copied
verbatim from https://razorpay.com/docs/errors/payments/list/ (retrieved
2026-08-24). Nothing in those three fields is invented.

Two fields ARE our own modelling layer, and are marked as such throughout:

  * `recovery_class` -- our compression of Razorpay's free-text "Next Steps"
    column into a small action space the agent can plan over. The mapping rule
    is mechanical and documented in docs/CALIBRATION.md; e.g. any reason whose
    documented next step is "The customer must try using another bank account
    or another method" becomes SWITCH_RAIL.

  * `base_recovery_prob` -- assumed probability that a *correctly chosen*
    intervention recovers the payment. These are assumptions, not measurements.
    docs/CALIBRATION.md states the reasoning behind each band and the
    sensitivity analysis we run over them.

The agent never imports this module. It sees only what a real merchant sees:
the `reason` and `description` strings on a failed payment webhook. See
agent/observe.py for the enforced boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    """Where the error originated.

    Values are the documented `source` enum, which varies by payment method.
    See https://razorpay.com/docs/errors/payment-error-parameters
    """

    CUSTOMER = "customer"
    BUSINESS = "business"
    INTERNAL = "internal"
    GATEWAY = "gateway"
    RAZORPAY = "razorpay"
    ISSUER_BANK = "issuer_bank"
    NETWORK = "network"
    CUSTOMER_PSP = "customer_psp"
    BENEFICIARY_BANK = "beneficiary_bank"


class Method(str, Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMANDATE = "emandate"
    CARDLESS_EMI = "cardless_emi"


class ErrorClass(str, Enum):
    """Razorpay's own top-level split on the error list page."""

    BAD_REQUEST = "BAD_REQUEST_ERROR"
    GATEWAY = "GATEWAY_ERROR"


class RecoveryClass(str, Enum):
    """The agent's action space. OUR abstraction, derived from documented next steps.

    Ordered roughly by how aggressive the intervention is.
    """

    RETRY_SAME = "retry_same"
    """Transient fault at bank/gateway/PSP. Same instrument will likely work shortly."""

    RETRY_LATER_FUNDS = "retry_later_funds"
    """Customer lacks funds or has hit a rolling limit. Needs calendar time, not seconds."""

    SWITCH_RAIL = "switch_rail"
    """The instrument itself is dead. Retrying it is pure waste; offer another method."""

    NUDGE_CUSTOMER = "nudge_customer"
    """Intent existed but the customer dropped out. Needs a human prompt, not a machine retry."""

    HARD_STOP = "hard_stop"
    """Fraud, risk or compliance block. Never retry. Escalate and stop."""

    MERCHANT_FIX = "merchant_fix"
    """Our own integration is broken. Not a customer recovery case; page engineering."""


@dataclass(frozen=True)
class ErrorReason:
    reason: str
    """Verbatim Razorpay `reason` string."""

    description: str
    """Verbatim Razorpay "Detailed Error Description"."""

    next_steps: str
    """Verbatim Razorpay "Next Steps"."""

    recovery_class: RecoveryClass
    """OUR mapping. See docs/CALIBRATION.md."""

    error_class: ErrorClass
    sources: tuple[Source, ...]
    methods: tuple[Method, ...]

    base_recovery_prob: float
    """OUR assumption: P(recovered | correct intervention chosen). See docs/CALIBRATION.md."""

    held_out: bool = False
    """If True, the rules-engine baseline has no hand-written mapping for this reason.

    Simulates the real operational fact that new gateway reason strings appear
    faster than anyone updates a rulebook. Used to measure whether the LLM
    actually generalises or merely memorises. See eval/baselines.py.
    """


def _e(
    reason: str,
    description: str,
    next_steps: str,
    recovery_class: RecoveryClass,
    error_class: ErrorClass,
    sources: tuple[Source, ...],
    methods: tuple[Method, ...],
    base_recovery_prob: float,
    held_out: bool = False,
) -> ErrorReason:
    return ErrorReason(
        reason=reason,
        description=description,
        next_steps=next_steps,
        recovery_class=recovery_class,
        error_class=error_class,
        sources=sources,
        methods=methods,
        base_recovery_prob=base_recovery_prob,
        held_out=held_out,
    )


ALL_METHODS = (Method.CARD, Method.UPI, Method.NETBANKING, Method.WALLET)
_BR = ErrorClass.BAD_REQUEST
_GW = ErrorClass.GATEWAY
_RC = RecoveryClass


# --------------------------------------------------------------------------
# Transient infrastructure faults -> RETRY_SAME
#
# Razorpay's documented next step for all of these is some form of "the
# customer must retry" / "please retry after some time", with no instruction to
# change instrument. High recovery probability once the fault clears.
# --------------------------------------------------------------------------

_TRANSIENT = [
    _e(
        "bank_not_available",
        "Bank is not available due to a downtime or a technical issue.",
        "The customer must retry.",
        _RC.RETRY_SAME,
        _GW,
        (Source.ISSUER_BANK, Source.GATEWAY),
        (Method.NETBANKING, Method.UPI, Method.CARD, Method.EMANDATE),
        0.86,
    ),
    _e(
        "bank_cutoff_in_progress",
        "Bank CBS cutoff is in progress. This is a periodic event at the bank's end.",
        "The customer must retry.",
        _RC.RETRY_SAME,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.NETBANKING, Method.UPI, Method.EMANDATE),
        0.91,
    ),
    _e(
        "bank_technical_error",
        "The issuing bank was facing technical problems at the moment the payment "
        "was attempted. This usually occurs when the Core Banking System encounters "
        "a technical error while processing the payment.",
        "The customer must try using another bank account or another method.",
        _RC.RETRY_SAME,
        _GW,
        (Source.ISSUER_BANK,),
        ALL_METHODS,
        0.74,
    ),
    _e(
        "gateway_technical_error",
        "Technical error occurred at the gateway.",
        "Please retry after some time.",
        _RC.RETRY_SAME,
        _GW,
        (Source.GATEWAY,),
        ALL_METHODS,
        0.88,
    ),
    _e(
        "issuer_technical_error",
        "Technical error occurred at the card issuer.",
        "Please retry after some time or use another card.",
        _RC.RETRY_SAME,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.79,
    ),
    _e(
        "server_error",
        "Technical error at Razorpay's server. This usually occurs when there is "
        "some server issue at Razorpay's end.",
        "Please retry after some time or reach out to Razorpay.",
        _RC.RETRY_SAME,
        _GW,
        (Source.RAZORPAY, Source.INTERNAL),
        ALL_METHODS,
        0.93,
    ),
    _e(
        "payment_declined_due_to_high_traffic",
        "Payment declined due to high traffic at the gateway.",
        "Please retry after some time.",
        _RC.RETRY_SAME,
        _GW,
        (Source.GATEWAY,),
        ALL_METHODS,
        0.90,
    ),
    _e(
        "request_timed_out",
        "The request has timed out.",
        "Please retry the request.",
        _RC.RETRY_SAME,
        _GW,
        (Source.GATEWAY, Source.NETWORK),
        ALL_METHODS,
        0.84,
    ),
    _e(
        "invalid_response_from_gateway",
        "Invalid response received from the gateway.",
        "Please retry the transaction.",
        _RC.RETRY_SAME,
        _GW,
        (Source.GATEWAY,),
        ALL_METHODS,
        0.82,
    ),
    _e(
        "verification_failed",
        "Verification of the payment using the status check API has failed.",
        "This is a temporary error. The customer must retry.",
        _RC.RETRY_SAME,
        _BR,
        (Source.INTERNAL, Source.GATEWAY),
        ALL_METHODS,
        0.87,
    ),
    _e(
        "upi_app_technical_error",
        "Technical error occurred at the customer's PSP due to which the payment failed.",
        "The customer must retry the payment. If the error persists then the "
        "customer should try using another UPI app.",
        _RC.RETRY_SAME,
        _BR,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.81,
    ),
    _e(
        "psp_app_not_available",
        "PSP app is not available. This can be because of a downtime with the PSP.",
        "The customer must retry with another PSP app.",
        _RC.RETRY_SAME,
        _GW,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.77,
    ),
    _e(
        "psp_not_available",
        "PSP is not available.",
        "Customer needs to retry with another PSP.",
        _RC.RETRY_SAME,
        _GW,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.76,
    ),
    _e(
        "authorisation_declined_by_psp",
        "PSP app has rejected the authorisation request. This can happen when there "
        "is an issue/downtime with the PSP or there's an issue with the customer's VPA.",
        "Recheck the customer's VPA and retry. If this is recurring, then the "
        "customer can choose another PSP app and retry.",
        _RC.RETRY_SAME,
        _GW,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.63,
        held_out=True,
    ),
    _e(
        "deemed_transaction",
        "The transaction is deemed and cannot be processed.",
        "Please contact Razorpay for assistance.",
        _RC.RETRY_SAME,
        _GW,
        (Source.NETWORK,),
        (Method.UPI,),
        0.58,
        held_out=True,
    ),
    _e(
        "duplicate_rrn_found",
        "A duplicate RRN (Retrieval Reference Number) was found.",
        "Please retry with a new transaction.",
        _RC.RETRY_SAME,
        _GW,
        (Source.NETWORK,),
        (Method.UPI,),
        0.72,
        held_out=True,
    ),
]


# --------------------------------------------------------------------------
# Funds / rolling limits -> RETRY_LATER_FUNDS
#
# Retrying in seconds is worthless; these need calendar time. Timing is the
# whole game here, which is why we model salary-cycle effects in sim/customers.py.
# --------------------------------------------------------------------------

_FUNDS = [
    _e(
        "insufficient_funds",
        "The customer does not have sufficient funds in the account to complete the payment.",
        "The customer must retry with a different card or method.",
        _RC.RETRY_LATER_FUNDS,
        _BR,
        (Source.ISSUER_BANK, Source.CUSTOMER),
        ALL_METHODS,
        0.68,
    ),
    _e(
        "transaction_daily_limit_exceeded",
        "The customer has exceeded the daily transaction limit set on the card. "
        "Some of the cards allow customers to set a limit or have a default limit set.",
        "The customer must retry using a different instrument or wait 24 hours to "
        "complete the payment.",
        _RC.RETRY_LATER_FUNDS,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD, Method.UPI),
        0.83,
    ),
    _e(
        "transaction_limit_exceeded",
        "The customers have exceeded the credit or debit limit set on their cards. "
        "This error usually arises while doing high value transactions.",
        "The customer must retry using a different bank's card or method.",
        _RC.RETRY_LATER_FUNDS,
        _BR,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.61,
    ),
    _e(
        "transaction_frequency_limit_exceeded",
        "NPCI has a transaction limit both on the amount and the frequency per day. "
        "Customer has exhausted the frequency limit.",
        "Please retry using another payment method.",
        _RC.RETRY_LATER_FUNDS,
        _BR,
        (Source.NETWORK,),
        (Method.UPI,),
        0.88,
    ),
    _e(
        "transaction_daily_count_exceeded",
        "The daily transaction count has been exceeded.",
        "Customer needs to wait for the next day or use another payment method.",
        _RC.RETRY_LATER_FUNDS,
        _GW,
        (Source.ISSUER_BANK, Source.NETWORK),
        (Method.UPI, Method.CARD),
        0.86,
    ),
    _e(
        "credit_limit_exceeded",
        "The customer's credit limit has been exceeded.",
        "Customer needs to reduce the amount or use another payment method.",
        _RC.RETRY_LATER_FUNDS,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD, Method.CARDLESS_EMI),
        0.54,
    ),
    _e(
        "otp_attempts_exceeded",
        "OTP attempts have been exceeded.",
        "Customer needs to wait and retry after some time.",
        _RC.RETRY_LATER_FUNDS,
        _BR,
        (Source.ISSUER_BANK, Source.CUSTOMER),
        (Method.CARD, Method.NETBANKING),
        0.71,
    ),
    _e(
        "pin_attempts_exceeded",
        "PIN attempts have been exceeded.",
        "Customer needs to wait and retry after some time.",
        _RC.RETRY_LATER_FUNDS,
        _BR,
        (Source.ISSUER_BANK, Source.CUSTOMER),
        (Method.UPI, Method.CARD),
        0.69,
    ),
    _e(
        "funds_blocked_by_mandate",
        "Funds are blocked by an existing mandate.",
        "Customer needs to release the mandate or use another account.",
        _RC.RETRY_LATER_FUNDS,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.EMANDATE, Method.UPI),
        0.47,
        held_out=True,
    ),
]


# --------------------------------------------------------------------------
# Dead instrument -> SWITCH_RAIL
#
# Razorpay's documented next step explicitly names a *different* card, account
# or method. Retrying the same instrument here is the single most common
# money-wasting mistake, and is what the fixed-retry baseline does.
# --------------------------------------------------------------------------

_DEAD_INSTRUMENT = [
    _e(
        "card_expired",
        "The card has expired.",
        "The customer must retry with a valid card.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.CUSTOMER, Source.ISSUER_BANK),
        (Method.CARD,),
        0.55,
    ),
    _e(
        "debit_instrument_blocked",
        "The customer is using a blocked card to complete the payment. The card "
        "could have been blocked by the issuer or by customers themselves.",
        "The customer must retry with a different card or method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.52,
    ),
    _e(
        "debit_instrument_inactive",
        "The debit instrument is inactive.",
        "Customer needs to activate the instrument or use another payment method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.50,
    ),
    _e(
        "card_declined",
        "The card has been declined.",
        "The customer must retry with a different card or method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.49,
    ),
    _e(
        "payment_declined",
        "The payment has been declined.",
        "Customer needs to retry with another payment method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK, Source.GATEWAY),
        ALL_METHODS,
        0.51,
    ),
    _e(
        "debit_declined",
        "The debit transaction has been declined.",
        "Customer needs to retry with another payment method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARD, Method.UPI),
        0.50,
        held_out=True,
    ),
    _e(
        "bank_account_invalid",
        "The bank account is not valid. The customer or bank could have closed the account.",
        "The customer must try using a valid bank account or another method.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.CUSTOMER, Source.ISSUER_BANK),
        (Method.NETBANKING, Method.EMANDATE),
        0.44,
    ),
    _e(
        "invalid_vpa",
        "The customer has entered an incorrect VPA to complete the payment.",
        "The customer must check and enter the correct VPA.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.CUSTOMER,),
        (Method.UPI,),
        0.58,
    ),
    _e(
        "vpa_resolution_failed",
        "The UPI network failed to validate the VPA. This is a technical error "
        "when the resolution fails.",
        "The customer must retry using a different bank account or method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.NETWORK,),
        (Method.UPI,),
        0.60,
    ),
    _e(
        "transaction_on_vpa_restricted",
        "Transaction on this VPA has been temporarily / permanently blocked by the PSP.",
        "The customer to retry with another UPI ID.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.46,
    ),
    _e(
        "psp_app_not_supported",
        "UPI App is not supported. This is a rare error used when a particular app "
        "is blacklisted.",
        "Please choose another PSP app and try again.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.64,
        held_out=True,
    ),
    _e(
        "card_not_enrolled",
        "The card is not enrolled for this payment method.",
        "Customer needs to enroll the card for this payment method or use another card.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.ISSUER_BANK,),
        (Method.CARD,),
        0.53,
    ),
    _e(
        "user_not_registered_for_netbanking",
        "The customer's bank account is not registered for netbanking.",
        "The customer should register their account with the issuing bank for netbanking.",
        _RC.SWITCH_RAIL,
        _BR,
        (Source.CUSTOMER, Source.ISSUER_BANK),
        (Method.NETBANKING,),
        0.57,
    ),
    _e(
        "credit_not_permitted",
        "Credit transactions are not permitted for this customer.",
        "Customer needs to use another payment method.",
        _RC.SWITCH_RAIL,
        _GW,
        (Source.ISSUER_BANK,),
        (Method.CARDLESS_EMI, Method.CARD),
        0.48,
        held_out=True,
    ),
]


# --------------------------------------------------------------------------
# Customer dropped out -> NUDGE_CUSTOMER
#
# Purchase intent was demonstrated (they reached the auth screen), so these are
# the most valuable failures in the batch. But they need a human-facing prompt,
# and prompts have a cost and an annoyance budget. See economics/costs.py.
# --------------------------------------------------------------------------

_DROPPED = [
    _e(
        "authentication_failed",
        "The payment failed as 3D secure, or OTP authentication failed. This could "
        "happen if the user cancels the payment on the authentication (OTP submit) "
        "screen or enters incorrect authentication details such as OTP.",
        "The customer must enter correct authentication details to complete the payment.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER, Source.ISSUER_BANK),
        (Method.CARD, Method.NETBANKING, Method.EMANDATE),
        0.66,
    ),
    _e(
        "incorrect_otp",
        "The customer has entered an incorrect OTP to complete the payment.",
        "The customer must retry and enter the correct OTP.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.CARD, Method.NETBANKING),
        0.74,
    ),
    _e(
        "otp_expired",
        "The OTP has expired.",
        "Customer needs to request a new OTP.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.CARD, Method.NETBANKING),
        0.78,
    ),
    _e(
        "payment_cancelled",
        "The customer has explicitly cancelled the payment due to which the "
        "authentication failed to complete.",
        "The customer must retry to complete the payment.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER,),
        ALL_METHODS,
        0.41,
    ),
    _e(
        "payment_timed_out",
        "The customer did not complete the transaction within the specified time. "
        "This error may also happen when no response is received from the gateway.",
        "The customer must retry and complete the transaction within the time.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER, Source.GATEWAY),
        ALL_METHODS,
        0.62,
    ),
    _e(
        "payment_session_expired",
        "The payment session has expired.",
        "Customer needs to start a new payment session.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER,),
        ALL_METHODS,
        0.67,
    ),
    _e(
        "payment_collect_request_expired",
        "The payment collect request has expired.",
        "Customer needs to retry the payment.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER,),
        (Method.UPI,),
        0.70,
    ),
    _e(
        "collect_request_pending",
        "A collect request is already pending for this transaction.",
        "Please wait for the current request to complete or retry after some time.",
        _RC.NUDGE_CUSTOMER,
        _GW,
        (Source.CUSTOMER_PSP,),
        (Method.UPI,),
        0.72,
        held_out=True,
    ),
    _e(
        "incorrect_cvv",
        "The customer has entered an incorrect CVV to complete the payment.",
        "The customer must retry and enter the correct CVV.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.CARD,),
        0.76,
    ),
    _e(
        "incorrect_card_details",
        "Incorrect card details entered.",
        "Customer needs to enter correct card details.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.CARD,),
        0.73,
    ),
    _e(
        "incorrect_card_expiry_date",
        "Incorrect card expiry date entered.",
        "Customer needs to enter the correct card expiry date.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.CARD,),
        0.75,
    ),
    _e(
        "incorrect_pin",
        "Incorrect PIN entered.",
        "Customer needs to enter the correct PIN.",
        _RC.NUDGE_CUSTOMER,
        _BR,
        (Source.CUSTOMER,),
        (Method.UPI, Method.CARD),
        0.77,
    ),
]


# --------------------------------------------------------------------------
# Risk / compliance -> HARD_STOP
#
# The guardrail set. Retrying a risk-declined payment is not merely wasteful,
# it is the kind of thing that gets a merchant's MID reviewed. agent/policy.py
# vetoes any proposed retry on these regardless of what the model asks for, and
# every veto is recorded in the audit trail.
# --------------------------------------------------------------------------

_RISK = [
    _e(
        "payment_risk_check_failed",
        "Payment declined due to risk checks. Risk checks are performed by Razorpay, "
        "Gateway, and Issuer Bank. The source parameter would give additional clarity "
        "where the risk check failed.",
        "The customer must retry with a different card or method.",
        _RC.HARD_STOP,
        _GW,
        (Source.RAZORPAY, Source.GATEWAY, Source.ISSUER_BANK),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "compliance_violation",
        "The payment violates compliance requirements.",
        "Please ensure the payment meets all compliance requirements.",
        _RC.HARD_STOP,
        _BR,
        (Source.RAZORPAY, Source.BUSINESS),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "payment_amount_tampered",
        "The payment amount has been tampered.",
        "The customer must retry with the correct amount.",
        _RC.HARD_STOP,
        _GW,
        (Source.RAZORPAY, Source.BUSINESS),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "international_transaction_not_allowed",
        "International transactions are not allowed.",
        "Customer needs to use a domestic payment method or enable international transactions.",
        _RC.HARD_STOP,
        _BR,
        (Source.ISSUER_BANK, Source.BUSINESS),
        (Method.CARD,),
        0.0,
    ),
    _e(
        "collect_on_mcc_blocked",
        "UPI Collect is blocked for this MCC (Merchant Category Code).",
        "Please contact Razorpay for assistance.",
        _RC.HARD_STOP,
        _GW,
        (Source.NETWORK,),
        (Method.UPI,),
        0.0,
        held_out=True,
    ),
    _e(
        "user_not_eligible",
        "The customer failed the eligibility check and is not eligible for credit. "
        "This error may arise when the customer has a poor credit score or incomplete "
        "/ insufficient documents.",
        "The customer must retry using a different payment method.",
        _RC.HARD_STOP,
        _BR,
        (Source.ISSUER_BANK,),
        (Method.CARDLESS_EMI,),
        0.0,
    ),
]


# --------------------------------------------------------------------------
# Our own integration is broken -> MERCHANT_FIX
#
# Not a customer-recovery case at all. The right move is to stop retrying and
# raise an engineering ticket. A recovery agent that cheerfully retries an
# `invalid_order_id` 3 times is burning money on a bug it should be reporting,
# and that is exactly the trap the naive-LLM baseline falls into.
# --------------------------------------------------------------------------

_MERCHANT = [
    _e(
        "input_validation_failed",
        "Payment failed due to wrong request or input sent in the payment request. "
        "This is also seen while creating a payment with incorrect parameter values "
        "on the Dashboard.",
        "Rectify the validation issues and try again. Check the error description and "
        "field parameters for more information about the error.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "invalid_order_id",
        "Order ID required in the payment request is either missing or is invalid. "
        "Order ID is mandatory for every payment.",
        "Make sure the correct order ID is always passed while initiating a new payment.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "order_amount_mismatch",
        "This error arises when the amount mentioned in the order request is "
        "different from the amount mentioned in the payment request.",
        "Please make sure that the same amount is passed in both payment and order request.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "order_already_paid",
        "There can only be one successful payment for each order ID. This error "
        "arises when you are trying to use an order ID where a payment is already completed.",
        "Check for order status before initiating a new payment attempt on the order.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "payment_method_not_enabled",
        "The selected payment method is not enabled for your business. This error "
        "occurs when your customer tries to complete a transaction using a method "
        "that is not enabled for you.",
        "Reach out to Razorpay to enable payment method.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
    ),
    _e(
        "bank_not_enabled",
        "The selected bank to complete the transaction is not enabled for your business.",
        "Please reach out to Razorpay to enable the selected bank.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        (Method.NETBANKING,),
        0.0,
    ),
    _e(
        "card_network_not_enabled",
        "The card's network (Visa, Mastercard, etc.) is not enabled for the merchant.",
        "Please reach out to Razorpay to enable the card network.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        (Method.CARD,),
        0.0,
    ),
    _e(
        "amount_less_than_minimum_amount",
        "Amount in the payment request is less than the minimum amount. Transacting "
        "through some banks have fixed fees. If the payment amount is less than the "
        "fixed fee then this error shows up.",
        "Please make sure that the payment amount is more than the minimum fees "
        "associated with the bank.",
        _RC.MERCHANT_FIX,
        _BR,
        (Source.BUSINESS,),
        ALL_METHODS,
        0.0,
        held_out=True,
    ),
]


# --------------------------------------------------------------------------
# The catch-all. Razorpay documents this one as having NO specific error code
# from the gateway, which makes it the single most interesting reason in the
# taxonomy: the correct action is genuinely underdetermined by the reason
# string alone, and can only be inferred from context (customer history,
# method, amount, issuer health). This is where an LLM should earn its keep
# over a lookup table, and eval/report.py breaks out its performance separately.
# --------------------------------------------------------------------------

_AMBIGUOUS = [
    _e(
        "payment_failed",
        "Payment processing failed due to error at bank or wallet gateway. No "
        "specific error code received from gateway in this case.",
        "Please retry with a different payment method.",
        _RC.RETRY_SAME,  # nominal; sim/world.py resolves the true class per-event
        _GW,
        (Source.GATEWAY, Source.ISSUER_BANK),
        ALL_METHODS,
        0.55,
    ),
]


ERRORS: tuple[ErrorReason, ...] = tuple(
    _TRANSIENT + _FUNDS + _DEAD_INSTRUMENT + _DROPPED + _RISK + _MERCHANT + _AMBIGUOUS
)

BY_REASON: dict[str, ErrorReason] = {e.reason: e for e in ERRORS}

HELD_OUT_REASONS: frozenset[str] = frozenset(e.reason for e in ERRORS if e.held_out)

AMBIGUOUS_REASONS: frozenset[str] = frozenset(e.reason for e in _AMBIGUOUS)

NEVER_RETRY_REASONS: frozenset[str] = frozenset(
    e.reason
    for e in ERRORS
    if e.recovery_class in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX)
)
"""Consumed by agent/policy.py as a hard veto list. Deliberately derived from the
taxonomy rather than hand-listed, so a new HARD_STOP reason is guarded the moment
it is added."""


def by_class(rc: RecoveryClass) -> tuple[ErrorReason, ...]:
    return tuple(e for e in ERRORS if e.recovery_class == rc)


def summary() -> dict[str, int]:
    """Counts per recovery class. Printed by scripts/verify_taxonomy.py."""
    return {rc.value: len(by_class(rc)) for rc in RecoveryClass}
