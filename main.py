"""
jump_park_worker/main.py
Worker de monitoramento do Jump Park.

Lógica migrada de:
  - test_jump_monitor.py   → loop principal, cache de placas, cruzamento, desbloqueio
  - test_jump_api.py       → cliente HTTP com patch IPv4, autenticação Bearer
  - test_service_orders.py → busca de ordens de serviço com filtro de período

Credenciais lidas exclusivamente do .env (nunca hardcoded).
"""

import json
import logging
import os
import socket
import time
from datetime import datetime, timedelta

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
# Carrega .env antes de qualquer leitura de variável de ambiente
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Patch IPv4-only
#
# Muitas redes são "dual-stack" (IPv4 + IPv6). Por padrão o sistema pode
# preferir IPv6, mas o IP cadastrado no Site Admin da Jump é IPv4. Sem esse
# patch a requisição sai por um IP diferente do cadastrado, causando 403.
# ──────────────────────────────────────────────────────────────────────────────
def _apply_ipv4_patch() -> None:
    _original_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo_ipv4_only(*args, **kwargs):
        return [
            addr
            for addr in _original_getaddrinfo(*args, **kwargs)
            if addr[0] == socket.AF_INET
        ]

    socket.getaddrinfo = _getaddrinfo_ipv4_only
    log.debug("Patch IPv4-only aplicado.")


_apply_ipv4_patch()

# ──────────────────────────────────────────────────────────────────────────────
# Configurações (carregadas do .env)
# ──────────────────────────────────────────────────────────────────────────────
BASE_URL          = "https://new-web.jumpparkapi.com.br"
INTEGRATION_ID    = os.getenv("JUMP_INTEGRATION_ID")
ESTABLISHMENT_ID  = os.getenv("JUMP_ESTABLISHMENT_ID")
TOKEN             = os.getenv("JUMP_ACCESS_TOKEN")
BLOCKED_CLIENT_ID = os.getenv("JUMP_BLOCKED_CLIENT_ID")  # ID do cliente "CARRO BLOQUEADO"
ORIGIN            = os.getenv("JUMP_ORIGIN", "").strip()  # Domínio cadastrado no Site Admin (opcional)

# Parâmetros do worker
POLLING_INTERVAL  = int(os.getenv("POLLING_INTERVAL_SECONDS", "10"))   # segundos entre ciclos
CACHE_DURATION    = int(os.getenv("CACHE_DURATION_SECONDS", "1800"))    # 30 min
TAXA_VALOR        = float(os.getenv("TAXA_BLOQUEIO_VALOR", "200.00"))   # valor da taxa de desbloqueio
WINDOW_DAYS       = int(os.getenv("SEARCH_WINDOW_DAYS", "7"))           # janela de busca de OS

_REQUIRED_VARS = [
    ("JUMP_INTEGRATION_ID", INTEGRATION_ID),
    ("JUMP_ESTABLISHMENT_ID", ESTABLISHMENT_ID),
    ("JUMP_ACCESS_TOKEN", TOKEN),
    ("JUMP_BLOCKED_CLIENT_ID", BLOCKED_CLIENT_ID),
]

# ──────────────────────────────────────────────────────────────────────────────
# Headers HTTP padrão
#
# User-Agent explícito: o padrão da lib requests (python-requests/x.x) é
# bloqueado por WAFs como o Cloudflare que ficam na frente do servidor Jump.
# ──────────────────────────────────────────────────────────────────────────────
def _build_headers() -> dict:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    # Se um domínio foi cadastrado no Site Admin, o header Origin é obrigatório
    if ORIGIN:
        headers["Origin"] = ORIGIN
    return headers


# ──────────────────────────────────────────────────────────────────────────────
# Funções de API
# ──────────────────────────────────────────────────────────────────────────────

def get_blocked_plates() -> list[str]:
    """Busca as placas vinculadas ao cliente 'CARRO BLOQUEADO'."""
    url = (
        f"{BASE_URL}/api/{INTEGRATION_ID}"
        f"/public/establishment/{ESTABLISHMENT_ID}"
        f"/clients/{BLOCKED_CLIENT_ID}/vehicles"
    )
    try:
        response = requests.get(url, headers=_build_headers(), timeout=10)
        if response.status_code == 200:
            data = response.json().get("data", [])
            plates = [v.get("plate") for v in data if v.get("plate")]
            return plates
        if response.status_code == 401:
            log.error(
                "[CACHE] Erro ao buscar veículos: HTTP 401 (Não autorizado). "
                "Verifique se JUMP_ACCESS_TOKEN e JUMP_INTEGRATION_ID estão corretos e se "
                "JUMP_ORIGIN está definido com o domínio cadastrado no Site Admin.\n"
                "  URL usada: %s", url,
            )
        else:
            log.error("[CACHE] Erro ao buscar veículos: HTTP %s", response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[CACHE] Exceção na requisição: %s", exc)
    return []


def get_service_orders(window_days: int = WINDOW_DAYS) -> list[dict]:
    """Busca ordens de serviço dos últimos `window_days` dias."""
    url = (
        f"{BASE_URL}/api/{INTEGRATION_ID}"
        f"/public/establishment/{ESTABLISHMENT_ID}"
        "/serviceorders/export/json"
    )
    agora  = datetime.now()
    inicio = agora - timedelta(days=window_days)
    params = {
        "startDate": inicio.strftime("%Y-%m-%d"),
        "endDate":   agora.strftime("%Y-%m-%d"),
        "startTime": "00:00:00",
        "endTime":   "23:59:59",
    }
    try:
        response = requests.get(url, headers=_build_headers(), params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("data", {}).get("content", [])
        log.error("[API] Erro ao buscar ordens: HTTP %s", response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[API] Exceção na requisição: %s", exc)
    return []


def unlock_vehicle(plate: str) -> bool:
    """Remove o veículo da conta 'CARRO BLOQUEADO', liberando-o no sistema."""
    url = (
        f"{BASE_URL}/api/{INTEGRATION_ID}"
        f"/public/establishment/{ESTABLISHMENT_ID}"
        f"/clients/{BLOCKED_CLIENT_ID}/vehicles/{plate}"
    )
    try:
        response = requests.delete(url, headers=_build_headers(), timeout=10)
        if response.status_code == 200:
            log.info("[UNLOCK] Placa %s desbloqueada com sucesso.", plate)
            return True
        log.error(
            "[UNLOCK] Falha ao desbloquear %s: HTTP %s — %s",
            plate, response.status_code, response.text,
        )
    except requests.exceptions.RequestException as exc:
        log.error("[UNLOCK] Exceção ao desbloquear %s: %s", plate, exc)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Loop principal do worker
# ──────────────────────────────────────────────────────────────────────────────

def run_monitor() -> None:
    """Executa o monitoramento em loop contínuo."""
    log.info("Iniciando worker de monitoramento...")
    log.info(
        "Estabelecimento: %s | Cliente Bloqueio: %s | Polling: %ss | Cache: %ss",
        ESTABLISHMENT_ID, BLOCKED_CLIENT_ID, POLLING_INTERVAL, CACHE_DURATION,
    )

    cache_plates: list[str] = []
    last_cache_update: float = 0.0

    while True:
        now = time.time()

        # ── 1. Atualiza cache de placas bloqueadas a cada CACHE_DURATION ───────
        if not cache_plates or (now - last_cache_update) >= CACHE_DURATION:
            log.info("[CACHE] Atualizando lista de placas bloqueadas...")
            new_plates = get_blocked_plates()
            if new_plates:
                cache_plates = new_plates
                last_cache_update = now
                log.info("[CACHE] %d placa(s) carregada(s): %s", len(cache_plates), cache_plates)
            else:
                log.warning("[CACHE] Nenhuma placa encontrada ou erro na atualização.")

        # ── 2. Cruzamento de ordens de serviço com placas bloqueadas ──────────
        if cache_plates:
            orders = get_service_orders()
            placas_pagas: dict[str, str] = {}

            for ordem in orders:
                plate    = ordem.get("plate")
                valor    = round(float(ordem.get("totalAmount", 0)), 2)
                situacao = ordem.get("financialSituationName")
                saida    = ordem.get("exitDateTime", "Data desconhecida")

                if plate in cache_plates and valor == TAXA_VALOR and situacao == "Pago":
                    # dict garante que mantemos só a última OS da mesma placa
                    placas_pagas[plate] = saida

            if placas_pagas:
                for plate, data_pagamento in placas_pagas.items():
                    log.info(
                        "[MONITOR] Placa %s pagou R$ %.2f em %s → desbloqueando...",
                        plate, TAXA_VALOR, data_pagamento,
                    )
                    if unlock_vehicle(plate):
                        cache_plates.remove(plate)
            else:
                log.info("[MONITOR] Nenhuma placa bloqueada realizou o pagamento no período.")
        else:
            log.info("[MONITOR] Aguardando cache de placas para iniciar monitoramento...")

        time.sleep(POLLING_INTERVAL)


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

def _validate_env() -> None:
    """Valida que todas as variáveis obrigatórias estão definidas no .env."""
    missing = [name for name, value in _REQUIRED_VARS if not value]
    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente obrigatórias não encontradas: {missing}\n"
            "Defina-as no arquivo .env antes de executar o worker."
        )


def main() -> None:
    _validate_env()
    try:
        run_monitor()
    except KeyboardInterrupt:
        log.info("Monitoramento encerrado pelo usuário.")


if __name__ == "__main__":
    main()
