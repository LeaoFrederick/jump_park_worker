"""
jump_park_worker/database.py
Camada de persistência — SQLAlchemy ORM + MySQL.

Registra eventos de bloqueio/desbloqueio de veículos em banco local,
consumido tanto pelo worker automático quanto pela API HTTP (bloqueios manuais).
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# SQLAlchemy setup
# ──────────────────────────────────────────────────────────────────────────────
Base = declarative_base()

_engine = None
_SessionFactory = None


class Evento(Base):
    """Modelo da tabela 'eventos' — log unificado de bloqueios e desbloqueios."""

    __tablename__ = "eventos"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Quando o evento ocorreu
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    # "BLOQUEIO" ou "DESBLOQUEIO"
    evento = Column(String(20), nullable=False)

    # "AUTOMATICO" (worker) ou "MANUAL" (WebApp via API)
    metodo = Column(String(20), nullable=False)

    # Quem originou a ação — "System Worker" para automáticos, nome do usuário para manuais
    autor = Column(String(100), nullable=False)

    # Justificativa / motivo do bloqueio ou desbloqueio
    motivo = Column(Text, nullable=False, default="")

    # Placa do veículo (ex: "ABC1D23")
    placa = Column(String(20), nullable=False, index=True)

    # ID do cliente no Jump Park (string longa, ex: "3326720251103174738")
    cliente_id = Column(String(50), nullable=False, default="")

    # Estabelecimento onde o pagamento foi detectado / ação foi originada
    estabelecimento_origem = Column(String(50), nullable=False)

    # Lista textual dos estabelecimentos afetados pelo desbloqueio
    # Ex: "COBRANÇA, CANAL, PRINCIPAL"
    estabelecimentos_afetados = Column(Text, nullable=False, default="")

    # ID da ordem de serviço que confirmou o pagamento (nullable para bloqueios manuais)
    os_id = Column(String(100), nullable=True)

    # Valor da taxa cobrada
    valor_taxa = Column(Float, nullable=True)

    # Status financeiro da OS (ex: "Pago")
    status_financeiro = Column(String(50), nullable=True)

    # Data/hora de saída do veículo (da OS)
    exit_datetime = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Evento(id={self.id}, evento={self.evento!r}, placa={self.placa!r}, "
            f"metodo={self.metodo!r}, timestamp={self.timestamp})>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Inicialização
# ──────────────────────────────────────────────────────────────────────────────

def init_db(database_url: str | None = None) -> None:
    """
    Cria a engine, a session factory (thread-safe) e todas as tabelas.
    Deve ser chamada uma única vez no startup do processo.
    """
    global _engine, _SessionFactory

    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "DATABASE_URL não definida. Adicione ao .env, ex:\n"
            "  DATABASE_URL=mysql+pymysql://jump_user:SENHA@localhost:3306/jump_park"
        )

    _engine = create_engine(
        url,
        pool_pre_ping=True,   # reconecta automaticamente se o MySQL reiniciar
        pool_recycle=3600,     # recicla conexões a cada 1h (evita timeout do MySQL)
        echo=False,            # True para debug SQL
    )
    _SessionFactory = scoped_session(sessionmaker(bind=_engine))

    # Cria tabelas que ainda não existem (idempotente)
    Base.metadata.create_all(_engine)
    log.info("[DB] Banco inicializado — tabela 'eventos' pronta.")


@contextmanager
def get_session():
    """Context manager thread-safe para sessões do SQLAlchemy."""
    if _SessionFactory is None:
        raise RuntimeError("Banco não inicializado. Chame init_db() primeiro.")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _SessionFactory.remove()


# ──────────────────────────────────────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────────────────────────────────────

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
) -> Evento | None:
    """
    Insere um evento de bloqueio ou desbloqueio no banco.

    Retorna o objeto Evento criado, ou None se houve erro
    (para não interromper o worker por falha de persistência).
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
            # Flush para obter o ID antes do commit (commit é feito pelo context manager)
            session.flush()
            return registro
    except Exception as exc:
        log.error("[DB] Falha ao registrar evento: %s", exc)
        return None
