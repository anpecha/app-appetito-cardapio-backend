# CardapioDigital

Microserviço responsável pela integração de **WhatsApp** e **pagamentos** do sistema Appetito.

Quando um cliente envia qualquer mensagem no WhatsApp de um restaurante, este serviço identifica automaticamente o restaurante e responde com o link do cardápio digital. Também é responsável por gerar sessões de pagamento via **Stripe** e **Mercado Pago**, além de processar os webhooks de confirmação de pagamento.

---

## Índice

- [Visão Geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Pré-requisitos](#pré-requisitos)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Instalação e Execução](#instalação-e-execução)
  - [Desenvolvimento local (sem Docker)](#desenvolvimento-local-sem-docker)
  - [Com Docker Compose](#com-docker-compose)
- [Endpoints da API](#endpoints-da-api)
  - [Health Check](#health-check)
  - [WhatsApp Webhook](#whatsapp-webhook)
  - [Criar Pagamento](#criar-pagamento)
  - [Webhook Stripe](#webhook-stripe)
  - [Webhook Mercado Pago](#webhook-mercado-pago)
- [Fluxo de Funcionamento](#fluxo-de-funcionamento)
  - [Fluxo WhatsApp](#fluxo-whatsapp)
  - [Fluxo de Pagamento](#fluxo-de-pagamento)
- [Provedores de Pagamento](#provedores-de-pagamento)
  - [Stripe](#stripe)
  - [Mercado Pago](#mercado-pago)
- [Integração WhatsApp (Z-API)](#integração-whatsapp-z-api)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Dependências](#dependências)

---

## Visão Geral

| Atributo         | Valor                          |
|------------------|-------------------------------|
| Porta padrão     | `8003`                        |
| Framework        | FastAPI + Uvicorn             |
| Banco de dados   | Supabase (via Service Role Key)|
| WhatsApp         | Z-API / Evolution API         |
| Pagamentos       | Stripe e Mercado Pago         |
| Python           | 3.11+                         |

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                       CardapioDigital                       │
│                         (porta 8003)                        │
│                                                             │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────────────┐ │
│  │ router.py│   │ payments.py  │   │    whatsapp.py      │ │
│  │          │──▶│              │   │                     │ │
│  │ Endpoints│   │ Stripe       │   │ Envia link do       │ │
│  │ REST     │   │ Mercado Pago │   │ cardápio via Z-API  │ │
│  └──────────┘   └──────────────┘   └─────────────────────┘ │
│        │                                      │             │
│        ▼                                      ▼             │
│  ┌──────────┐                        ┌────────────────────┐ │
│  │database.py│                       │    Z-API           │ │
│  │ Supabase  │                       │  (WhatsApp API)    │ │
│  │ Admin SDK │                       └────────────────────┘ │
│  └──────────┘                                               │
└─────────────────────────────────────────────────────────────┘
```

O serviço é acessado pelo frontend Next.js através do proxy reverso configurado em `/api/proxy/services/cardapiodigital`.

---

## Pré-requisitos

- Python 3.11 ou superior
- Uma conta no [Supabase](https://supabase.com) com o projeto Appetito configurado
- Uma instância no [Z-API](https://z-api.io) para envio de mensagens WhatsApp
- Conta no [Stripe](https://stripe.com) e/ou [Mercado Pago](https://mercadopago.com.br) para pagamentos

---

## Variáveis de Ambiente

Copie o arquivo de exemplo e preencha com suas credenciais reais:

```bash
cp .env.example .env
```

| Variável                      | Obrigatório | Descrição                                                                 |
|-------------------------------|:-----------:|---------------------------------------------------------------------------|
| `SUPABASE_URL`                | ✅           | URL do projeto Supabase (ex: `https://xxxx.supabase.co`)                 |
| `SUPABASE_SERVICE_ROLE_KEY`   | ✅           | Chave de service role do Supabase (acesso admin, **nunca exponha no frontend**) |
| `WHATSAPP_API_URL`            | ✅           | URL base da Z-API (ex: `https://api.z-api.io`)                           |
| `WHATSAPP_INSTANCE_ID`        | ✅           | ID da instância Z-API do restaurante                                     |
| `WHATSAPP_INSTANCE_TOKEN`     | ✅           | Token da instância Z-API                                                 |
| `STRIPE_SECRET_KEY`           | ⚠️          | Chave secreta do Stripe (obrigatório se usar Stripe)                     |
| `STRIPE_WEBHOOK_SECRET`       | ⚠️          | Secret para verificar webhooks do Stripe (`whsec_...`)                   |
| `MERCADO_PAGO_ACCESS_TOKEN`   | ⚠️          | Access token do Mercado Pago (obrigatório se usar MP)                    |
| `MERCADO_PAGO_WEBHOOK_SECRET` | ⚠️          | Secret para verificar webhooks do Mercado Pago                           |
| `PORT`                        | ❌           | Porta do servidor (padrão: `8003`)                                       |
| `APP_DOMAIN`                  | ❌           | Domínio público do frontend (padrão: `http://localhost:3000`)            |
| `ALLOWED_ORIGINS`             | ❌           | Origens permitidas no CORS, separadas por vírgula                        |

> ⚠️ Pelo menos um provedor de pagamento deve estar configurado.

---

## Instalação e Execução

### Desenvolvimento local (sem Docker)

```bash
# 1. Crie e ative um ambiente virtual
python -m venv venv
source venv/bin/activate       # Linux/macOS
venv\Scripts\activate          # Windows

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env com suas credenciais

# 4. Inicie o servidor com hot reload
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
```

O serviço estará disponível em: `http://localhost:8003`

Documentação interativa (Swagger): `http://localhost:8003/docs`

### Com Docker Compose

```bash
# Configure o .env antes de subir
cp .env.example .env

# Subir o serviço
docker compose up --build

# Subir em background
docker compose up -d --build

# Ver logs
docker compose logs -f

# Parar
docker compose down
```

---

## Endpoints da API

### Health Check

```http
GET /health
```

**Resposta:**
```json
{
  "status": "ok",
  "service": "cardapiodigital"
}
```

---

### WhatsApp Webhook

```http
POST /webhook/whatsapp
```

Recebe webhooks do WhatsApp via Z-API ou Evolution API. O serviço identifica automaticamente qual restaurante está vinculado ao número receptor e responde ao cliente com o link do cardápio digital.

**Formato Z-API:**
```json
{
  "phone": "5511999999999",
  "text": "Oi",
  "instancePhone": "5511888888888",
  "instanceId": "abc123"
}
```

**Formato Evolution API:**
```json
{
  "data": {
    "key": { "from": "5511999999999@s.whatsapp.net" },
    "message": { "conversation": "Olá" },
    "instance": { "phone": "5511888888888" }
  }
}
```

**Resposta de sucesso:**
```json
{
  "status": "sent",
  "to": "5511999999999",
  "restaurant": "Pizzaria do João"
}
```

**Resposta quando restaurante não encontrado:**
```json
{
  "status": "ignored",
  "reason": "restaurant not found"
}
```

---

### Criar Pagamento

```http
POST /payments/create
```

Cria uma sessão de pagamento via Stripe (Checkout Session) ou Mercado Pago (Preference).

**Body:**
```json
{
  "provider": "stripe",
  "order_id": "uuid-do-pedido",
  "amount_cents": 4990,
  "description": "Pedido Appetito",
  "customer_email": "cliente@email.com",
  "customer_name": "João Silva"
}
```

| Campo            | Tipo     | Obrigatório | Descrição                                      |
|------------------|----------|:-----------:|------------------------------------------------|
| `provider`       | `string` | ✅           | `"stripe"` ou `"mercadopago"`                 |
| `order_id`       | `string` | ✅           | UUID do pedido no Supabase                    |
| `amount_cents`   | `int`    | ✅           | Valor em centavos (ex: `4990` = R$ 49,90)     |
| `description`    | `string` | ❌           | Descrição do item no checkout                 |
| `customer_email` | `string` | ❌           | Email do cliente para recibo                  |
| `customer_name`  | `string` | ❌           | Nome do cliente (usado no Mercado Pago)       |

**Resposta Stripe:**
```json
{
  "provider": "stripe",
  "session_id": "cs_test_...",
  "checkout_url": "https://checkout.stripe.com/...",
  "status": "pending"
}
```

**Resposta Mercado Pago:**
```json
{
  "provider": "mercadopago",
  "preference_id": "123456789-abc...",
  "init_point": "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=...",
  "sandbox_init_point": "https://sandbox.mercadopago.com.br/...",
  "status": "pending"
}
```

---

### Webhook Stripe

```http
POST /webhook/stripe
```

Endpoint para receber eventos do Stripe. Deve ser configurado no [Dashboard do Stripe](https://dashboard.stripe.com/webhooks).

Ao receber o evento `payment_intent.succeeded`, o pedido correspondente é atualizado no Supabase:
- `payment_status` → `"paid"`
- `status` → `"preparing"`

> A verificação da assinatura é feita automaticamente via `STRIPE_WEBHOOK_SECRET`.

---

### Webhook Mercado Pago

```http
POST /webhook/mercadopago
```

Endpoint para receber notificações IPN do Mercado Pago. Deve ser configurado como `notification_url` na preference.

Ao receber uma notificação de pagamento aprovado (`status: "approved"`), o pedido é atualizado no Supabase da mesma forma que o webhook do Stripe.

---

## Fluxo de Funcionamento

### Fluxo WhatsApp

```
Cliente envia mensagem no WhatsApp
         │
         ▼
Z-API dispara webhook → POST /webhook/whatsapp
         │
         ▼
Extrai o telefone do remetente e do destinatário
         │
         ▼
Busca o restaurante no Supabase pelo número WhatsApp configurado
  (campo config_json.whatsapp ou config_json.integrations.zapi_phone)
         │
         ▼
Encontrou restaurante?
  ├── NÃO → Retorna { status: "ignored" }
  └── SIM → Envia mensagem com link do cardápio via Z-API
              Ex: https://app.appetito.com.br/{slug}
```

### Fluxo de Pagamento

```
Frontend chama POST /payments/create com provider, order_id e amount
         │
         ▼
Stripe? → Cria Checkout Session → retorna checkout_url
         │
Mercado Pago? → Cria Preference → retorna init_point
         │
         ▼
Cliente é redirecionado para a página de pagamento
         │
         ▼
Pagamento aprovado? → Provedor envia webhook
         │
         ▼
POST /webhook/stripe ou /webhook/mercadopago
         │
         ▼
Atualiza pedido no Supabase: payment_status=paid, status=preparing
```

---

## Provedores de Pagamento

### Stripe

O serviço usa **Stripe Checkout Session** (não PaymentIntent diretamente), que redireciona o cliente para uma página de pagamento hospedada pelo Stripe.

- **Moeda:** BRL (Real Brasileiro)
- **Sucesso:** redireciona para `{APP_DOMAIN}/tracking/{order_id}?payment=success`
- **Cancelamento:** redireciona para `{APP_DOMAIN}/{slug}/checkout?payment=cancelled`
- **Webhook:** deve ser registrado no Dashboard do Stripe apontando para `/webhook/stripe`

**Configuração do webhook no Stripe CLI (desenvolvimento local):**
```bash
stripe listen --forward-to localhost:8003/webhook/stripe
```

### Mercado Pago

O serviço cria uma **Preference** do Mercado Pago, retornando o `init_point` para redirecionar o cliente.

- **Moeda:** BRL (Real Brasileiro)
- **Referência:** o `order_id` é enviado como `external_reference` para identificar o pedido no webhook
- **Webhook:** a URL de notificação é automaticamente configurada como `{APP_DOMAIN}/api/proxy/services/cardapiodigital/webhook/mercadopago`

---

## Integração WhatsApp (Z-API)

O serviço suporta dois formatos de webhook:

| Formato         | Campo do remetente                              | Campo do texto           |
|-----------------|------------------------------------------------|--------------------------|
| **Z-API**       | `body.phone`                                   | `body.text`              |
| **Evolution API** | `body.data.message.from` (remove `@s.whatsapp.net`) | `body.data.message.conversation` |

O número do **restaurante** (destinatário) é identificado via:
- Z-API: `body.instancePhone`
- Evolution API: `body.data.instance.phone`

A mensagem enviada ao cliente segue este padrão:
```
Olá! 🍽️

Aqui está o cardápio digital do *[Nome do Restaurante]*:
https://app.appetito.com.br/[slug]

Escolha seus produtos, faça o pedido e pague online com cartão ou PIX.
Em caso de dúvidas, fale diretamente com o restaurante.
```

---

## Estrutura do Projeto

```
CardapioDigital/
├── main.py            # Entrypoint FastAPI — configura app, CORS e inclui o router
├── router.py          # Todos os endpoints REST (webhook WhatsApp, pagamentos)
├── payments.py        # Integração com Stripe e Mercado Pago
├── whatsapp.py        # Envio de mensagens via Z-API
├── database.py        # Cliente Supabase (Service Role para acesso admin)
├── requirements.txt   # Dependências Python
├── Dockerfile         # Imagem Docker (Python 3.11-slim)
├── docker-compose.yml # Configuração Docker Compose para execução isolada
├── .env.example       # Template de variáveis de ambiente
└── README.md          # Esta documentação
```

---

## Dependências

| Biblioteca         | Versão mínima | Uso                                          |
|--------------------|:-------------:|----------------------------------------------|
| `fastapi`          | 0.115.0       | Framework web assíncrono                     |
| `uvicorn[standard]`| 0.32.0        | Servidor ASGI de produção                   |
| `supabase`         | 2.6.0         | SDK oficial do Supabase                     |
| `python-dotenv`    | 1.0.1         | Carregamento de variáveis do arquivo `.env`  |
| `stripe`           | 11.0.0        | SDK oficial do Stripe                        |
| `httpx`            | 0.28.0        | Cliente HTTP assíncrono (Mercado Pago, Z-API)|
| `pydantic`         | 2.10.0        | Validação e serialização de dados            |
