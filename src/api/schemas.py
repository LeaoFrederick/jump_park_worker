"""
src/api/schemas.py
Modelos Pydantic para validação de dados da API FastAPI.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class ActionRequest(BaseModel):
    """Payload recebido do WebApp para bloqueio ou desbloqueio manual."""
    plate: str = Field(
        ...,
        description="Placa do veículo (7 caracteres)",
        examples=["ABC1234"],
    )
    reason: str = Field(
        default="",
        description="Motivo do bloqueio/desbloqueio",
        examples=["Inadimplência"],
    )
    autor: str = Field(
        ...,
        description="Nome e email do operador",
        examples=["João Silva (joao@dominio.com)"],
    )


class ActionResponse(BaseModel):
    """Resposta padronizada para o frontend."""
    status: str
    message: str
    resultados: list[dict] = Field(default_factory=list)
    evento_id: int | None = None


class EventoRequest(BaseModel):
    """Payload para registro genérico de evento (retrocompatibilidade)."""
    evento: str = Field(..., examples=["BLOQUEIO"])
    metodo: str = Field(default="MANUAL", examples=["MANUAL"])
    autor: str = Field(..., examples=["João Silva"])
    motivo: str = Field(default="", examples=["Inadimplência"])
    placa: str = Field(..., examples=["ABC1D23"])
    cliente_id: str = Field(default="", examples=["3326720251103174738"])
    estabelecimento_origem: str = Field(..., examples=["COBRANÇA"])
    estabelecimentos_afetados: str = Field(default="", examples=["COBRANÇA, CANAL, PRINCIPAL"])
    os_id: str | None = Field(default=None)
    valor_taxa: float | None = Field(default=None, examples=[200.00])
    status_financeiro: str | None = Field(default=None, examples=["Pago"])
    exit_datetime: datetime | None = Field(default=None)
    timestamp: datetime | None = Field(default=None)


class EventoResponse(BaseModel):
    """Resposta do endpoint genérico de eventos."""
    status: str = "ok"
    message: str
    evento_id: int | None = None
