"""
jump_park_worker/main.py
Worker de monitoramento do Jump Park — Multi-Estabelecimento.

Arquitetura:
  - Thread principal: loop de polling (worker de desbloqueio automático)
  - Thread daemon:    servidor FastAPI (API para bloqueios/desbloqueios manuais)
  - Persistência:     MySQL local via SQLAlchemy (módulo database.py)

Credenciais lidas exclusivamente do .env (nunca hardcoded).
Suporta múltiplos estabelecimentos, cada um com suas credenciais próprias.
"""

import logging
import os
import signal
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
import uvicorn
from dotenv import load_dotenv

from database import init_db, registrar_evento

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
# Modelo de configuração por estabelecimento
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class EstablishmentConfig:
    """Agrupa todas as credenciais e metadados de um estabelecimento Jump Park."""
    label: str                    # Nome legível (ex: "COBRANÇA", "CANAL", "PRINCIPAL")
    prefix: str                   # Prefixo no .env (ex: "COBRANCA", "CANAL", "PRINCIPAL")
    integration_id: str           # ID de integração na API
    establishment_id: str         # ID do estabelecimento na API
    token: str                    # Bearer token de acesso
    origin: str = ""              # Domínio cadastrado no Site Admin (opcional)
    blocked_client_id: str = ""   # ID do cliente "CARRO BLOQUEADO" (pode estar vazio)

    @property
    def is_ready(self) -> bool:
        """Retorna True se o estabelecimento tem todas as credenciais necessárias."""
        return bool(
            self.integration_id
            and self.establishment_id
            and self.token
            and self.blocked_client_id
        )


# ──────────────────────────────────────────────────────────────────────────────
# Carregamento de estabelecimentos do .env
# ──────────────────────────────────────────────────────────────────────────────

# Prefixos configurados — adicione novos aqui ao expandir
_ESTABLISHMENT_PREFIXES = [
    ("COBRANCA", "COBRANÇA"),
    ("CANAL",    "CANAL"),
    ("PRINCIPAL","PRINCIPAL"),
]


def load_establishments() -> list[EstablishmentConfig]:
    """Carrega todos os estabelecimentos configurados no .env."""
    establishments = []
    for prefix, label in _ESTABLISHMENT_PREFIXES:
        integration_id    = os.getenv(f"{prefix}_INTEGRATION_ID", "")
        establishment_id  = os.getenv(f"{prefix}_ESTABLISHMENT_ID", "")
        token             = os.getenv(f"{prefix}_ACCESS_TOKEN", "")
        origin            = os.getenv(f"{prefix}_ORIGIN", "").strip()
        blocked_client_id = os.getenv(f"{prefix}_BLOCKED_CLIENT_ID", "")

        # Pula completamente se nem integration_id tem (bloco não configurado)
        if not integration_id:
            log.debug("[CONFIG] Prefixo %s sem INTEGRATION_ID — ignorado.", prefix)
            continue

        config = EstablishmentConfig(
            label=label,
            prefix=prefix,
            integration_id=integration_id,
            establishment_id=establishment_id,
            token=token,
            origin=origin,
            blocked_client_id=blocked_client_id,
        )

        if config.is_ready:
            log.info(
                "[CONFIG] ✔ %s (ID %s) — pronto para monitoramento.",
                label, establishment_id,
            )
        else:
            missing = []
            if not establishment_id: missing.append("ESTABLISHMENT_ID")
            if not token:            missing.append("ACCESS_TOKEN")
            if not blocked_client_id: missing.append("BLOCKED_CLIENT_ID")
            log.warning(
                "[CONFIG] ⚠ %s (ID %s) — pendente, faltam: %s. "
                "Será ignorado no monitoramento até ser configurado.",
                label, establishment_id or "?", ", ".join(missing),
            )

        establishments.append(config)

    return establishments


# ──────────────────────────────────────────────────────────────────────────────
# Configurações globais do worker (carregadas do .env)
# ──────────────────────────────────────────────────────────────────────────────
BASE_URL          = "https://new-web.jumpparkapi.com.br"

# Parâmetros do worker
POLLING_INTERVAL  = int(os.getenv("POLLING_INTERVAL_SECONDS", "10"))   # segundos entre ciclos
CACHE_DURATION    = int(os.getenv("CACHE_DURATION_SECONDS", "1800"))    # 30 min
TAXA_VALOR        = float(os.getenv("TAXA_BLOQUEIO_VALOR", "200.00"))   # valor da taxa de desbloqueio
WINDOW_DAYS       = int(os.getenv("SEARCH_WINDOW_DAYS", "1"))           # janela de busca de OS

# Parâmetros da API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))


# ──────────────────────────────────────────────────────────────────────────────
# Headers HTTP padrão
#
# User-Agent explícito: o padrão da lib requests (python-requests/x.x) é
# bloqueado por WAFs como o Cloudflare que ficam na frente do servidor Jump.
# ──────────────────────────────────────────────────────────────────────────────
def _build_headers(config: EstablishmentConfig) -> dict:
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    # Se um domínio foi cadastrado no Site Admin, o header Origin é obrigatório
    if config.origin:
        headers["Origin"] = config.origin
    return headers


# ──────────────────────────────────────────────────────────────────────────────
# Funções de API (parametrizadas por estabelecimento)
# ──────────────────────────────────────────────────────────────────────────────

def get_blocked_plates(config: EstablishmentConfig) -> list[str]:
    """Busca as placas vinculadas ao cliente 'CARRO BLOQUEADO' de um estabelecimento."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles"
    )
    try:
        response = requests.get(url, headers=_build_headers(config), timeout=10)
        if response.status_code == 200:
            data = response.json().get("data", [])
            plates = [v.get("plate") for v in data if v.get("plate")]
            return plates
        if response.status_code == 401:
            log.error(
                "[CACHE][%s] Erro ao buscar veículos: HTTP 401 (Não autorizado). "
                "Verifique se ACCESS_TOKEN e INTEGRATION_ID estão corretos e se "
                "ORIGIN está definido com o domínio cadastrado no Site Admin.\n"
                "  URL usada: %s", config.label, url,
            )
        else:
            log.error("[CACHE][%s] Erro ao buscar veículos: HTTP %s", config.label, response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[CACHE][%s] Exceção na requisição: %s", config.label, exc)
    return []


def get_service_orders(config: EstablishmentConfig, window_days: int = WINDOW_DAYS) -> list[dict]:
    """Busca ordens de serviço dos últimos `window_days` dias de um estabelecimento."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
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
        response = requests.get(url, headers=_build_headers(config), params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("data", {}).get("content", [])
        log.error("[API][%s] Erro ao buscar ordens: HTTP %s", config.label, response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[API][%s] Exceção na requisição: %s", config.label, exc)
    return []


def unlock_vehicle(config: EstablishmentConfig, plate: str) -> bool:
    """Remove o veículo da conta 'CARRO BLOQUEADO', liberando-o no sistema."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/{plate}"
    )
    try:
        response = requests.delete(url, headers=_build_headers(config), timeout=10)
        if response.status_code == 200:
            log.info("[UNLOCK][%s] Placa %s desbloqueada com sucesso.", config.label, plate)
            return True
        log.error(
            "[UNLOCK][%s] Falha ao desbloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, response.text,
        )
    except requests.exceptions.RequestException as exc:
        log.error("[UNLOCK][%s] Exceção ao desbloquear %s: %s", config.label, plate, exc)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Estado de monitoramento por estabelecimento
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MonitorState:
    """Mantém o estado de cache e timestamps para um estabelecimento."""
    config: EstablishmentConfig
    cache_plates: list[str] = field(default_factory=list)
    last_cache_update: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Loop principal do worker
# ──────────────────────────────────────────────────────────────────────────────

def run_monitor(establishments: list[EstablishmentConfig]) -> None:
    """Executa o monitoramento em loop contínuo para todos os estabelecimentos."""
    # Filtra apenas os estabelecimentos prontos para monitorar
    ready = [e for e in establishments if e.is_ready]
    pending = [e for e in establishments if not e.is_ready]

    if not ready:
        log.error(
            "Nenhum estabelecimento está completamente configurado. "
            "Verifique o .env e preencha os campos obrigatórios."
        )
        return

    log.info("Iniciando worker de monitoramento...")
    log.info(
        "Estabelecimentos ativos: %s | Polling: %ss | Cache: %ss",
        ", ".join(f"{e.label} (ID {e.establishment_id})" for e in ready),
        POLLING_INTERVAL, CACHE_DURATION,
    )
    if pending:
        log.warning(
            "Estabelecimentos pendentes (falta BLOCKED_CLIENT_ID): %s",
            ", ".join(f"{e.label} (ID {e.establishment_id})" for e in pending),
        )

    # Inicializa estado separado para cada estabelecimento
    states = [MonitorState(config=e) for e in ready]

    while True:
        try:
            now = time.time()

            for state in states:
                cfg = state.config
                tag = cfg.label

                # ── 1. Atualiza cache de placas bloqueadas a cada CACHE_DURATION ─
                if not state.cache_plates or (now - state.last_cache_update) >= CACHE_DURATION:
                    log.info("[CACHE][%s] Atualizando lista de placas bloqueadas...", tag)
                    new_plates = get_blocked_plates(cfg)
                    if new_plates:
                        state.cache_plates = new_plates
                        state.last_cache_update = now
                        log.info(
                            "[CACHE][%s] %d placa(s) carregada(s): %s",
                            tag, len(state.cache_plates), state.cache_plates,
                        )
                    else:
                        log.warning("[CACHE][%s] Nenhuma placa encontrada ou erro na atualização.", tag)

                # ── 2. Cruzamento de ordens de serviço com placas bloqueadas ─────
                if state.cache_plates:
                    orders = get_service_orders(cfg)
                    placas_pagas: dict[str, dict] = {}

                    for ordem in orders:
                        plate    = ordem.get("plate")
                        valor    = round(float(ordem.get("totalAmount", 0)), 2)
                        situacao = ordem.get("financialSituationName")
                        saida    = ordem.get("exitDateTime", "Data desconhecida")
                        os_id    = ordem.get("serviceOrderId", "")

                        if plate in state.cache_plates and valor == TAXA_VALOR and situacao == "Pago":
                            # dict garante que mantemos só a última OS da mesma placa
                            placas_pagas[plate] = {
                                "exit_datetime": saida,
                                "os_id": str(os_id),
                                "status_financeiro": situacao,
                            }

                    if placas_pagas:
                        for plate, info in placas_pagas.items():
                            log.info(
                                "[MONITOR][%s] Placa %s pagou R$ %.2f em %s → desbloqueando em TODOS os estabelecimentos...",
                                tag, plate, TAXA_VALOR, info["exit_datetime"],
                            )
                            # ── Desbloqueio cross-estabelecimento ─────────────────────
                            unlocked_from = []
                            for target_state in states:
                                if plate in target_state.cache_plates:
                                    if unlock_vehicle(target_state.config, plate):
                                        target_state.cache_plates.remove(plate)
                                        unlocked_from.append(target_state.config.label)
                                        log.info(
                                            "[UNLOCK][%s] Placa %s removida do cache de %s.",
                                            tag, plate, target_state.config.label,
                                        )

                            # ── Registra evento no banco de dados ─────────────────────
                            if unlocked_from:
                                # Parse exit_datetime se possível
                                exit_dt = None
                                try:
                                    raw = info["exit_datetime"]
                                    if raw and raw != "Data desconhecida" and not raw.startswith("0001"):
                                        exit_dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                                except (ValueError, TypeError):
                                    pass

                                registrar_evento(
                                    evento="DESBLOQUEIO",
                                    metodo="AUTOMATICO",
                                    autor="System Worker",
                                    motivo=f"Taxa R$ {TAXA_VALOR:.2f} paga — OS detectada no estabelecimento {tag}",
                                    placa=plate,
                                    cliente_id=cfg.blocked_client_id,
                                    estabelecimento_origem=tag,
                                    estabelecimentos_afetados=", ".join(unlocked_from),
                                    os_id=info.get("os_id"),
                                    valor_taxa=TAXA_VALOR,
                                    status_financeiro=info.get("status_financeiro"),
                                    exit_datetime=exit_dt,
                                )
                    else:
                        log.info("[MONITOR][%s] Nenhuma placa bloqueada realizou o pagamento no período.", tag)
                else:
                    log.info("[MONITOR][%s] Aguardando cache de placas para iniciar monitoramento...", tag)

        except Exception as e:
            log.error("Erro inesperado no loop principal de monitoramento: %s", e, exc_info=True)
            alertar_discord(f"⚠️ **ALERTA: Erro no Loop Principal**\nErro recuperável capturado:\n```python\n{e}\n```")

        try:
            time.sleep(POLLING_INTERVAL)
        except KeyboardInterrupt:
            raise


# ──────────────────────────────────────────────────────────────────────────────
# Servidor API (thread daemon)
# ──────────────────────────────────────────────────────────────────────────────

def _start_api_server() -> None:
    """Inicia o servidor FastAPI em uma thread daemon."""
    from api import app

    log.info("[API] Iniciando servidor FastAPI em %s:%s...", API_HOST, API_PORT)
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
        # Desabilita reload — estamos rodando via threading, não CLI
        # Desabilita signal handlers — a thread principal cuida do SIGINT
    )


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

def _validate_env(establishments: list[EstablishmentConfig]) -> None:
    """Valida que pelo menos um estabelecimento está completamente configurado."""
    if not establishments:
        raise EnvironmentError(
            "Nenhum estabelecimento configurado no .env.\n"
            "Defina ao menos um bloco com prefixo (ex: COBRANCA_INTEGRATION_ID, "
            "COBRANCA_ESTABLISHMENT_ID, COBRANCA_ACCESS_TOKEN, COBRANCA_BLOCKED_CLIENT_ID)."
        )

    ready = [e for e in establishments if e.is_ready]
    if not ready:
        details = []
        for e in establishments:
            missing = []
            if not e.integration_id:    missing.append(f"{e.prefix}_INTEGRATION_ID")
            if not e.establishment_id:  missing.append(f"{e.prefix}_ESTABLISHMENT_ID")
            if not e.token:             missing.append(f"{e.prefix}_ACCESS_TOKEN")
            if not e.blocked_client_id: missing.append(f"{e.prefix}_BLOCKED_CLIENT_ID")
            details.append(f"  {e.label}: faltam {missing}")
        raise EnvironmentError(
            "Nenhum estabelecimento está completamente configurado:\n"
            + "\n".join(details)
            + "\nPreencha pelo menos um bloco completo no .env."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Alertas de falha (Discord Webhook)
# ──────────────────────────────────────────────────────────────────────────────

def alertar_discord(mensagem: str) -> None:
    """
    Envia uma mensagem de alerta para o canal Discord configurado via webhook.
    Se DISCORD_WEBHOOK_URL não estiver definida no .env, retorna silenciosamente.
    Falhas de rede são engolidas para nunca travar o worker.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return

    try:
        requests.post(
            webhook_url,
            json={"content": mensagem},
            timeout=5,
        )
    except Exception:
        # Falha no Discord não deve impactar o worker
        pass


def _setup_signal_handlers() -> None:
    """Configura captura de sinais do sistema (SIGTERM do systemd, SIGINT de Ctrl+C)."""
    def _shutdown_handler(signum, frame):
        sig_name = "SIGTERM (systemd stop/restart)" if signum == signal.SIGTERM else "SIGINT (Ctrl+C)"
        log.info("Sinal %s recebido. Encerrando worker...", sig_name)
        alertar_discord(
            f"🟡 **JUMP PARK WORKER — PARADO** 🟡\n"
            f"O serviço foi encerrado via `{sig_name}`."
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)


def _start_discord_bot() -> None:
    """Inicia o Bot do Discord interativo em uma thread daemon."""
    try:
        from discord_bot import start_discord_bot
        start_discord_bot()
    except Exception as exc:
        log.error("[DISCORD BOT] Falha ao carregar módulo do Discord Bot: %s", exc)


def main() -> None:
    # Configura handlers para parada limpa com notificação no Discord
    _setup_signal_handlers()

    # 1. Inicializa o banco de dados (cria tabelas se necessário)
    log.info("Inicializando banco de dados...")
    init_db()

    # 2. Carrega e valida estabelecimentos
    establishments = load_establishments()
    _validate_env(establishments)

    # 3. Inicia servidor API em thread daemon
    api_thread = threading.Thread(target=_start_api_server, daemon=True)
    api_thread.start()

    # 4. Inicia Bot do Discord interativo em thread daemon (se token estiver no .env)
    discord_thread = threading.Thread(target=_start_discord_bot, daemon=True)
    discord_thread.start()

    # Notificação de inicialização com sucesso no Discord
    ready_labels = [e.label for e in establishments if e.is_ready]
    msg_startup = (
        "🟢 **JUMP PARK WORKER — INICIADO COM SUCESSO** 🟢\n"
        f"- **Estabelecimentos:** {', '.join(ready_labels)}\n"
        f"- **API FastAPI:** `http://{API_HOST}:{API_PORT}`\n"
        f"- **Discord Bot:** Ativo para comandos remotos (/status, /placas, etc.)\n"
        f"- **Intervalo de Polling:** `{POLLING_INTERVAL}s` | **Janela OS:** `{WINDOW_DAYS}d`\n"
        f"- **Status:** Monitoramento ativo e persistência no MySQL"
    )
    alertar_discord(msg_startup)

    # 5. Roda o worker na thread principal (bloqueia)
    run_monitor(establishments)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception as e:
        erro = traceback.format_exc()
        alertar_discord(
            f"🚨 **ALERTA CRÍTICO — JUMP PARK WORKER DERRUBADO** 🚨\n"
            f"O processo foi finalizado por um erro inesperado:\n"
            f"```python\n{erro}\n```"
        )
        raise e
