"""
find_blocked_client_id.py

Script utilitário para consultar o endpoint de clientes dos estabelecimentos
CANAL e PRINCIPAL na API Jump Park e localizar o clientId correspondente ao
cliente "CARRO BLOQUEADO".

O resultado pode ser copiado diretamente para o .env nos campos:
  CANAL_BLOCKED_CLIENT_ID=...
  PRINCIPAL_BLOCKED_CLIENT_ID=...

Uso:
  python find_blocked_client_id.py
"""

import json
import logging
import os
import socket
import sys

import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Carrega .env
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Patch IPv4-only (mesmo patch do main.py — necessário para a whitelist da API)
# ──────────────────────────────────────────────────────────────────────────────
_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(*args, **kwargs):
    return [
        addr
        for addr in _original_getaddrinfo(*args, **kwargs)
        if addr[0] == socket.AF_INET
    ]


socket.getaddrinfo = _getaddrinfo_ipv4_only
log.debug("Patch IPv4-only aplicado.")

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────
BASE_URL = "https://new-web.jumpparkapi.com.br"
BLOCKED_CLIENT_NAME = "CARRO BLOQUEADO"

# Estabelecimentos a pesquisar
TARGETS = [
    {
        "label": "CANAL",
        "prefix": "CANAL",
    },
    {
        "label": "PRINCIPAL",
        "prefix": "PRINCIPAL",
    },
]

# Headers padrão (mesmo User-Agent do main.py para evitar bloqueio do Cloudflare)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ──────────────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ──────────────────────────────────────────────────────────────────────────────

def build_headers(token: str, origin: str = "") -> dict:
    """Monta os headers HTTP para uma requisição à API Jump Park."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if origin:
        headers["Origin"] = origin
    return headers


def fetch_clients_page(
    integration_id: str,
    establishment_id: str,
    headers: dict,
    page: int = 1,
    per_page: int = 50,
) -> dict | None:
    """Busca uma página do endpoint GET /clients."""
    url = (
        f"{BASE_URL}/api/{integration_id}"
        f"/public/establishment/{establishment_id}"
        "/clients"
    )
    params = {
        "page": page,
        "perPage": per_page,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        log.error(
            "  Erro HTTP %s ao buscar clientes (página %d). Resposta: %s",
            resp.status_code, page, resp.text[:300],
        )
    except requests.exceptions.RequestException as exc:
        log.error("  Exceção na requisição (página %d): %s", page, exc)
    return None


def find_blocked_client(
    integration_id: str,
    establishment_id: str,
    token: str,
    origin: str = "",
) -> list[dict]:
    """
    Percorre todas as páginas do endpoint /clients e retorna todos os clientes
    cujo nome contenha BLOCKED_CLIENT_NAME (case-insensitive).
    """
    headers = build_headers(token, origin)
    found = []
    page = 1

    while True:
        log.info("  Buscando página %d...", page)
        data = fetch_clients_page(
            integration_id, establishment_id, headers, page=page
        )
        if data is None:
            break

        # A resposta pode ter a lista em "content" (raiz) ou em "data.content"
        content = data.get("content") or data.get("data", {}).get("content", [])
        if not content:
            log.info("  Página %d vazia — fim da lista.", page)
            break

        for client in content:
            name = (client.get("name") or "").strip().upper()
            if BLOCKED_CLIENT_NAME.upper() in name:
                found.append(client)

        last_page = data.get("lastPage", page)
        total = data.get("total", "?")
        log.info(
            "  Página %d/%s — %d clientes nesta página, %s total(is).",
            page, last_page, len(content), total,
        )

        if page >= last_page:
            break
        page += 1

    return found


# ──────────────────────────────────────────────────────────────────────────────
# Execução principal
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 70)
    log.info("Buscando BLOCKED_CLIENT_ID para estabelecimentos CANAL e PRINCIPAL")
    log.info("=" * 70)

    results = {}

    for target in TARGETS:
        prefix = target["prefix"]
        label = target["label"]

        integration_id = os.getenv(f"{prefix}_INTEGRATION_ID", "")
        establishment_id = os.getenv(f"{prefix}_ESTABLISHMENT_ID", "")
        token = os.getenv(f"{prefix}_ACCESS_TOKEN", "")
        origin = os.getenv(f"{prefix}_ORIGIN", "").strip()

        if not integration_id or not establishment_id or not token:
            log.warning(
                "[%s] Credenciais incompletas no .env — pulando. "
                "Verifique %s_INTEGRATION_ID, %s_ESTABLISHMENT_ID e %s_ACCESS_TOKEN.",
                label, prefix, prefix, prefix,
            )
            continue

        log.info("")
        log.info("─" * 50)
        log.info("[%s] Estabelecimento ID: %s | Integration ID: %s", label, establishment_id, integration_id)
        log.info("─" * 50)

        matches = find_blocked_client(integration_id, establishment_id, token, origin)

        if matches:
            log.info("")
            log.info("  ✅ Encontrado(s) %d cliente(s) com nome '%s':", len(matches), BLOCKED_CLIENT_NAME)
            for m in matches:
                client_id = m.get("clientId", "?")
                name = m.get("name", "?")
                log.info("     → clientId: %s  |  name: %s", client_id, name)
                results[prefix] = client_id

            # Mostra o JSON completo para verificação
            log.info("")
            log.info("  JSON completo dos clientes encontrados:")
            print(json.dumps(matches, indent=2, ensure_ascii=False))
        else:
            log.warning(
                "  ⚠ Nenhum cliente com nome '%s' encontrado no estabelecimento %s (%s).",
                BLOCKED_CLIENT_NAME, label, establishment_id,
            )
            log.info(
                "  Verifique se o cliente 'CARRO BLOQUEADO' já foi criado neste "
                "estabelecimento. Pode ser necessário criá-lo manualmente primeiro."
            )

    # ── Resumo final com valores para copiar para o .env ─────────────────
    log.info("")
    log.info("=" * 70)
    log.info("RESUMO — Copie para o .env:")
    log.info("=" * 70)

    if not results:
        log.warning("Nenhum BLOCKED_CLIENT_ID encontrado. Veja os avisos acima.")
        sys.exit(1)

    for prefix, client_id in results.items():
        env_var = f"{prefix}_BLOCKED_CLIENT_ID={client_id}"
        log.info("  %s", env_var)

    log.info("")
    log.info("Pronto! Cole os valores acima no arquivo .env.")


if __name__ == "__main__":
    main()
