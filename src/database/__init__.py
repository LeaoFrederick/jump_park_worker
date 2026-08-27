"""
src/database package.
Exporta conexão, modelos e funções de persistência.
"""

from src.database.connection import get_session, init_db
from src.database.models import Base, Evento
from src.database.repository import registrar_evento

__all__ = [
    "Base",
    "Evento",
    "get_session",
    "init_db",
    "registrar_evento",
]
