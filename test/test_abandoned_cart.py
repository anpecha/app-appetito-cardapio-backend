"""BDD: 10-api-carrinho.feature — Carrinho Abandonado"""


class TestAbandonedCart:
    """API — Carrinho Abandonado"""

    def test_store_and_send_whatsapp_on_success(self, client, mock_supabase, mock_whatsapp):
        """Carrinho abandonado é registrado no Supabase e WhatsApp enviado"""
        payload = {
            'restaurant_id': 'rest-123',
            'customer_phone': '5511999998888',
            'customer_name': 'João Silva',
            'slug': 'hamburgueria-legal',
            'item_count': 3,
            'cart_total_cents': 4500,
            'items_summary': 'X-Burger, Coca-Cola, Batata Frita',
        }

        response = client.post('/abandoned-cart/remind', json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'sent'
        assert data['phone'] == '5511999998888'

        mock_whatsapp.assert_awaited_once()
        call_kwargs = mock_whatsapp.call_args[1]
        assert call_kwargs['phone'] == '5511999998888'
        assert 'hamburgueria-legal' in call_kwargs['message']
        assert 'João Silva' in call_kwargs['message']
        assert '3 itens' in call_kwargs['message']

    def test_db_failure_still_returns_200(self, client, mock_supabase, mock_whatsapp):
        """Falha ao salvar no DB ainda retorna 200 (registro não é crítico)"""
        payload = {
            'restaurant_id': 'rest-456',
            'customer_phone': '5511999997777',
            'customer_name': 'Maria Santos',
            'slug': 'pizza-place',
            'item_count': 2,
            'cart_total_cents': 3200,
        }

        mock_supabase.from_ = MagicMock(side_effect=Exception('DB error'))

        response = client.post('/abandoned-cart/remind', json=payload)

        assert response.status_code == 200
        assert response.json()['status'] == 'sent'

    def test_whatsapp_failure_returns_error_status(self, client, mock_supabase, mock_whatsapp):
        """Falha no WhatsApp retorna status error"""
        mock_whatsapp.side_effect = Exception('Z-API error')

        payload = {
            'restaurant_id': 'rest-789',
            'customer_phone': '5511999996666',
            'customer_name': 'Carlos Pereira',
            'slug': 'sushi-bar',
            'item_count': 5,
            'cart_total_cents': 8900,
        }

        response = client.post('/abandoned-cart/remind', json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'error'
        assert 'Z-API error' in data['detail']

    def test_invalid_payload_returns_422(self, client, mock_supabase, mock_whatsapp):
        """Payload inválido retorna 422"""
        response = client.post('/abandoned-cart/remind', json={
            'restaurant_id': 'rest-123',
        })

        assert response.status_code == 422
