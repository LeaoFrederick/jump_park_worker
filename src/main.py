"""
src/main.py
Módulo de orquestração do Jump Park Worker.

Inicializa:
  1. Banco de Dados MySQL (SQLAlchemy)
  2. Thread daemon: Servidor HTTP FastAPI
  3. Thread daemon: Bot Interativo do Discord
  4. Thread principal: Loop de polling do worker
"""

import logging
import signal
import sys
import threading
import traceback
import uvicorn

from src.api import app
from src.bot import start_discord_bot
from src.config import (
    API_HOST,
    API_PORT,
    POLLING_INTERVAL,
    WINDOW_DAYS,
    alertar_discord,
    load_establishments,
    validate_env,
)
from src.core import run_monitor
from src.database import init_db

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jump_park_worker")


def _start_api_server() -> None:
    """Inicia o servidor FastAPI em uma thread daemon."""
    log.info("[API] Iniciando servidor FastAPI em %s:%s...", API_HOST, API_PORT)
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
    )


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


def main() -> None:
    """Função principal de inicialização do Jump Park Worker."""
    _setup_signal_handlers()

    # 1. Inicializa o banco de dados
    log.info("Inicializando banco de dados...")
    init_db()

    # 2. Carrega e valida os estabelecimentos configurados
    establishments = load_establishments()
    validate_env(establishments)

    # 3. Inicia servidor API em thread daemon
    api_thread = threading.Thread(target=_start_api_server, daemon=True)
    api_thread.start()

    # 4. Inicia Bot do Discord em thread daemon
    discord_thread = threading.Thread(target=start_discord_bot, daemon=True)
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
