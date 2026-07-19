"""Telegram Bot API integration (official API)."""

import os
import httpx

TELEGRAM_API_BASE = 'https://api.telegram.org/bot'


async def send_message(bot_token: str, chat_id: str, text: str) -> dict:
    """Send a text message via Telegram Bot API."""
    url = f'{TELEGRAM_API_BASE}{bot_token}/sendMessage'
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'})
        resp.raise_for_status()
        return resp.json()


async def send_menu_link(
    bot_token: str, chat_id: str,
    restaurant_name: str, slug: str, domain: str | None = None,
    greeting: str | None = None,
) -> dict:
    """Send a menu link via Telegram."""
    base_domain = domain or os.environ.get('APP_DOMAIN', 'http://localhost:3000')
    menu_url = f'{base_domain}/{slug}?source=telegram'
    if greeting:
        text = f'{greeting}\n\n{menu_url}'
    else:
        text = (
            f'Olá! Seja bem-vindo(a) ao <b>{restaurant_name}</b>! 🎉\n\n'
            f'👇 Confira nosso cardápio digital:\n'
            f'{menu_url}\n\n'
            f'Aceitamos PIX, cartão de crédito e débito. 💳\n'
            f'Qualquer dúvida, é só chamar!'
        )
    return await send_message(bot_token, chat_id, text)
