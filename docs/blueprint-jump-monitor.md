# Blueprint Técnico — Sistema de Gestão de Veículos Inadimplentes (Jump Park)

> Documento de referência para desenvolvimento assistido (Antigravity). Contém escopo, arquitetura, contratos de API já validados, e o plano de implementação faseado — começando por validação local antes de qualquer deploy em nuvem.

---

## 1. Visão Executiva

Ecossistema automatizado para gestão de veículos inadimplentes no Jump Park, atuando em três frentes:

1. **Worker de Desbloqueio** — processo em background, polling a cada 10s, libera a placa assim que a taxa de R$ 200,00 é registrada como "Paga".
2. **WebApp de Gestão** — interface para acionar bloqueios (vincular placa ao ID do cliente inadimplente) e gerenciar o fluxo de restrições.
3. **Data Pipeline e Monitoramento** — log estruturado de bloqueios/desbloqueios em Google Sheets / MySQL / BigQuery, alimentando dashboard no Looker Studio.

---

## 2. Status de Integração (já validado)

| Item | Valor / Observação |
|---|---|
| Base URL | `https://new-web.jumpparkapi.com.br` |
| Autenticação | Bearer token no header `Authorization` |
| Padrão de URL | `/api/{integrationId}/public/establishment/{establishmentId}/...` |
| Controle de acesso | Whitelist de **IP de origem** cadastrada no Site Admin |
| Header `Origin` | **Opcional** — só se aplica quando há domínio cadastrado |
| Rate limit | 120 requisições/minuto por token, exposto via header `X-RateLimit-Remaining` |
| Endpoint validado | `GET /clients` retornando `200 OK` e lista de clientes |

### ⚠ Regra de Negócio Crítica — Desbloqueio Cross-Estabelecimento

Uma placa bloqueada pode estar vinculada ao cliente `"CARRO BLOQUEADO"` em **mais de um estabelecimento** simultaneamente (COBRANÇA, CANAL e PRINCIPAL compartilham a mesma base de veículos). Portanto:

> **Ao detectar o pagamento da taxa de desbloqueio (R$ 200,00 pago) em qualquer estabelecimento, o worker DEVE remover a placa do cliente `"CARRO BLOQUEADO"` em TODOS os estabelecimentos nos quais ela estiver presente.**

---

## 3. Arquitetura Modular (`src/`)

A aplicação está organizada nos seguintes pacotes:
- `src/config.py`: Gerenciamento centralizado de variáveis e modelos de estabelecimentos.
- `src/core/`: Polling loop contínuo e chamadas à Jump Park API.
- `src/api/`: Servidor FastAPI para recepção de requisições manuais e healthcheck.
- `src/database/`: Persistência MySQL via SQLAlchemy ORM e scoped sessions.
- `src/bot/`: Bot interativo do Discord com comandos Slash e notificações.
- `deploy/`: Scripts de infraestrutura e serviço systemd Linux.
- `scripts/`: Utilitários de linha de comando.
