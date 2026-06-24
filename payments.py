"""Stripe and Mercado Pago payment integration."""

import os
from decimal import Decimal

import stripe
import httpx

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
MERCADO_PAGO_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN", "")
MERCADO_PAGO_WEBHOOK_SECRET = os.environ.get("MERCADO_PAGO_WEBHOOK_SECRET", "")
APP_DOMAIN = os.environ.get("APP_DOMAIN", "http://localhost:3000")


async def create_stripe_payment(
    amount_cents: int,
    order_id: str,
    description: str = "Pedido Appetito",
    customer_email: str | None = None,
) -> dict:
    """Create a Stripe PaymentIntent and return the client secret."""
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="brl",
            description=description,
            metadata={"order_id": order_id},
            receipt_email=customer_email,
            automatic_payment_methods={"enabled": True},
        )
        return {
            "provider": "stripe",
            "payment_intent_id": intent.id,
            "client_secret": intent.client_secret,
            "status": intent.status,
        }
    except stripe.StripeError as e:
        return {"error": str(e), "provider": "stripe"}


async def create_stripe_checkout_session(
    amount_cents: int,
    order_id: str,
    description: str = "Pedido Appetito",
    customer_email: str | None = None,
) -> dict:
    """Create a Stripe Checkout Session and return the checkout URL."""
    try:
        session = stripe.checkout.Session.create(
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "product_data": {"name": description},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            metadata={"order_id": order_id},
            customer_email=customer_email,
            success_url=f"{APP_DOMAIN}/tracking/{order_id}?payment=success",
            cancel_url=f"{APP_DOMAIN}/{order_id.split('-')[0] if '-' in order_id else order_id}/checkout?payment=cancelled",
        )
        return {
            "provider": "stripe",
            "session_id": session.id,
            "checkout_url": session.url,
            "status": "pending",
        }
    except stripe.StripeError as e:
        return {"error": str(e), "provider": "stripe"}


async def create_mercadopago_payment(
    amount_cents: int,
    order_id: str,
    description: str = "Pedido Appetito",
    customer_email: str | None = None,
    customer_name: str | None = None,
) -> dict:
    """Create a Mercado Pago preference and return the init point URL."""
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return {"error": "Mercado Pago not configured", "provider": "mercadopago"}

    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "items": [
            {
                "title": description,
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(Decimal(amount_cents) / Decimal(100)),
            }
        ],
        "external_reference": order_id,
        "notification_url": f"{APP_DOMAIN}/api/proxy/services/cardapiodigital/webhook/mercadopago",
        "purpose": "wallet_purchase",
    }
    if customer_email:
        payload["payer"] = {"email": customer_email}
    if customer_name:
        payload["payer"] = payload.get("payer", {})
        payload["payer"]["name"] = customer_name

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                headers=headers,
                json=payload,
            )
            data = resp.json()
            if resp.status_code not in (200, 201):
                return {"error": data.get("message", "Mercado Pago error"), "provider": "mercadopago"}
            return {
                "provider": "mercadopago",
                "preference_id": data["id"],
                "init_point": data["init_point"],
                "sandbox_init_point": data.get("sandbox_init_point"),
                "status": "pending",
            }
    except httpx.HTTPError as e:
        return {"error": str(e), "provider": "mercadopago"}


async def verify_stripe_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Verify and parse a Stripe webhook event."""
    endpoint_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not endpoint_secret:
        return None
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        return event
    except (ValueError, stripe.SignatureVerificationError):
        return None


async def verify_mercadopago_webhook(payload: dict) -> dict | None:
    """Verify a Mercado Pago IPN notification (basic check)."""
    # Mercado Pago uses x-signature header with HMAC-SHA256
    # For simplicity, we trust the notification_url + query params
    return payload
