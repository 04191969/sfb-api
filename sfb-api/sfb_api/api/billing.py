"""Stripe subscription rows that gate fitment and watches."""

from datetime import datetime, timezone

from django.conf import settings

from .catalog import (
    subscription_row,
    update_subscription_by_stripe_id,
    upsert_subscription,
)
from .catalog_shape import tier_for_status


def subscription_payload(user_id, status, customer_id=None, subscription_id=None):
    tier, stored_status = tier_for_status(status)
    row = {
        "user_id": user_id,
        "tier": tier,
        "status": stored_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if customer_id:
        row["stripe_customer_id"] = customer_id
    if subscription_id:
        row["stripe_subscription_id"] = subscription_id
    return row


def apply_checkout_session(session):
    """Record a completed Checkout session against the Supabase user id."""
    if session.get("mode") != "subscription":
        return None
    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return None
    user_id = session.get("client_reference_id") or (session.get("metadata") or {}).get("user_id")
    if not user_id:
        return None
    row = subscription_payload(
        user_id,
        "active",
        customer_id=session.get("customer"),
        subscription_id=session.get("subscription"),
    )
    return upsert_subscription(row)


def apply_subscription_event(subscription, deleted=False):
    status = "canceled" if deleted else subscription.get("status")
    user_id = (subscription.get("metadata") or {}).get("user_id")
    values = subscription_payload(
        user_id or "",
        status,
        customer_id=subscription.get("customer"),
        subscription_id=subscription.get("id"),
    )
    if user_id:
        return upsert_subscription(values)
    values.pop("user_id", None)
    return update_subscription_by_stripe_id(subscription.get("id"), values)


def create_checkout_session(user_id):
    import stripe

    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_PRICE_ID:
        raise RuntimeError("Stripe checkout is not configured.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode="subscription",
        client_reference_id=user_id,
        line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        metadata={"user_id": user_id},
        subscription_data={"metadata": {"user_id": user_id}},
    )
    return session.url


def create_portal_session(user_id):
    import stripe

    row = subscription_row(user_id)
    customer_id = (row or {}).get("stripe_customer_id")
    if not customer_id:
        raise ValueError("No Stripe customer for this account.")
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("Stripe checkout is not configured.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=settings.STRIPE_SUCCESS_URL,
    )
    return session.url


def construct_event(payload, signature):
    import stripe

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("Stripe webhook is not configured.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe.Webhook.construct_event(
        payload,
        signature,
        settings.STRIPE_WEBHOOK_SECRET,
    )
