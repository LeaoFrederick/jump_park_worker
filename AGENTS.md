# AGENTS.md — Regras & Skills do Projeto Jump Park Worker

Este arquivo define os padrões de arquitetura, segurança e as skills recomendadas para o desenvolvimento, manutenção e depuração deste projeto. O assistente de IA deve seguir rigorosamente as diretrizes abaixo em qualquer interação neste workspace.

---

## 🎯 Skills Aplicadas Neste Projeto

Ao trabalhar neste projeto, utilize prioritariamente as diretrizes das seguintes skills:

- **`@python-pro` & `@python-patterns`**:
  - Código idiomático em Python 3.12+, tipagem estática rigorosa (`typing`, `dataclasses`).
  - Concorrência segura entre threads (FastAPI daemon thread + loop de polling do worker).
  - Uso otimizado de `requests.Session` e tratamento granular de exceções de rede.

- **`@database-design`**:
  - Modelagem e persistência segura via SQLAlchemy e PyMySQL.
  - Pool de conexões resiliente (`pool_recycle`, `pool_pre_ping`) para evitar timeouts de conexão MySQL.
  - Transações atômicas e queries otimizadas com índices nos campos de status e datas.

- **`@api-security-best-practices` & `@backend-security-coder`**:
  - Leitura de credenciais exclusivamente a partir do `.env` (nunca expor tokens, chaves ou senhas em código).
  - Sanitização de logs (nunca registrar tokens Jump Park, senhas ou dados sensíveis de clientes).
  - Validação estrita de schemas em endpoints FastAPI (Pydantic / Dataclasses).
  - Respostas de erro seguras que não vazam stack traces para o cliente externo.

- **`@systematic-debugging`**:
  - Diagnóstico metódico de falhas na integração com a API da Jump Park (ex: erros 403 por IPv6 vs IPv4, expiração de tokens e rate limits).
  - Isolamento de causa raiz antes de aplicar correções.

- **`@concise-planning` & `@lint-and-validate`**:
  - Planejamento enxuto antes de refatorações complexas.
  - Código limpo, legível e em conformidade com PEP 8.

---

## 🏗️ Arquitetura & Diretrizes Críticas do Código

1. **Patch IPv4 Forçado:**
   - A Jump Park valida o IP de origem cadastrado no painel. O patch `_apply_ipv4_patch()` em `main.py` **deve ser preservado** para evitar falhas de autenticação (HTTP 403) em redes dual-stack.

2. **Timeouts Obrigatórios:**
   - Toda chamada HTTP (`requests.get`, `requests.post`, etc.) deve conter um `timeout` explícito para evitar travamento de threads.

3. **Ciclo de Vida & Graceful Shutdown:**
   - O worker roda como serviço Linux (`systemd`). Sempre tratar sinais `SIGTERM` e `SIGINT` para fechar sessões de banco e conexões de forma limpa.

4. **Multi-Estabelecimento:**
   - A lógica do worker deve sempre suportar isolamento por estabelecimento, respeitando as credenciais e configurações individuais de cada unidade.
