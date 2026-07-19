"""WhatsApp integration via Z-API provider."""

import os
import httpx

BASE_URL = os.environ.get("WHATSAPP_API_URL", "https://api.z-api.io")
INSTANCE_ID = os.environ.get("WHATSAPP_INSTANCE_ID", "")
INSTANCE_TOKEN = os.environ.get("WHATSAPP_INSTANCE_TOKEN", "")


async def send_text(phone: str, message: str) -> dict:
    """Send a text message via Z-API."""
    url = f"{BASE_URL}/instances/{INSTANCE_ID}/token/{INSTANCE_TOKEN}/send-text"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json={"phone": phone, "message": message},
        )
        resp.raise_for_status()
        return resp.json()


async def send_link(phone: str, link: str, title: str = "Cardapio Digital", description: str = "Clique para ver nosso cardapio") -> dict:
    """Send a link preview via Z-API."""
    url = f"{BASE_URL}/instances/{INSTANCE_ID}/token/{INSTANCE_TOKEN}/send-link"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json={
                "phone": phone,
                "message": description,
                "linkUrl": link,
                "title": title,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_menu_link(
    phone: str,
    restaurant_name: str,
    slug: str,
    domain: str | None = None,
    source: str = "website",
    greeting: str | None = None,
) -> dict:
    """Send the digital menu link for a restaurant with source tracking."""
    base_domain = domain or os.environ.get("APP_DOMAIN", "http://localhost:3000")
    menu_url = f"{base_domain}/{slug}?source={source}"

    if greeting:
        welcome = f"{greeting}\n\n{menu_url}"
    else:
        welcome = (
            f"Olá! Seja muito bem-vindo(a) ao *{restaurant_name}*! 🎉\n\n"
            f"É um prazer ter você por aqui. 😊\n\n"
            f"👇 Confira nosso cardápio digital e faça seu pedido:\n"
            f"{menu_url}\n\n"
            f"Aceitamos PIX, cartão de crédito e débito. 💳✨\n"
            f"Se preferir, você também pode pagar na entrega em dinheiro ou cartão.\n\n"
            f"Qualquer dúvida, é só chamar! Estamos à disposição. 💛"
        )
    return await send_text(phone, welcome)
