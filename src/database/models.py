"""
src/database/models.py
Modelos ORM SQLAlchemy para o Jump Park Worker.
"""

from datetime import datetime
from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


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
