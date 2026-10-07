# Gateway de Comunicação

Microserviço adaptador entre WhatsApp Cloud API / Microsoft Outlook e sistema central de atendimento com aprovação humana.

## Arquitetura

```
WhatsApp/Outlook → Webhook → Gateway → API Central → Aprovação Humana → Gateway → WhatsApp/Outlook
```

## Requisitos

- Python 3.12+
- Docker & Docker Compose
- SQLite (padrão) ou PostgreSQL (produção)
- Redis (opcional, Fase 4)

## Instalação Local (Docker)

```bash
# Clone o repositório
git clone https://github.com/mateuscordeiro011/Gateway-comunicacao.git
cd Gateway-comunicacao

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com suas credenciais (chmod 600 .env em Linux/Mac)

# Gerar segredos seguros
openssl rand -hex 32  # para cada segredo (GATEWAY_API_KEY, etc.)

# Copiar configuração de mapeamento WhatsApp
cp config/whatsapp_company_mapping.json.example config/whatsapp_company_mapping.json
# Editar com seus números e domínios

# Subir com Docker Compose (produção)
docker compose up -d --build

# Verificar saúde
curl http://127.0.0.1:8001/health

# Ver logs
docker compose logs -f app
```

## Desenvolvimento Local (com hot reload)

```bash
# Subir ambiente de desenvolvimento
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Acessar shell no container
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app sh

# Executar testes
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test run --rm test

# Lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps app ruff check .
```

## Configuração

### Variáveis de Ambiente (.env)

| Variável | Descrição | Obrigatório |
|----------|-----------|-------------|
| `CENTRAL_API_URL` | URL da API central | Sim |
| `GATEWAY_API_KEY` | Chave para endpoints outbound | Sim |
| `WHATSAPP_VERIFY_TOKEN` | Token de verificação do webhook | Sim |
| `WHATSAPP_APP_SECRET` | App Secret do Meta | Sim |
| `WHATSAPP_ACCESS_TOKEN` | Access Token do WhatsApp | Sim |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone Number ID | Sim |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | Business Account ID | Sim |
| `WHATSAPP_COMPANY_MAPPING_FILE` | Caminho para arquivo JSON de mapeamento | Sim |
| `GRAPH_TENANT_ID` | Tenant ID do Azure AD | Sim |
| `GRAPH_CLIENT_ID` | Client ID do App Registration | Sim |
| `GRAPH_CLIENT_SECRET` | Client Secret | Sim |
| `GRAPH_NOTIFICATION_CLIENT_STATE` | String aleatória para validação | Sim |
| `GRAPH_SUBSCRIPTION_RESOURCE` | Resource para subscription | Sim |
| `DATABASE_URL` | URL do banco (sqlite+aiosqlite:///./data/gateway.db) | Não |
| `IDEMPOTENCY_BACKEND` | `database` ou `memory` | Não |
| `LOG_LEVEL` | Nível de log (INFO, DEBUG) | Não |

### Mapeamento WhatsApp (config/whatsapp_company_mapping.json)

```json
{
  "5511999999999": "empresa1.com",
  "5511888888888": "empresa2.com"
}
```

Chave: telefone E.164 sem `+` | Valor: domínio da empresa

## Endpoints

### Inbound (Webhooks)

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/webhook/whatsapp` | GET | Verificação do webhook (hub.challenge) |
| `/webhook/whatsapp` | POST | Receber mensagens WhatsApp |
| `/webhook/outlook` | GET | Validação inicial (validationToken) |
| `/webhook/outlook` | POST | Notificações de mudança do Graph |

### Outbound (Chamados pela API Central)

| Endpoint | Método | Auth | Descrição |
|----------|--------|------|-----------|
| `/api/v1/enviar/whatsapp` | POST | X-API-Key | Enviar mensagem WhatsApp |
| `/api/v1/enviar/outlook` | POST | X-API-Key | Responder e-mail Outlook |

### Administrativos

| Endpoint | Método | Auth | Descrição |
|----------|--------|------|-----------|
| `/health` | GET | - | Health check |
| `/admin/subscriptions/outlook/create` | POST | X-API-Key | Criar subscription Graph |
| `/admin/subscriptions/outlook/renew/{id}` | POST | X-API-Key | Renovar subscription |
| `/admin/subscriptions/outlook/{id}` | DELETE | X-API-Key | Deletar subscription |

## Payloads

### Inbound para API Central (POST /api/v1/mensagens/receber)

```json
{
  "canal": "whatsapp",
  "cliente_nome": "João Silva",
  "cliente_identificador": "5511999999999",
  "empresa_dominio": "empresa1.com",
  "mensagem_id_externo": "wamid.xxx",
  "pergunta": "Olá, preciso de ajuda"
}
```

### Outbound WhatsApp (POST /api/v1/enviar/whatsapp)

```json
{
  "telefone": "5511999999999",
  "mensagem": "Olá! Como posso ajudar?"
}
```

### Outbound Outlook (POST /api/v1/enviar/outlook)

```json
{
  "message_id": "AAMkAD...",
  "resposta": "Olá! Sua solicitação foi recebida.",
  "reply_all": false
}
```

## Docker

### Produção

```bash
# Build e execução
docker compose up -d --build

# Verificar status
docker compose ps

# Ver logs
docker compose logs -f app

# Parar
docker compose down

# Parar e remover volumes (cuidado: apaga o banco)
docker compose down -v
```

### Desenvolvimento

```bash
# Com hot reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Testes
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test run --rm test

# Lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps app ruff check .

# Shell no container
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app sh
```

### Backup do banco

```bash
docker run --rm -v gateway-comunicacao_gateway_data:/d -v "$PWD":/b alpine tar czf /b/backup-$(date +%F).tgz -C /d .
```

## Testes

```bash
# Executar todos os testes com cobertura
pytest

# Apenas testes unitários
pytest tests/ -v

# Com relatório de cobertura
pytest --cov=app --cov-report=html
```

## Lint

```bash
# Verificar código
ruff check .

# Formatar código
ruff format .
```

## Meta for Developers - WhatsApp Cloud API

1. Acesse [Meta for Developers](https://developers.facebook.com/)
2. Crie um App do tipo "Business"
3. Adicione o produto "WhatsApp"
4. Configure:
   - **Webhook URL**: `https://seu-dominio.com/webhook/whatsapp`
   - **Verify Token**: valor de `WHATSAPP_VERIFY_TOKEN`
   - **Campos**: `messages`, `message_deliveries`, `message_reads`
5. Obtenha:
   - **App Secret** → `WHATSAPP_APP_SECRET`
   - **Access Token** → `WHATSAPP_ACCESS_TOKEN`
   - **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - **WhatsApp Business Account ID** → `WHATSAPP_BUSINESS_ACCOUNT_ID`

## Microsoft Entra ID - Outlook/Graph

1. Acesse [Portal Azure](https://portal.azure.com/)
2. **Microsoft Entra ID** → **App registrations** → **New registration**
3. **API permissions** → **Add permission** → **Microsoft Graph** → **Application permissions**:
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `Mail.Send`
4. **Grant admin consent**
5. **Certificates & secrets** → **New client secret** → `GRAPH_CLIENT_SECRET`
6. **Overview** → copie **Directory (tenant) ID** → `GRAPH_TENANT_ID` e **Application (client) ID** → `GRAPH_CLIENT_ID`

## Idempotência

- Backend padrão: `database` (SQLite com unique constraint)
- Chave: `{canal}:{mensagem_id_externo}`
- TTL: 24h (configurável via `IDEMPOTENCY_TTL_SECONDS`)

## Retries

- Configuráveis via `RETRY_MAX_ATTEMPTS`, `RETRY_MIN_WAIT`, `RETRY_MAX_WAIT`
- Aplicados para: HTTP 5xx, timeouts, erros de conexão
- NÃO aplicados para: 4xx (exceto 429 se configurado)

## Logs

- Formato JSON estruturado
- Dados sensíveis (tokens, secrets, API keys) automaticamente redigidos
- Nível configurável via `LOG_LEVEL`

## Estrutura do Projeto

```
gateway-comunicacao/
├── app/
│   ├── main.py                 # FastAPI app + rotas
│   ├── core/
│   │   ├── config.py           # Configurações (Pydantic Settings)
│   │   ├── logging_config.py   # Logs JSON estruturados
│   │   ├── security.py         # Validação API Key
│   │   └── idempotency.py      # Idempotência (DB + memória)
│   ├── schemas/
│   │   └── models.py           # Modelos Pydantic
│   ├── clients/
│   │   └── central_client.py   # Cliente HTTP API Central
│   └── integrations/
│       ├── whatsapp.py         # Integração WhatsApp
│       └── outlook.py          # Integração Outlook/Graph
├── config/
│   └── whatsapp_company_mapping.json.example
├── tests/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
└── README.md
```

## Troubleshooting

### Webhook WhatsApp não recebe mensagens
- Verifique se a URL é acessível publicamente (use ngrok para teste local)
- Confirme `WHATSAPP_VERIFY_TOKEN` no Meta e no `.env`
- Verifique logs para erros de HMAC

### Erro 401 no Graph API
- Confirme `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`
- Verifique se **Admin Consent** foi concedido para as permissões
- Confirme que a caixa de correio monitorada tem licença M365

### Duplicatas não sendo bloqueadas
- Verifique se `IDEMPOTENCY_BACKEND=database`
- Confirme se o banco está sendo criado (`data/gateway.db`)
- Verifique logs de idempotência

## Custos

- **WhatsApp Cloud API**: 1.000 conversas "Serviço" grátis/mês. Templates "Marketing"/"Utilidade" são cobrados.
- **Microsoft Graph**: Gratuito (requer licença M365 ativa nas caixas monitoradas).
- **Infraestrutura**: 100% local/open-source (Docker, SQLite, Redis opcional).

## Licença

MIT