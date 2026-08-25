"""
jump_park_worker/api.py
API HTTP leve (FastAPI) para receber bloqueios/desbloqueios manuais
enviados pelo WebApp (Google Apps Script).

Roda em thread separada no mesmo processo do worker.
"""

import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import registrar_evento

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Jump Park Worker API",
    description="API para registro de eventos de bloqueio/desbloqueio de veículos.",
    version="1.0.0",
)

# CORS aberto — o Google Apps Script faz requisições de domínio arbitrário
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────────────
# Schemas (Pydantic)
# ──────────────────────────────────────────────────────────────────────────────

class EventoRequest(BaseModel):
    """Payload esperado pelo WebApp ao registrar um evento."""

    evento: str = Field(
        ...,
        description='Tipo do evento: "BLOQUEIO" ou "DESBLOQUEIO"',
        examples=["BLOQUEIO"],
    )
    metodo: str = Field(
        default="MANUAL",
        description='Método de ação: "MANUAL" ou "AUTOMATICO"',
        examples=["MANUAL"],
    )
    autor: str = Field(
        ...,
        description="Nome do operador que executou a ação",
        examples=["João Silva"],
    )
    motivo: str = Field(
        default="",
        description="Justificativa para o bloqueio/desbloqueio",
        examples=["Inadimplência 3 meses"],
    )
    placa: str = Field(
        ...,
        description="Placa do veículo",
        examples=["ABC1D23"],
    )
    cliente_id: str = Field(
        default="",
        description="ID do cliente no Jump Park",
        examples=["3326720251103174738"],
    )
    estabelecimento_origem: str = Field(
        ...,
        description="Estabelecimento onde a ação foi originada",
        examples=["COBRANÇA"],
    )
    estabelecimentos_afetados: str = Field(
        default="",
        description="Estabelecimentos afetados (separados por vírgula)",
        examples=["COBRANÇA, CANAL, PRINCIPAL"],
    )
    os_id: str | None = Field(
        default=None,
        description="ID da ordem de serviço (se aplicável)",
    )
    valor_taxa: float | None = Field(
        default=None,
        description="Valor da taxa de desbloqueio",
        examples=[200.00],
    )
    status_financeiro: str | None = Field(
        default=None,
        description='Status financeiro da OS (ex: "Pago")',
        examples=["Pago"],
    )
    exit_datetime: datetime | None = Field(
        default=None,
        description="Data/hora de saída do veículo",
    )
    timestamp: datetime | None = Field(
        default=None,
        description="Timestamp do evento (usa datetime atual se omitido)",
    )


class EventoResponse(BaseModel):
    """Resposta após registro bem-sucedido."""
    status: str = "ok"
    message: str
    evento_id: int | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Rotas
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/eventos", response_model=EventoResponse, status_code=201)
def criar_evento(payload: EventoRequest):
    """Registra um evento de bloqueio ou desbloqueio no banco de dados."""

    # Validação do tipo de evento
    if payload.evento not in ("BLOQUEIO", "DESBLOQUEIO"):
        raise HTTPException(
            status_code=422,
            detail=f'Campo "evento" deve ser "BLOQUEIO" ou "DESBLOQUEIO", '
                   f'recebido: "{payload.evento}"',
        )

    registro = registrar_evento(
        evento=payload.evento,
        metodo=payload.metodo,
        autor=payload.autor,
        motivo=payload.motivo,
        placa=payload.placa.upper().strip(),
        cliente_id=payload.cliente_id,
        estabelecimento_origem=payload.estabelecimento_origem,
        estabelecimentos_afetados=payload.estabelecimentos_afetados,
        os_id=payload.os_id,
        valor_taxa=payload.valor_taxa,
        status_financeiro=payload.status_financeiro,
        exit_datetime=payload.exit_datetime,
        timestamp=payload.timestamp,
    )

    if registro is None:
        raise HTTPException(
            status_code=500,
            detail="Falha ao gravar evento no banco de dados. Verifique os logs.",
        )

    return EventoResponse(
        message=f"Evento {payload.evento} registrado para placa {payload.placa}.",
        evento_id=registro.id,
    )


@app.get("/api/health")
def health():
    """Healthcheck simples — confirma que a API está respondendo."""
    return {"status": "ok", "service": "jump_park_worker_api"}
