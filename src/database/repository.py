"""
src/database/repository.py
Operações de persistência (CRUD) para eventos de bloqueio e desbloqueio.
"""

import logging
from datetime import datetime

from src.database.connection import get_session
from src.database.models import Evento

log = logging.getLogger(__name__)


def registrar_evento(
    *,
    evento: str,
    metodo: str,
    autor: str,
    placa: str,
    estabelecimento_origem: str,
    motivo: str = "",
    cliente_id: str = "",
    estabelecimentos_afetados: str = "",
    os_id: str | None = None,
    valor_taxa: float | None = None,
    status_financeiro: str | None = None,
    exit_datetime: datetime | None = None,
    timestamp: datetime | None = None,
) -> int | None:
    """
    Insere um evento de bloqueio ou desbloqueio no banco.

    Retorna o ID (int) do Evento criado, ou None se houve erro
    (para não interromper o worker por falha de persistência e evitar DetachedInstanceError).
    """
    try:
        registro = Evento(
            timestamp=timestamp or datetime.now(),
            evento=evento,
            metodo=metodo,
            autor=autor,
            motivo=motivo,
            placa=placa,
            cliente_id=cliente_id,
            estabelecimento_origem=estabelecimento_origem,
            estabelecimentos_afetados=estabelecimentos_afetados,
            os_id=os_id,
            valor_taxa=valor_taxa,
            status_financeiro=status_financeiro,
            exit_datetime=exit_datetime,
        )
        with get_session() as session:
            session.add(registro)
            log.info(
                "[DB] Evento registrado: %s | %s | placa=%s | metodo=%s | origem=%s",
                evento, placa, placa, metodo, estabelecimento_origem,
            )
            # Flush para gerar e obter o ID antes do commit
            session.flush()
            evento_id = registro.id
            return evento_id
    except Exception as exc:
        log.error("[DB] Falha ao registrar evento: %s", exc)
        return None
