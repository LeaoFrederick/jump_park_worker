"""
jump_park_worker/api.py
API HTTP (FastAPI) para receber bloqueios/desbloqueios manuais
enviados pelo WebApp (Google Apps Script).

Rotas principais:
  POST /api/bloquear    — vincula placa ao "CARRO BLOQUEADO" em todos os estabelecimentos
  POST /api/desbloquear — remove placa do "CARRO BLOQUEADO" em todos os estabelecimentos
  POST /api/eventos     — registro genérico de evento (mantido por retrocompatibilidade)
  GET  /api/health      — healthcheck

Roda em thread separada no mesmo processo do worker.
"""

import logging
from datetime import datetime

import requests
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
    version="2.0.0",
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


# ──────────────────────────────────────────────────────────────────────────────
# Funções de integração com a API Jump Park
# ──────────────────────────────────────────────────────────────────────────────

def _block_vehicle_on_establishment(config, plate: str) -> dict:
    """
    Adiciona veículo (placa) ao cliente "CARRO BLOQUEADO" de um estabelecimento.
    POST /api/{integrationId}/public/establishment/{establishmentId}/clients/{clientId}/vehicles/new
    Documentação: https://docs.jumpparkapi.com.br/public/docs/api-reference/Clients/client-vehicle-add
    """
    from main import BASE_URL, _build_headers

    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/new"
    )
    headers = _build_headers(config)
    headers["Content-Type"] = "application/json"

    body = {
        "establishmentId": int(config.establishment_id),
        "plate": plate.upper().strip(),
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("response") == "success":
                log.info(
                    "[BLOCK][%s] Placa %s vinculada ao CARRO BLOQUEADO com sucesso.",
                    config.label, plate,
                )
                return {"estabelecimento": config.label, "status": "ok", "detail": "Placa bloqueada"}
            else:
                detail = data.get("data", {})
                msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else str(detail)
                log.warning("[BLOCK][%s] Resposta inesperada para %s: %s", config.label, plate, msg)
                return {"estabelecimento": config.label, "status": "warning", "detail": msg}

        log.error(
            "[BLOCK][%s] Falha ao bloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, response.text[:300],
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"HTTP {response.status_code}: {response.text[:200]}",
        }
    except requests.exceptions.RequestException as exc:
        log.error("[BLOCK][%s] Exceção ao bloquear %s: %s", config.label, plate, exc)
        return {"estabelecimento": config.label, "status": "error", "detail": str(exc)}


def _unblock_vehicle_on_establishment(config, plate: str) -> dict:
    """
    Remove veículo (placa) do cliente "CARRO BLOQUEADO" de um estabelecimento.
    DELETE /api/{integrationId}/public/establishment/{establishmentId}/clients/{clientId}/vehicles/{plate}
    Espelha unlock_vehicle() do main.py.
    """
    from main import BASE_URL, _build_headers

    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/{plate.upper().strip()}"
    )
    try:
        response = requests.delete(url, headers=_build_headers(config), timeout=10)
        if response.status_code == 200:
            log.info(
                "[UNBLOCK][%s] Placa %s removida do CARRO BLOQUEADO com sucesso.",
                config.label, plate,
            )
            return {"estabelecimento": config.label, "status": "ok", "detail": "Placa desbloqueada"}

        log.error(
            "[UNBLOCK][%s] Falha ao desbloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, response.text[:300],
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"HTTP {response.status_code}: {response.text[:200]}",
        }
    except requests.exceptions.RequestException as exc:
        log.error("[UNBLOCK][%s] Exceção ao desbloquear %s: %s", config.label, plate, exc)
        return {"estabelecimento": config.label, "status": "error", "detail": str(exc)}


def _get_ready_establishments() -> list:
    """Retorna os estabelecimentos prontos (is_ready == True)."""
    from main import load_establishments

    establishments = load_establishments()
    ready = [e for e in establishments if e.is_ready]
    if not ready:
        raise HTTPException(
            status_code=500,
            detail="Nenhum estabelecimento está completamente configurado no .env.",
        )
    return ready


# ──────────────────────────────────────────────────────────────────────────────
# Rotas — Bloqueio / Desbloqueio
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/bloquear", response_model=ActionResponse)
def bloquear(payload: ActionRequest):
    """
    Bloqueia uma placa em TODOS os estabelecimentos ativos.
    Vincula a placa ao cliente "CARRO BLOQUEADO" via API Jump Park
    e registra o evento no banco de dados local.
    """
    plate = payload.plate.upper().strip()
    if not plate or len(plate) < 7:
        raise HTTPException(status_code=400, detail="Placa inválida. Deve ter 7 caracteres.")

    ready = _get_ready_establishments()
    resultados = []
    sucesso_em = []

    for config in ready:
        resultado = _block_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)

    # Registra no banco se ao menos um estabelecimento teve sucesso
    evento_id = None
    if sucesso_em:
        registro = registrar_evento(
            evento="BLOQUEIO",
            metodo="MANUAL",
            autor=payload.autor,
            motivo=payload.reason,
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="WEBAPP",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )
        if registro:
            evento_id = registro.id

    # Determina status geral da resposta
    total_ok = len(sucesso_em)
    total = len(ready)

    if total_ok == total:
        status = "ok"
        message = f"Placa {plate} bloqueada em {total_ok}/{total} estabelecimento(s)."
    elif total_ok > 0:
        status = "partial"
        message = f"Placa {plate} bloqueada em {total_ok}/{total} estabelecimento(s). Verifique detalhes."
    else:
        status = "error"
        message = f"Falha ao bloquear placa {plate} em todos os estabelecimentos."

    return ActionResponse(
        status=status,
        message=message,
        resultados=resultados,
        evento_id=evento_id,
    )


@app.post("/api/desbloquear", response_model=ActionResponse)
def desbloquear(payload: ActionRequest):
    """
    Desbloqueia uma placa em TODOS os estabelecimentos ativos.
    Remove a placa do cliente "CARRO BLOQUEADO" via API Jump Park
    e registra o evento no banco de dados local.
    """
    plate = payload.plate.upper().strip()
    if not plate or len(plate) < 7:
        raise HTTPException(status_code=400, detail="Placa inválida. Deve ter 7 caracteres.")

    ready = _get_ready_establishments()
    resultados = []
    sucesso_em = []

    for config in ready:
        resultado = _unblock_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)

    # Registra no banco se ao menos um estabelecimento teve sucesso
    evento_id = None
    if sucesso_em:
        registro = registrar_evento(
            evento="DESBLOQUEIO",
            metodo="MANUAL",
            autor=payload.autor,
            motivo=payload.reason,
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="WEBAPP",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )
        if registro:
            evento_id = registro.id

    # Determina status geral
    total_ok = len(sucesso_em)
    total = len(ready)

    if total_ok == total:
        status = "ok"
        message = f"Placa {plate} desbloqueada em {total_ok}/{total} estabelecimento(s)."
    elif total_ok > 0:
        status = "partial"
        message = f"Placa {plate} desbloqueada em {total_ok}/{total} estabelecimento(s). Verifique detalhes."
    else:
        status = "error"
        message = f"Falha ao desbloquear placa {plate} em todos os estabelecimentos."

    return ActionResponse(
        status=status,
        message=message,
        resultados=resultados,
        evento_id=evento_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Rota — Registro genérico de evento (retrocompatibilidade)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/eventos", response_model=EventoResponse, status_code=201)
def criar_evento(payload: EventoRequest):
    """Registra um evento de bloqueio ou desbloqueio no banco de dados."""

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


# ──────────────────────────────────────────────────────────────────────────────
# Healthcheck
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Healthcheck simples — confirma que a API está respondendo."""
    return {"status": "ok", "service": "jump_park_worker_api"}
