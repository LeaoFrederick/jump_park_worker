"""
src/database/connection.py
Gerenciamento de conexões e sessões SQLAlchemy com pool e pre-ping.
"""

import logging
import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.database.models import Base

log = logging.getLogger(__name__)

_engine = None
_SessionFactory = None


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
