# Blueprint Técnico — Sistema de Gestão de Veículos Inadimplentes (Jump Park)

> Documento de referência para desenvolvimento assistido (Antigravity). Contém escopo, arquitetura, contratos de API já validados, e o plano de implementação faseado — começando por validação local antes de qualquer deploy em nuvem.

---

## 1. Visão Executiva

Ecossistema automatizado para gestão de veículos inadimplentes no Jump Park, atuando em três frentes:

1. **Worker de Desbloqueio** — processo em background, polling a cada 10s, libera a placa assim que a taxa de R$ 200,00 é registrada como "Paga".
2. **WebApp de Gestão** — interface para acionar bloqueios (vincular placa ao ID do cliente inadimplente) e gerenciar o fluxo de restrições.
3. **Data Pipeline e Monitoramento** — log estruturado de bloqueios/desbloqueios em Google Sheets / BigQuery, alimentando dashboard no Looker Studio.

**Objetivo da fase atual**: provar que a lógica de aquisição de dados (consulta de status de cobrança via API) funciona de forma confiável **rodando localmente**, antes de qualquer custo ou complexidade de infraestrutura em nuvem.

---

## 2. Status de Integração (já validado)

Estes pontos já foram confirmados em testes reais e servem como contrato fixo para o código:

| Item | Valor / Observação |
|---|---|
| Base URL | `https://new-web.jumpparkapi.com.br` |
| Autenticação | Bearer token no header `Authorization` |
| Padrão de URL | `/api/{integrationId}/public/establishment/{establishmentId}/...` |
| Controle de acesso | Whitelist de **IP de origem** cadastrada no Site Admin (não domínio, no cenário atual) |
| Header `Origin` | **Não necessário** — só se aplica quando há domínio cadastrado, o que não é o caso |
| Rate limit | 120 requisições/minuto por token, exposto via header `X-RateLimit-Remaining` |
| Endpoint validado | `GET /clients` retornando `200 OK` e lista de clientes |
| Establishment atual (Fase 1) | 1 estabelecimento liberado no plano contratado da API |
| Establishments futuros (Fase 2) | +2 estabelecimentos, mediante upgrade do plano comercial |

### ⚠ Regra de Negócio Crítica — Desbloqueio Cross-Estabelecimento

Uma placa bloqueada pode estar vinculada ao cliente `"CARRO BLOQUEADO"` em **mais de um estabelecimento** simultaneamente (COBRANÇA, CANAL e PRINCIPAL compartilham a mesma base de veículos). Portanto:

> **Ao detectar o pagamento da taxa de desbloqueio (R$ 200,00 pago) em qualquer estabelecimento, o worker DEVE remover a placa do cliente `"CARRO BLOQUEADO"` em TODOS os estabelecimentos nos quais ela estiver presente.**

Isso está implementado em `run_monitor()` via loop `for target_state in states` que itera sobre todos os estados antes de chamar `unlock_vehicle()`. Não alterar este comportamento sem atualizar esta seção.

### Endpoint de Ordens de Serviço (fonte real do pagamento)

Confirmado na documentação — **este é o endpoint que carrega a lógica de aquisição do pagamento**, não a fatura do cliente:

```
GET /api/{integrationId}/public/establishment/{establishmentId}/serviceorders/export/json
```

**Query params relevantes:**

| Param | Tipo | Uso no nosso caso |
|---|---|---|
| `startDate` / `endDate` | `YYYY-MM-DD` | Janela de busca — no worker, algo como "últimas 24h" é suficiente e mantém o payload pequeno |
| `startTime` / `endTime` | `HH:MM:SS` | Padrão `00:00:00` / `23:59:59` |
| `typeDateTime` | `entryDateTime` (padrão) ou `exitDateTime` | Manter padrão |
| `serviceOrderPlate` | string | **Filtro principal** — busca parcial por placa. Permite pedir à API só as OS daquela placa específica, em vez de baixar o JSON massivo inteiro |
| `search` | string | Alternativa: busca por placa, código, documento ou nome do cliente |

**Campos-chave dentro de cada item de `data.content[]`:**
- `plate` — placa do veículo.
- `totalAmount` — valor total da OS. **É este campo que deve ser comparado a `200.00`**, o valor cadastrado da taxa de restrição.
- `financialSituationName` — string tipo `"Pago"` / provavelmente `null` ou outro valor quando não pago.
- `operationSituationName` — ex: `"Finalizada"` / `"Aberta"`.
- `exitDateTime` — quando é `"0001-01-01 00:00:00"`, indica que o veículo ainda não saiu (OS em aberto).

**Armadilhas já identificadas e resolvidas** (documentar para não retrabalhar):
- Requisições podem sair por IPv6 em redes dual-stack, não batendo com o IPv4 cadastrado → forçar resolução IPv4.
- User-Agent padrão de bibliotecas HTTP pode ser bloqueado pelo Cloudflare (WAF) antes mesmo de chegar na aplicação da Jump → usar um `User-Agent` de navegador.
- Um `403` pode vir do **Cloudflare** (infraestrutura, página "Attention Required") ou da **API da Jump** (aplicação) — são causas diferentes; o corpo da resposta HTML entrega qual é qual.

---

### Endpoint de Clientes (vínculo placa ↔ bloqueio)

Estrutura real observada em `GET /clients` para um cliente bloqueado:

```json
{
  "content": [
    {
      "establishmentId": 18967,
      "clientId": "3326720251103174738",
      "name": "CARRO BLOQUEADO",
      "clientTypeId": 2,
      "hasInvoice": 1,
      "invoiceDateTime": "20251103174841",
      "clientInvoiceSituationId": 1,
      "dueDateTime": "2025-11-03 17:48:41",
      "invoiceAmount": 0.01
    }
  ],
  "total": 1,
  "perPage": 10,
  "currentPage": 1,
  "lastPage": 1
}
```

Ponto importante: `invoiceAmount` aqui é um valor simbólico (`0.01`), **não** o valor real da taxa de restrição. O objeto `client` serve para **identificar que aquele veículo está marcado como bloqueado** (convenção observada: `name: "CARRO BLOQUEADO"`), mas **não** é onde se confirma o pagamento. A confirmação de pagamento vem do endpoint de Ordens de Serviço, cruzando pela `plate`.

### Lógica de correlação (núcleo do worker)

```
1. Obter a lista de clientes bloqueados (GET /clients, filtrando por name/clientTypeId
   que identifique "CARRO BLOQUEADO" ou equivalente) -> extrair a placa vinculada.
2. Para cada placa bloqueada, consultar:
   GET /serviceorders/export/json?serviceOrderPlate={placa}&startDate=...&endDate=...
3. Nos itens retornados em data.content[], verificar se existe algum registro com:
     totalAmount == 200.00  E  financialSituationName == "Pago"
4. Se encontrado -> essa é a condição de "taxa paga" -> acionar desbloqueio da placa.
5. Se não encontrado -> manter bloqueado, tentar novamente no próximo ciclo (10s).
```

---

## 3. Arquitetura Alvo

### 3.1 Infraestrutura Core (Fase 2 — ainda não implementar)
- VM na Oracle Cloud, tier **Always Free** (`VM.Standard.A1.Flex` recomendado).
- Ubuntu, execução 24/7 via `systemd`.
- IP público da VM precisa ser cadastrado no Site Admin da Jump antes do primeiro deploy.

### 3.2 Stack
- **Linguagem**: Python (orquestra tanto o worker quanto o webapp).
- **Persistência de log**: Google Sheets (fase inicial) → BigQuery (escala).
- **BI**: Looker Studio, alimentado pelas planilhas/BigQuery.

### 3.3 Estrutura de projeto proposta

```
jump-monitor/
├── shared/
│   ├── jump_client.py        # cliente HTTP central: auth, headers, rate limit, retries
│   ├── config.py              # carregamento de variáveis de ambiente
│   └── models.py               # dataclasses: Cliente, Fatura, Restricao
├── worker/
│   ├── main.py                 # loop de polling (10s)
│   └── unlock_logic.py         # regra: fatura "Paga" -> remove restrição
├── webapp/
│   ├── app.py                   # interface para bloquear placa / vincular a cliente
│   └── templates/
├── pipeline/
│   └── sheets_writer.py         # grava eventos de bloqueio/desbloqueio
├── tests/
│   ├── test_jump_client.py
│   └── fixtures/                # respostas de API mockadas para testes offline
├── .env.example
├── requirements.txt
└── README.md
```

---

## 4. Fase 0 — Validação Local da Lógica de Aquisição (ESCOPO ATUAL)

Objetivo: provar, rodando na sua própria máquina, que é possível:
1. Autenticar corretamente na API.
2. Buscar a lista de clientes e localizar um cliente específico por placa/ID.
3. Consultar o status de cobrança (fatura) desse cliente.
4. Identificar corretamente quando uma fatura de R$ 200,00 está com status "Paga".
5. Rodar isso em loop respeitando o rate limit, sem quebrar em erros de rede.

### 4.1 Perguntas em aberto a responder nesta fase

O endpoint e os campos de leitura já estão confirmados (§2). O que falta validar com chamadas reais:

- [ ] Qual o(s) valor(es) exato(s) de `clientTypeId` / `name` que identificam de forma confiável um "cliente bloqueado" na sua base — `"CARRO BLOQUEADO"` é o padrão observado, mas confirmar se é sempre esse texto literal ou se varia.
- [ ] Confirmar se `financialSituationName == "Pago"` é suficiente, ou se existe também `financialSituationId` numérico mais confiável para comparar (evita depender de string exata).
- [ ] Definir a janela de `startDate`/`endDate` ideal para o polling — muito ampla deixa o payload desnecessariamente grande; muito curta arrisca perder uma OS paga fora da janela (ex: pagamento feito minutos antes do início do range).
- [ ] Existe endpoint de **restrição/bloqueio** para reverter o bloqueio da placa (ex: `PUT /clients/{clientId}` ou equivalente) — ainda não documentado nas páginas exploradas até agora; precisa ser localizado em "Clientes" ou "Veículos" na referência da API.

**Ação recomendada**: rodar o script de validação abaixo contra uma placa conhecida (uma que você bloqueou manualmente para teste) e inspecionar o JSON completo de Ordens de Serviço retornado, documentando em `docs/api-samples.md`.

### 4.2 Cliente HTTP central (`shared/jump_client.py`)

Contrato mínimo, já incorporando os aprendizados de autenticação/rate-limit:

```python
import requests
import logging
import time

logger = logging.getLogger("jump_client")

class JumpAPIError(Exception):
    pass

class JumpClient:
    def __init__(self, base_url, integration_id, establishment_id, token):
        self.base_url = base_url
        self.integration_id = integration_id
        self.establishment_id = establishment_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/{self.integration_id}/public/establishment/{self.establishment_id}{path}"

    def _request(self, method: str, path: str, **kwargs):
        url = self._url(path)
        try:
            resp = requests.request(method, url, headers=self.headers, timeout=10, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.error(f"Falha de conexão em {path}: {e}")
            raise JumpAPIError(f"Erro de conexão: {e}") from e

        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            logger.debug(f"Rate limit restante: {remaining}")
            if int(remaining) < 5:
                logger.warning("Rate limit quase esgotado, aguardando 5s extra.")
                time.sleep(5)

        if resp.status_code == 401:
            raise JumpAPIError("401 - token inválido ou expirado.")
        if resp.status_code == 403:
            if "cloudflare" in resp.text.lower():
                raise JumpAPIError("403 - bloqueio de infraestrutura (Cloudflare), não da API.")
            raise JumpAPIError("403 - sem permissão para este estabelecimento/recurso.")
        if not resp.ok:
            raise JumpAPIError(f"{resp.status_code} - {resp.text[:200]}")

        return resp.json()

    def list_clients(self, **params):
        return self._request("GET", "/clients", params=params)

    def get_service_orders(self, plate: str, start_date: str, end_date: str,
                            start_time: str = "00:00:00", end_time: str = "23:59:59"):
        """
        Busca ordens de serviço filtradas por placa, dentro de uma janela de datas.
        start_date/end_date no formato YYYY-MM-DD.
        """
        params = {
            "serviceOrderPlate": plate,
            "startDate": start_date,
            "endDate": end_date,
            "startTime": start_time,
            "endTime": end_time,
        }
        return self._request("GET", "/serviceorders/export/json", params=params)

    # Placeholder até localizarmos o endpoint real de reversão de bloqueio (ver §4.1)
    def set_restriction(self, client_id: str, blocked: bool):
        raise NotImplementedError("Endpoint de bloqueio/restrição ainda não confirmado")
```

### 4.3 Lógica de verificação de pagamento (`worker/unlock_logic.py`)

Função pura, fácil de testar isoladamente antes de plugar no loop de 10s:

```python
from datetime import datetime, timedelta

TAXA_RESTRICAO = 200.00

def taxa_restricao_paga(service_orders_response: dict, valor_esperado: float = TAXA_RESTRICAO) -> bool:
    """
    Recebe o JSON retornado por GET /serviceorders/export/json (já filtrado por placa)
    e retorna True se existir uma OS com o valor da taxa de restrição, paga.
    """
    content = service_orders_response.get("data", {}).get("content", [])
    for ordem in content:
        valor_bate = round(ordem.get("totalAmount", 0), 2) == round(valor_esperado, 2)
        esta_pago = ordem.get("financialSituationName") == "Pago"
        if valor_bate and esta_pago:
            return True
    return False


def janela_busca_padrao(horas_atras: int = 24):
    """Janela de busca para o polling: agora - N horas até agora."""
    agora = datetime.now()
    inicio = agora - timedelta(hours=horas_atras)
    return {
        "start_date": inicio.strftime("%Y-%m-%d"),
        "end_date": agora.strftime("%Y-%m-%d"),
    }
```

### 4.4 Script de validação local (`scripts/validate_local.py`)

Script isolado, fora do worker de produção, para explorar e confirmar o comportamento real da API antes de codar o loop final — usa uma placa conhecida (bloqueada manualmente para teste):

```python
import json
import os
from shared.jump_client import JumpClient
from worker.unlock_logic import taxa_restricao_paga, janela_busca_padrao

client = JumpClient(
    base_url="https://new-web.jumpparkapi.com.br",
    integration_id=os.environ["JUMP_INTEGRATION_ID"],
    establishment_id=os.environ["JUMP_ESTABLISHMENT_ID"],
    token=os.environ["JUMP_ACCESS_TOKEN"],
)

PLACA_TESTE = os.environ.get("PLACA_TESTE", "ABC1D23")  # troque pela placa bloqueada de teste

janela = janela_busca_padrao(horas_atras=48)
resultado = client.get_service_orders(plate=PLACA_TESTE, **janela)

print(f"Ordens de serviço encontradas para a placa {PLACA_TESTE}:")
print(json.dumps(resultado.get("data", {}).get("content", []), indent=2, ensure_ascii=False))

pago = taxa_restricao_paga(resultado)
print(f"\nTaxa de restrição (R$ 200,00) paga para essa placa? {pago}")

# Salva a amostra para consulta posterior, sem precisar chamar a API de novo
with open("docs/api-samples.json", "w", encoding="utf-8") as f:
    json.dump(resultado, f, indent=2, ensure_ascii=False)
```

### 4.5 Critério de saída da Fase 0

A Fase 0 está concluída quando:
- `taxa_restricao_paga()` identificar corretamente, contra dados reais, uma OS de R$ 200,00 paga vinculada a uma placa bloqueada de teste.
- A janela de busca (`startDate`/`endDate`) tiver sido calibrada — grande o suficiente para não perder pagamentos, pequena o suficiente para manter o payload leve.
- O campo de confirmação de pagamento (`financialSituationName` vs. `financialSituationId`) estiver decidido com base na estrutura real observada (§4.1).
- O endpoint de reversão de bloqueio tiver sido localizado e testado manualmente (mesmo que ainda não integrado ao loop).
- Um teste local, rodando em loop curto (ex: 5 iterações de 10s) contra a placa de teste, conseguir identificar corretamente o pagamento sem estourar rate limit nem quebrar em exceção não tratada.
- **Nenhuma chamada de desbloqueio automático real for feita ainda** — a validação é só de leitura; a ação de desbloqueio só entra depois que o webapp existir para permitir reversão manual em caso de erro.

---

## 5. Fase 1 — Prova de Conceito (após validação local)

- WebApp completo + 1 job de monitoramento, restrito ao estabelecimento já liberado no plano da API.
- Custo adicional zero.
- Objetivo: homologar com a diretoria.

## 6. Fase 2 — Escala e Expansão

- Upgrade do plano comercial da API para os outros 2 estabelecimentos.
- Worker único multi-estabelecimento (arquitetura atual) com desbloqueio cross-estabelecimento ativo.
- Consumo global estimado: 18 requisições/minuto (3 workers × 6/min) → **15% da capacidade de 120 req/min**, dentro de margem segura.
- Deploy na VM Oracle Cloud (ver §3.1), com IP da VM cadastrado na whitelist da Jump.

---

## 7. Fase 3 — Data Pipeline e Monitoramento (MySQL local + Looker Studio)

### Objetivo

Registrar todos os eventos de bloqueio e desbloqueio em um banco de dados MySQL rodando na **mesma VM Oracle** do worker, e expor os dados via **Looker Studio** (conectado diretamente ao MySQL pelo IP público da VM).

### 7.1 Arquitetura

```
┌──────────────────────────────────────────────────────────┐
│  VM Oracle Cloud (Always Free)                           │
│                                                          │
│  ┌─────────────┐    threading    ┌─────────────────┐     │
│  │   Worker     │◄──────────────►│  FastAPI (API)   │     │
│  │  (polling)   │                │  POST /api/      │     │
│  └──────┬───────┘                │  eventos         │     │
│         │                        └────────┬─────────┘     │
│         │  registrar_evento()             │               │
│         │                                 │               │
│         ▼                                 ▼               │
│  ┌──────────────────────────────────────────┐             │
│  │        MySQL (banco local)               │             │
│  │        tabela: eventos                   │             │
│  └──────────────────┬───────────────────────┘             │
│                     │                                     │
└─────────────────────┼─────────────────────────────────────┘
                      │ porta 3306 (IP público)
                      ▼
               ┌──────────────┐      ┌──────────────────┐
               │ Looker Studio│      │ WebApp (Google    │
               │ (dashboard)  │      │  Apps Script)     │
               └──────────────┘      └──────────────────┘
```

### 7.2 Schema do Banco — Tabela `eventos`

Definido via SQLAlchemy ORM em `database.py`:

| Coluna | Tipo | Nullable | Descrição |
|---|---|---|---|
| `id` | Integer (PK, auto) | Não | Chave primária |
| `timestamp` | DateTime | Não | Quando o evento ocorreu |
| `evento` | String(20) | Não | `"BLOQUEIO"` ou `"DESBLOQUEIO"` |
| `metodo` | String(20) | Não | `"AUTOMATICO"` ou `"MANUAL"` |
| `autor` | String(100) | Não | `"System Worker"` ou nome do operador |
| `motivo` | Text | Não | Justificativa da ação |
| `placa` | String(20) | Não | Placa do veículo (indexada) |
| `cliente_id` | String(50) | Não | ID do cliente Jump Park (texto longo) |
| `estabelecimento_origem` | String(50) | Não | Onde o pagamento foi detectado |
| `estabelecimentos_afetados` | Text | Não | Lista separada por vírgula |
| `os_id` | String(100) | Sim | ID da OS que confirmou pagamento |
| `valor_taxa` | Float | Sim | Valor da taxa cobrada |
| `status_financeiro` | String(50) | Sim | Ex: `"Pago"` |
| `exit_datetime` | DateTime | Sim | Data/hora de saída do veículo |

### 7.3 API HTTP (FastAPI)

Módulo `api.py` — roda em thread daemon no mesmo processo do worker:

| Rota | Método | Descrição |
|---|---|---|
| `/api/eventos` | POST | Recebe JSON do WebApp, valida via Pydantic, grava no banco |
| `/api/health` | GET | Healthcheck simples |

CORS totalmente aberto para permitir chamadas do Google Apps Script.

### 7.4 Integração no Worker

O worker (`main.py`) chama `database.registrar_evento()` diretamente após cada `unlock_vehicle()` bem-sucedido:

```python
registrar_evento(
    evento="DESBLOQUEIO",
    metodo="AUTOMATICO",
    autor="System Worker",
    motivo=f"Taxa R$ {TAXA_VALOR:.2f} paga — OS detectada em {tag}",
    placa=plate,
    cliente_id=cfg.blocked_client_id,
    estabelecimento_origem=tag,
    estabelecimentos_afetados=", ".join(unlocked_from),
    os_id=info.get("os_id"),
    valor_taxa=TAXA_VALOR,
    status_financeiro=info.get("status_financeiro"),
    exit_datetime=exit_dt,
)
```

### 7.5 Dashboard Looker Studio

Conectar ao MySQL da VM Oracle via **MySQL connector** (IP público + porta 3306). Painéis previstos:

- **KPIs principais**: total de bloqueios (período), total de desbloqueios, taxa de conversão, receita total.
- **Timeline**: eventos de bloqueio/desbloqueio no tempo (gráfico de linha).
- **Tabela de placas ativas**: lista ao vivo de placas ainda bloqueadas com data do bloqueio.
- **Filtro por estabelecimento**: COBRANÇA / CANAL / PRINCIPAL.
- **Filtro por método**: AUTOMATICO / MANUAL.

### 7.6 Variáveis de Ambiente Necessárias

```dotenv
# Banco de Dados (MySQL local na VM Oracle)
DATABASE_URL=mysql+pymysql://jump_user:SENHA@localhost:3306/jump_park

# API HTTP (FastAPI — recebe bloqueios manuais do WebApp)
API_HOST=0.0.0.0
API_PORT=8000
```

### 7.7 Critério de Saída da Fase 3

- [ ] MySQL instalado e rodando na VM Oracle Cloud.
- [ ] Tabela `eventos` criada automaticamente pelo `init_db()`.
- [ ] Worker registra desbloqueios automáticos no banco sem erros.
- [ ] `POST /api/eventos` aceita e grava bloqueios manuais do WebApp.
- [ ] Looker Studio conectado ao MySQL da VM e exibindo dados ao vivo.
- [ ] Firewall da VM Oracle libera portas 3306 (MySQL) e 8000 (API).

---

## 8. Segurança e Boas Práticas (aplicar desde a Fase 0)

- Token nunca versionado em código-fonte — sempre via variável de ambiente / `.env` (adicionar `.env` ao `.gitignore` desde o primeiro commit).
- Rotação periódica de credenciais; revogação imediata se exposto.
- Tratamento de exceção em toda chamada de API (`try/except` já embutido no `JumpClient`).
- Logs estruturados desde o início (facilita depuração quando migrar para a VM, onde não há acesso visual direto ao terminal o tempo todo).

---

## 9. Próximos Passos Imediatos

1. Bloquear manualmente uma placa de teste no sistema (gerando o registro `"CARRO BLOQUEADO"` em `/clients`).
2. Simular/realizar um pagamento de R$ 200,00 vinculado a essa placa, gerando a OS correspondente.
3. Rodar `scripts/validate_local.py` contra essa placa e confirmar que `taxa_restricao_paga()` retorna `True` no momento certo.
4. Documentar a estrutura real em `docs/api-samples.md` e ajustar `financialSituationName`/`financialSituationId` conforme observado.
5. Localizar e testar manualmente (fora do worker) o endpoint de reversão de bloqueio.
6. Só então implementar `set_restriction()` no `JumpClient` e rodar o worker completo localmente em loop controlado (não 24/7 ainda).
7. Por último, avançar para §5 (WebApp) e §6 (deploy Oracle Cloud).