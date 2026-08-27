"""
src/core/worker.py
Loop contínuo de polling e monitoramento para desbloqueio automático de veículos.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from src.bot.notifications import notificar_desbloqueio
from src.config import (
    CACHE_DURATION,
    POLLING_INTERVAL,
    TAXA_VALOR,
    EstablishmentConfig,
    alertar_discord,
)
from src.core.jumppark_client import (
    get_blocked_plates,
    get_service_orders,
    unlock_vehicle,
)
from src.database import registrar_evento

log = logging.getLogger(__name__)


@dataclass
class MonitorState:
    """Mantém o estado de cache e timestamps para um estabelecimento."""
    config: EstablishmentConfig
    cache_plates: list[str] = field(default_factory=list)
    last_cache_update: float = 0.0


def run_monitor(establishments: list[EstablishmentConfig]) -> None:
    """Executa o monitoramento em loop contínuo para todos os estabelecimentos."""
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
                                exit_dt = None
                                try:
                                    raw = info["exit_datetime"]
                                    if raw and raw != "Data desconhecida" and not raw.startswith("0001"):
                                        exit_dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                                except (ValueError, TypeError):
                                    pass

                                evento_id = registrar_evento(
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

                                # ── Notificação no Discord ─────────────────────────────
                                notificar_desbloqueio(
                                    placa=plate,
                                    valor_taxa=TAXA_VALOR,
                                    estabelecimento_origem=tag,
                                    estabelecimentos_afetados=unlocked_from,
                                    os_id=info.get("os_id"),
                                    exit_datetime=info.get("exit_datetime"),
                                    evento_id=evento_id,
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
