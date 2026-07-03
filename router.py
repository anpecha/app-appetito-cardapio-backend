import os
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import httpx

from database import get_admin_db
from whatsapp import send_menu_link as send_wa_menu_link, send_text
from payments import create_stripe_checkout_session, create_mercadopago_payment
from meta import send_facebook_menu_link, send_instagram_menu_link
from telegram_bot import send_menu_link as send_tg_menu_link

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
            robo_url = os.environ.get("ROBO_API_URL", "http://localhost:8002")
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{robo_url}/robo/chat",
                        json={
                            "session_id": f"wa_{sender_phone}",
                            "message": message_text,
                            "restaurant_id": restaurant["id"],
                            "restaurant_name": restaurant_name,
                            "slug": slug,
                            "source": "whatsapp",
                        },
                    )
                if resp.status_code == 200:
                    reply = resp.json().get("response", "")
                    if reply:
                        await send_text(phone=sender_phone, message=reply)
                    return {"status": "replied", "to": sender_phone}
            except Exception as e:
                logger.warning("Failed to forward to robo-ia: %s", e)

        await send_wa_menu_link(
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
        integrations = cfg.get("integrations", {}) or {}
        bot_settings = cfg.get("bot_settings") or {}
        greeting = bot_settings.get("greeting_message") or None
        bot_mode = bot_settings.get("bot_mode", "simple")
        bot_token = ""
        if isinstance(integrations, list):
            for i in integrations:
                if i.get("id") == "telegram":
                    bot_token = (i.get("fields") or {}).get("bot_token", "")
        else:
            bot_token = integrations.get("telegram_bot_token") or (integrations.get("fields") or {}).get("bot_token", "")

        if bot_mode == "interactive" and bot_token:
            robo_url = os.environ.get("ROBO_API_URL", "http://localhost:8002")
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{robo_url}/robo/chat",
                        json={
                            "session_id": f"tg_{chat_id}",
                            "message": message_text,
                            "restaurant_id": restaurant["id"],
                            "restaurant_name": restaurant_name,
                            "slug": slug,
                            "source": "telegram",
                        },
                    )
                if resp.status_code == 200:
                    reply = resp.json().get("response", "")
                    if reply:
                        from telegram_bot import send_message as tg_send
                        await tg_send(bot_token=bot_token, chat_id=chat_id, text=reply)
                    return {"status": "replied", "to": chat_id}
            except Exception as e:
                logger.warning("Failed to forward to robo-ia: %s", e)

        await send_tg_menu_link(
            bot_token=bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=chat_id,
            restaurant_name=restaurant_name,
            slug=slug,
            domain=APP_DOMAIN,
            greeting=greeting,
        )

        return {"status": "sent", "to": chat_id, "restaurant": restaurant_name}

    except Exception as e:
        logger.error("Error processing Telegram webhook: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# FACEBOOK WEBHOOK (Meta Graph API)
# ==========================================


@router.post("/webhook/facebook")
async def facebook_webhook(request: Request):
    """Recebe webhooks do Facebook Messenger via Meta Graph API."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("Facebook webhook received: %s", body)

    if body.get("object") != "page":
        return {"status": "ignored", "reason": "not a page subscription"}

    for entry in body.get("entry", []):
        page_id = entry.get("id", "")
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id", "")
            message_text = event.get("message", {}).get("text", "")
            if not sender_id or not message_text:
                continue

            db = get_admin_db()
            restaurants_resp = (
                db.from_("restaurants")
                .select("id, name, slug, config_json")
                .is_("deleted_at", "null")
                .execute()
            )

            restaurant = None
            page_token = ""
            for r in (restaurants_resp.data or []):
                cfg = r.get("config_json") or {}
                integrations = cfg.get("integrations", {}) or {}
                if isinstance(integrations, list):
                    for i in integrations:
                        if i.get("id") == "facebook" and i.get("fields", {}).get("page_id") == page_id:
                            restaurant = r
                            page_token = (i.get("fields") or {}).get("page_token", "")
                            break
                else:
                    fb = integrations.get("facebook", {})
                    if fb.get("page_id") == page_id:
                        restaurant = r
                        page_token = fb.get("page_token", "")

            if not restaurant:
                logger.warning("No restaurant found for Facebook page: %s", page_id)
                continue

            restaurant_name = restaurant["name"]
            slug = restaurant["slug"]
            cfg = restaurant.get("config_json") or {}
            bot_settings = cfg.get("bot_settings") or {}
            greeting = bot_settings.get("greeting_message") or None
            bot_mode = bot_settings.get("bot_mode", "simple")

            if bot_mode == "interactive" and page_token:
                robo_url = os.environ.get("ROBO_API_URL", "http://localhost:8002")
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.post(
                            f"{robo_url}/robo/chat",
                            json={
                                "session_id": f"fb_{sender_id}",
                                "message": message_text,
                                "restaurant_id": restaurant["id"],
                                "restaurant_name": restaurant_name,
                                "slug": slug,
                                "source": "facebook",
                            },
                        )
                    if resp.status_code == 200:
                        reply = resp.json().get("response", "")
                        if reply:
                            from meta import send_facebook_message as fb_send
                            await fb_send(page_id=page_id, token=page_token, psid=sender_id, message=reply)
                    continue
                except Exception as e:
                    logger.warning("Failed to forward to robo-ia: %s", e)

            await send_facebook_menu_link(
                page_id=page_id,
                token=page_token,
                psid=sender_id,
                restaurant_name=restaurant_name,
                slug=slug,
                domain=APP_DOMAIN,
                greeting=greeting,
            )

    return {"status": "ok"}


@router.get("/webhook/facebook")
async def facebook_webhook_verify(request: Request):
    """Verificação do webhook do Facebook (handshake)."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    expected = os.environ.get("META_VERIFY_TOKEN", "appetito_meta_verify_2024")
    if mode == "subscribe" and token == expected:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================
# INSTAGRAM WEBHOOK (Meta Graph API)
# ==========================================


@router.post("/webhook/instagram")
async def instagram_webhook(request: Request):
    """Recebe webhooks do Instagram Direct via Meta Graph API."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("Instagram webhook received: %s", body)

    if body.get("object") != "instagram":
        return {"status": "ignored", "reason": "not an instagram subscription"}

    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id", "")
            message_text = event.get("message", {}).get("text", "")
            if not sender_id or not message_text:
                continue

            db = get_admin_db()
            restaurants_resp = (
                db.from_("restaurants")
                .select("id, name, slug, config_json")
                .is_("deleted_at", "null")
                .execute()
            )

            restaurant = None
            business_id = ""
            page_token = ""
            for r in (restaurants_resp.data or []):
                cfg = r.get("config_json") or {}
                integrations = cfg.get("integrations", {}) or {}
                if isinstance(integrations, list):
                    for i in integrations:
                        if i.get("id") == "instagram":
                            restaurant = r
                            business_id = (i.get("fields") or {}).get("business_id", "")
                            page_token = (i.get("fields") or {}).get("page_token", "")
                            break
                else:
                    ig = integrations.get("instagram", {})
                    if ig.get("business_id"):
                        restaurant = r
                        business_id = ig.get("business_id")
                        page_token = ig.get("page_token", "")

            if not restaurant:
                logger.warning("No restaurant found for Instagram business")
                continue

            restaurant_name = restaurant["name"]
            slug = restaurant["slug"]
            cfg = restaurant.get("config_json") or {}
            bot_settings = cfg.get("bot_settings") or {}
            greeting = bot_settings.get("greeting_message") or None
            bot_mode = bot_settings.get("bot_mode", "simple")

            if bot_mode == "interactive" and page_token:
                robo_url = os.environ.get("ROBO_API_URL", "http://localhost:8002")
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.post(
                            f"{robo_url}/robo/chat",
                            json={
                                "session_id": f"ig_{sender_id}",
                                "message": message_text,
                                "restaurant_id": restaurant["id"],
                                "restaurant_name": restaurant_name,
                                "slug": slug,
                                "source": "instagram",
                            },
                        )
                    if resp.status_code == 200:
                        reply = resp.json().get("response", "")
                        if reply:
                            from meta import send_instagram_message as ig_send
                            await ig_send(ig_business_id=business_id, token=page_token, igsid=sender_id, message=reply)
                    continue
                except Exception as e:
                    logger.warning("Failed to forward to robo-ia: %s", e)

            await send_instagram_menu_link(
                ig_business_id=business_id,
                token=page_token,
                igsid=sender_id,
                restaurant_name=restaurant_name,
                slug=slug,
                domain=APP_DOMAIN,
                greeting=greeting,
            )

    return {"status": "ok"}


@router.get("/webhook/instagram")
async def instagram_webhook_verify(request: Request):
    """Verificação do webhook do Instagram (handshake)."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    expected = os.environ.get("META_VERIFY_TOKEN", "appetito_meta_verify_2024")
    if mode == "subscribe" and token == expected:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================
# UBER EATS WEBHOOK
# ==========================================


class UberEatsNotification(BaseModel):
    event: str | None = None
    event_type: str | None = None
    data: dict | None = None
    store_id: str | None = None


@router.post("/webhook/ubereats")
async def ubereats_webhook(payload: UberEatsNotification):
    """Recebe notificações do UberEats (pedidos, atualizações)."""
    event = payload.event or payload.event_type or ""
    logger.info("UberEats webhook received: event=%s store_id=%s", event, payload.store_id)

    if not event:
        return {"status": "ignored", "reason": "no event type"}

    db = get_admin_db()
    order_data = payload.data or {}

    # Find restaurant by UberEats store_id
    restaurants_resp = (
        db.from_("restaurants")
        .select("id, name, slug, config_json")
        .is_("deleted_at", "null")
        .execute()
    )

    restaurant = None
    for r in (restaurants_resp.data or []):
        cfg = r.get("config_json") or {}
        integrations = cfg.get("integrations", {}) or {}
        if isinstance(integrations, list):
            for i in integrations:
                if i.get("id") == "uber_eats":
                    fields = i.get("fields", {}) or {}
                    if fields.get("store_id") == payload.store_id:
                        restaurant = r
                        break
        else:
            if integrations.get("uber_eats", {}).get("store_id") == payload.store_id:
                restaurant = r

    if not restaurant:
        logger.warning("No restaurant found for UberEats store: %s", payload.store_id)
        return {"status": "ignored", "reason": "restaurant not found"}

    if "created" in event.lower() or "new" in event.lower():
        external_id = order_data.get("order_id", "") or order_data.get("id", "")
        items = order_data.get("items", []) or []
        total = order_data.get("total", 0) or order_data.get("total_cents", 0)
        customer_name = order_data.get("customer", {}).get("name", "") or order_data.get("customer_name", "")

        db.from_("orders").insert({
            "restaurant_id": restaurant["id"],
            "order_source": "uber_eats",
            "status": "pending",
            "customer_name": customer_name,
            "total_cents": total if isinstance(total, int) else int(total * 100),
            "external_id": external_id,
            "notes": f"UberEats order {external_id}",
        }).execute()

        logger.info("UberEats order created for restaurant %s: %s", restaurant["id"], external_id)

    return {"status": "ok"}


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
