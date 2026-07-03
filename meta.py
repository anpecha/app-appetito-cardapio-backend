"""Facebook / Instagram messaging via Meta Graph API (official API)."""

import os
import httpx

GRAPH_API_BASE = 'https://graph.facebook.com/v21.0'


async def send_facebook_message(page_id: str, token: str, psid: str, message: str) -> dict:
    """Send a text message to a Facebook Messenger user via Graph API."""
    url = f'{GRAPH_API_BASE}/{page_id}/messages'
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            params={'access_token': token},
            json={
                'recipient': {'id': psid},
                'message': {'text': message},
                'messaging_type': 'RESPONSE',
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_instagram_message(ig_business_id: str, token: str, igsid: str, message: str) -> dict:
    """Send a text message via Instagram Direct Messaging API."""
    url = f'{GRAPH_API_BASE}/{ig_business_id}/messages'
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            params={'access_token': token},
            json={
                'recipient': {'id': igsid},
                'message': {'text': message},
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_facebook_menu_link(
    page_id: str, token: str, psid: str,
    restaurant_name: str, slug: str, domain: str | None = None,
    greeting: str | None = None,
) -> dict:
    """Send a menu link via Facebook Messenger."""
    base_domain = domain or os.environ.get('APP_DOMAIN', 'http://localhost:3000')
    menu_url = f'{base_domain}/{slug}?source=facebook'
    if greeting:
        text = f'{greeting}\n\n{menu_url}'
    else:
        text = (
            f'Olá! Seja bem-vindo(a) ao {restaurant_name}! '
            f'Confira nosso cardápio: {menu_url}'
        )
    return await send_facebook_message(page_id, token, psid, text)


async def send_instagram_menu_link(
    ig_business_id: str, token: str, igsid: str,
    restaurant_name: str, slug: str, domain: str | None = None,
    greeting: str | None = None,
) -> dict:
    """Send a menu link via Instagram Direct."""
    base_domain = domain or os.environ.get('APP_DOMAIN', 'http://localhost:3000')
    menu_url = f'{base_domain}/{slug}?source=instagram'
    if greeting:
        text = f'{greeting}\n\n{menu_url}'
    else:
        text = (
            f'Olá! Seja bem-vindo(a) ao {restaurant_name}! '
            f'Confira nosso cardápio: {menu_url}'
        )
    return await send_instagram_message(ig_business_id, token, igsid, text)
