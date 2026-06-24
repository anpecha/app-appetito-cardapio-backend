import os
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import httpx

from database import get_admin_db
from whatsapp import send_menu_link, send_text
from payments import create_stripe_checkout_session, create_mercadopago_payment

logger = logging.getLogger(__name__)
router = APIRouter()

APP_DOMAIN = os.environ.get("APP_DOMAIN", "http://localhost:3000")


# ==========================================
# SCHEMAS
# ==========================================


class AbandonedCartRequest(BaseModel):
    restaurant_id: str
    customer_phone: str
    customer_name: str
    slug: str
    item_count: int
    cart_total_cents: int
    items_summary: str | None = None


class PaymentRequest(BaseModel):
    provider: str = Field(..., description="stripe or mercadopago")
    order_id: str
    amount_cents: int
    description: str = "Pedido Appetito"
    customer_email: str | None = None
    customer_name: str | None = None


class WebhookPayload(BaseModel):
    """Generic WhatsApp webhook payload (Z-API compatible)."""
    phone: str | None = None
    message: dict | None = None
    text: str | None = None
    sender: str | None = None


# ==========================================
# HEALTH
# ==========================================


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cardapiodigital"}


# ==========================================
# WHATSAPP WEBHOOK
# ==========================================


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Recebe webhooks do WhatsApp (Z-API / Evolution API).
    Identifica o restaurante pelo número de telefone do destinatário
    e envia o link do cardápio digital.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("WhatsApp webhook received: %s", body)

    # Z-API format: { "phone": "5511999999999", "text": "...", "sender": "..." }
    # Evolution API format: { "data": { "key": {...}, "message": {...} } }
    # Normalize to get sender phone and message text

    sender_phone = None
    message_text = ""

    if "phone" in body:
        sender_phone = body.get("phone")
        message_text = body.get("text", "") or (body.get("message", {}) or {}).get("text", "")
    elif "data" in body:
        data = body["data"]
        msg = data.get("message", {})
        sender_phone = msg.get("from", "").replace("@s.whatsapp.net", "").replace("@c.us", "")
        message_text = msg.get("conversation", "") or ""
        for ext in (msg.get("extendedTextMessage", {}) or {}).values():
            if isinstance(ext, str):
                message_text = ext
                break

    if not sender_phone:
        logger.warning("Webhook without sender phone: %s", body)
        return {"status": "ignored", "reason": "no sender phone"}

    # Look up which restaurant owns the recipient WhatsApp number
    # The webhook is sent to the restaurant's registered Z-API instance
    # We use the restaurant's configured WhatsApp number from config_json
    db = get_admin_db()

    try:
        # Find restaurant by the recipient phone (config_json->whatsapp)
        restaurants_resp = (
            db.from_("restaurants")
            .select("id, name, slug, config_json")
            .is_("deleted_at", "null")
            .execute()
        )

        restaurant = None
        recipient_phone = _get_recipient_phone(body)

        for r in (restaurants_resp.data or []):
            cfg = r.get("config_json") or {}
            whatsapp = cfg.get("whatsapp", "")
            integrations = cfg.get("integrations", {}) or {}
            zapi_phone = integrations.get("zapi_phone", "")

            if recipient_phone and (whatsapp == recipient_phone or zapi_phone == recipient_phone):
                restaurant = r
                break

        if not restaurant:
            logger.warning("No restaurant found for recipient phone: %s", recipient_phone)
            return {"status": "ignored", "reason": "restaurant not found"}

        restaurant_name = restaurant["name"]
        slug = restaurant["slug"]
        cfg = restaurant.get("config_json") or {}
        bot_settings = cfg.get("bot_settings") or {}
        greeting = bot_settings.get("greeting_message") or None
        bot_mode = bot_settings.get("bot_mode", "simple")

        if bot_mode == "interactive":
            # Forward to robo-ia-atendimento service
            robo_url = os.environ.get("ROBO_API_URL", "http://localhost:8002")
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{robo_url}/chat/whatsapp",
                        json={
                            "phone": sender_phone,
                            "message": message_text,
                            "restaurant_id": restaurant["id"],
                            "restaurant_name": restaurant_name,
                            "slug": slug,
                        },
                    )
                return {"status": "forwarded", "to": sender_phone, "mode": "interactive"}
            except Exception as e:
                logger.warning("Failed to forward to robo-ia: %s", e)
                # Fall through to simple mode

        # Simple mode: send menu link
        await send_menu_link(
            phone=sender_phone,
            restaurant_name=restaurant_name,
            slug=slug,
            domain=APP_DOMAIN,
            source="whatsapp",
            greeting=greeting,
        )

        return {"status": "sent", "to": sender_phone, "restaurant": restaurant_name}

    except Exception as e:
        logger.error("Error processing webhook: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def _get_recipient_phone(body: dict) -> str | None:
    """Extract the recipient (restaurant's) phone from the webhook payload."""
    # Z-API sends webhooks to a specific instance which is tied to a phone
    # The phone can be in various places depending on provider
    if "instanceId" in body:
        return body.get("instancePhone")
    if "data" in body and "instance" in body["data"]:
        return body["data"]["instance"].get("phone")
    return None


# ==========================================
# TELEGRAM WEBHOOK
# ==========================================


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """
    Recebe webhooks do Telegram Bot API.
    Responde com link do cardápio digital + source=telegram.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("Telegram webhook received: %s", body)

    # Telegram Update format: { "update_id": ..., "message": { "chat": { "id": ... }, "text": "..." } }
    chat_id = None
    message_text = ""
    try:
        msg = body.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        message_text = msg.get("text", "")
    except Exception:
        pass

    if not chat_id:
        logger.warning("Telegram webhook without chat_id: %s", body)
        return {"status": "ignored", "reason": "no chat_id"}

    # Look up restaurant by Telegram bot token or configured chat
    db = get_admin_db()

    try:
        restaurants_resp = (
            db.from_("restaurants")
            .select("id, name, slug, config_json")
            .is_("deleted_at", "null")
            .execute()
        )

        # For now, use the first restaurant (single-tenant Telegram bot)
        # In production, map Telegram chat_id to restaurant via config_json
        restaurant = None
        for r in (restaurants_resp.data or []):
            cfg = r.get("config_json") or {}
            integrations = cfg.get("integrations", {}) or {}
            tg_chat = integrations.get("telegram_chat_id", "")
            if tg_chat and tg_chat == chat_id:
                restaurant = r
                break

        # Fallback: first restaurant (development mode)
        if not restaurant and restaurants_resp.data:
            restaurant = restaurants_resp.data[0]

        if not restaurant:
            logger.warning("No restaurant found for Telegram chat: %s", chat_id)
            return {"status": "ignored", "reason": "restaurant not found"}

        restaurant_name = restaurant["name"]
        slug = restaurant["slug"]
        cfg = restaurant.get("config_json") or {}
        bot_settings = cfg.get("bot_settings") or {}
        greeting = bot_settings.get("greeting_message") or None

        await send_menu_link(
            phone=chat_id,
            restaurant_name=restaurant_name,
            slug=slug,
            domain=APP_DOMAIN,
            source="telegram",
            greeting=greeting,
        )

        return {"status": "sent", "to": chat_id, "restaurant": restaurant_name}

    except Exception as e:
        logger.error("Error processing Telegram webhook: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# ABANDONED CART
# ==========================================


@router.post("/abandoned-cart/remind")
async def abandoned_cart_remind(payload: AbandonedCartRequest):
    """Save abandoned cart record and send WhatsApp recovery message."""
    db = get_admin_db()

    # Store in Supabase for analytics
    try:
        db.from_("carts_abandoned").insert(
            {
                "restaurant_id": payload.restaurant_id,
                "customer_phone": payload.customer_phone,
                "customer_name": payload.customer_name,
                "item_count": payload.item_count,
                "cart_total_cents": payload.cart_total_cents,
                "items_summary": payload.items_summary or "",
                "recovered": False,
            }
        ).execute()
    except Exception as e:
        logger.warning("Failed to store abandoned cart: %s", e)

    # Send WhatsApp recovery message
    cart_url = f"{APP_DOMAIN}/{payload.slug}?cart_recovery=true&phone={payload.customer_phone}"
    message = (
        f"Oi {payload.customer_name}! 🛒\n\n"
        f"Você deixou {payload.item_count} itens no carrinho.\n"
        f"Que tal finalizar seu pedido agora?\n\n"
        f"{cart_url}\n\n"
        f"Seu carrinho está guardado! 💛"
    )

    try:
        await send_text(phone=payload.customer_phone, message=message)
        return {"status": "sent", "phone": payload.customer_phone}
    except Exception as e:
        logger.error("Failed to send abandoned cart WA: %s", e)
        return {"status": "error", "detail": str(e)}


# ==========================================
# PAYMENT ENDPOINTS
# ==========================================


@router.post("/payments/create")
async def create_payment(payload: PaymentRequest):
    """Create a payment (Stripe PaymentIntent or Mercado Pago preference)."""
    if payload.provider == "stripe":
        result = await create_stripe_checkout_session(
            amount_cents=payload.amount_cents,
            order_id=payload.order_id,
            description=payload.description,
            customer_email=payload.customer_email,
        )
    elif payload.provider == "mercadopago":
        result = await create_mercadopago_payment(
            amount_cents=payload.amount_cents,
            order_id=payload.order_id,
            description=payload.description,
            customer_email=payload.customer_email,
            customer_name=payload.customer_name,
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid provider. Use 'stripe' or 'mercadopago'.")

    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])

    return result


# ==========================================
# PAYMENT WEBHOOKS
# ==========================================


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe payment webhook."""
    from payments import verify_stripe_webhook

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    event = await verify_stripe_webhook(payload, sig_header)
    if event is None:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.get("type", "")
    if event_type == "payment_intent.succeeded":
        intent = event["data"]["object"]
        order_id = intent.get("metadata", {}).get("order_id")
        if order_id:
            db = get_admin_db()
            db.from_("orders").update({"payment_status": "paid", "status": "preparing"}).eq(
                "id", order_id
            ).execute()
            logger.info("Payment succeeded for order %s", order_id)

    return {"status": "ok"}


@router.post("/webhook/mercadopago")
async def mercadopago_webhook(request: Request):
    """Handle Mercado Pago IPN (Instant Payment Notification)."""
    from payments import verify_mercadopago_webhook

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    payload = await verify_mercadopago_webhook(body)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid notification")

    # Mercado Pago sends topic=payment with resource=payment_id
    topic = body.get("topic") or body.get("type", "")
    if topic in ("payment", "merchant_order"):
        resource_id = body.get("resource") or body.get("data", {}).get("id", "")
        if resource_id:
            import httpx

            headers = {
                "Authorization": f"Bearer {os.environ.get('MERCADO_PAGO_ACCESS_TOKEN', '')}",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.mercadopago.com/v1/payments/{resource_id}", headers=headers
                )
                if resp.status_code == 200:
                    payment = resp.json()
                    if payment.get("status") == "approved":
                        order_id = payment.get("external_reference", "")
                        if order_id:
                            db = get_admin_db()
                            db.from_("orders").update(
                                {"payment_status": "paid", "status": "preparing"}
                            ).eq("id", order_id).execute()
                            logger.info("Mercado Pago payment approved for order %s", order_id)

    return {"status": "ok"}
