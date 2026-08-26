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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Captura qualquer exceção não tratada na API para evitar travamentos silenciosos da thread."""
    log.error(f"Erro interno não tratado na rota {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Erro interno inesperado no servidor."}
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
                return {"estabelecimento": config.label, "status": "ok", "detail": "Placa bloqueada com sucesso"}
            else:
                detail = data.get("data", {})
                msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else str(detail)
                is_already = any(k in msg.lower() for k in ["já cadastrado", "ja cadastrado", "já existe", "ja existe", "duplicad", "already", "vinculad"])
                if is_already:
                    log.warning("[BLOCK][%s] Placa %s já está vinculada ao CARRO BLOQUEADO: %s", config.label, plate, msg)
                    return {"estabelecimento": config.label, "status": "already_blocked", "detail": f"Placa já cadastrada ({msg})"}
                log.warning("[BLOCK][%s] Resposta inesperada para %s: %s", config.label, plate, msg)
                return {"estabelecimento": config.label, "status": "warning", "detail": msg}

        # Tratamento de status codes diferentes de 200 (ex: 400, 409, 422)
        try:
            data = response.json()
            detail = data.get("data", {})
            msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else (data.get("message") or response.text[:200])
        except Exception:
            msg = response.text[:200]

        is_already = response.status_code in (400, 409, 422) and any(
            k in msg.lower() for k in ["já cadastrado", "ja cadastrado", "já existe", "ja existe", "duplicad", "already", "vinculad"]
        )
        if is_already:
            log.warning("[BLOCK][%s] Placa %s já está vinculada ao CARRO BLOQUEADO: %s", config.label, plate, msg)
            return {"estabelecimento": config.label, "status": "already_blocked", "detail": f"Placa já cadastrada ({msg})"}

        log.error(
            "[BLOCK][%s] Falha ao bloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, msg,
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"HTTP {response.status_code}: {msg}",
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
            try:
                data = response.json()
                if data.get("response") == "error":
                    detail = data.get("data", {})
                    msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else str(detail)
                    is_not_found = any(k in msg.lower() for k in ["não encontrad", "nao encontrad", "inexistente", "not found", "não existe", "nao existe"])
                    if is_not_found:
                        log.warning("[UNBLOCK][%s] Placa %s não encontrada para desbloqueio: %s", config.label, plate, msg)
                        return {"estabelecimento": config.label, "status": "not_found", "detail": "Placa não estava bloqueada neste estabelecimento"}
                    log.warning("[UNBLOCK][%s] Resposta inesperada ao desbloquear %s: %s", config.label, plate, msg)
                    return {"estabelecimento": config.label, "status": "warning", "detail": msg}
            except Exception:
                pass

            log.info(
                "[UNBLOCK][%s] Placa %s removida do CARRO BLOQUEADO com sucesso.",
                config.label, plate,
            )
            return {"estabelecimento": config.label, "status": "ok", "detail": "Placa desbloqueada com sucesso"}

        try:
            data = response.json()
            detail = data.get("data", {})
            msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else (data.get("message") or response.text[:200])
        except Exception:
            msg = response.text[:200]

        is_not_found = response.status_code == 404 or any(
            k in msg.lower() for k in ["não encontrad", "nao encontrad", "inexistente", "not found", "não existe", "nao existe"]
        )
        if is_not_found:
            log.warning("[UNBLOCK][%s] Placa %s não encontrada na lista de bloqueados: %s", config.label, plate, msg)
            return {"estabelecimento": config.label, "status": "not_found", "detail": "Placa não estava bloqueada neste estabelecimento"}

        log.error(
            "[UNBLOCK][%s] Falha ao desbloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, msg,
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"HTTP {response.status_code}: {msg}",
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
    ja_bloqueada_em = []
    falha_em = []

    for config in ready:
        resultado = _block_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)
        elif resultado["status"] == "already_blocked":
            ja_bloqueada_em.append(config.label)
        else:
            falha_em.append(f"{config.label} ({resultado.get('detail', 'Erro')})")

    # Registra no banco se ao menos um estabelecimento teve sucesso
    evento_id = None
    if sucesso_em:
        evento_id = registrar_evento(
            evento="BLOQUEIO",
            metodo="MANUAL",
            autor=payload.autor,
            motivo=payload.reason,
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="WEBAPP",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )

    total = len(ready)
    if len(sucesso_em) == total:
        status = "ok"
        message = f"Placa {plate} bloqueada com sucesso em todos os {total} estabelecimento(s)."
        log.info("[BLOCK] Placa %s bloqueada com sucesso em todos os estabelecimentos por %s.", plate, payload.autor)
    elif len(ja_bloqueada_em) == total:
        status = "already_blocked"
        message = f"A placa {plate} já está cadastrada como bloqueada em todos os estabelecimentos."
        log.warning("[BLOCK] Tentativa de bloqueio para placa %s que já estava bloqueada em todos os estabelecimentos (%s). Operador: %s", plate, ", ".join(ja_bloqueada_em), payload.autor)
    elif len(sucesso_em) > 0:
        status = "partial"
        partes = []
        if sucesso_em:
            partes.append(f"Bloqueada em: {', '.join(sucesso_em)}")
        if ja_bloqueada_em:
            partes.append(f"Já constava bloqueada em: {', '.join(ja_bloqueada_em)}")
        if falha_em:
            partes.append(f"Falhas: {', '.join(falha_em)}")
        message = f"Placa {plate} processada: " + " | ".join(partes)
        log.info("[BLOCK] Placa %s processada parcialmente por %s: %s", plate, payload.autor, message)
    elif len(ja_bloqueada_em) > 0:
        status = "already_blocked"
        message = f"A placa {plate} já constava como bloqueada em: {', '.join(ja_bloqueada_em)}."
        if falha_em:
            message += f" (Falhas nos demais: {', '.join(falha_em)})"
        log.warning("[BLOCK] Placa %s já constava bloqueada por %s: %s", plate, payload.autor, message)
    else:
        status = "error"
        message = f"Falha ao bloquear placa {plate} nos estabelecimentos: {', '.join(falha_em)}"
        log.error("[BLOCK] Falha total ao bloquear placa %s por %s: %s", plate, payload.autor, message)

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
    nao_encontrada_em = []
    falha_em = []

    for config in ready:
        resultado = _unblock_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)
        elif resultado["status"] == "not_found":
            nao_encontrada_em.append(config.label)
        else:
            falha_em.append(f"{config.label} ({resultado.get('detail', 'Erro')})")

    # Registra no banco se ao menos um estabelecimento teve sucesso
    evento_id = None
    if sucesso_em:
        evento_id = registrar_evento(
            evento="DESBLOQUEIO",
            metodo="MANUAL",
            autor=payload.autor,
            motivo=payload.reason,
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="WEBAPP",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )

    total = len(ready)
    if len(sucesso_em) == total:
        status = "ok"
        message = f"Placa {plate} desbloqueada com sucesso em todos os {total} estabelecimento(s)."
        log.info("[UNBLOCK] Placa %s desbloqueada com sucesso em todos os estabelecimentos por %s.", plate, payload.autor)
    elif len(nao_encontrada_em) == total:
        status = "not_found"
        message = f"A placa {plate} não foi encontrada na lista de bloqueados de nenhum estabelecimento."
        log.warning("[UNBLOCK] Tentativa de desbloqueio para placa %s que não constava bloqueada em nenhum estabelecimento. Operador: %s", plate, payload.autor)
    elif len(sucesso_em) > 0:
        status = "partial"
        partes = []
        if sucesso_em:
            partes.append(f"Desbloqueada em: {', '.join(sucesso_em)}")
        if nao_encontrada_em:
            partes.append(f"Não constava bloqueada em: {', '.join(nao_encontrada_em)}")
        if falha_em:
            partes.append(f"Falhas: {', '.join(falha_em)}")
        message = f"Placa {plate} processada: " + " | ".join(partes)
        log.info("[UNBLOCK] Placa %s processada parcialmente por %s: %s", plate, payload.autor, message)
    elif len(nao_encontrada_em) > 0:
        status = "not_found"
        message = f"A placa {plate} não constava como bloqueada em: {', '.join(nao_encontrada_em)}."
        if falha_em:
            message += f" (Falhas nos demais: {', '.join(falha_em)})"
        log.warning("[UNBLOCK] Placa %s não constava bloqueada por %s: %s", plate, payload.autor, message)
    else:
        status = "error"
        message = f"Falha ao desbloquear placa {plate} nos estabelecimentos: {', '.join(falha_em)}"
        log.error("[UNBLOCK] Falha total ao desbloquear placa %s por %s: %s", plate, payload.autor, message)

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

    evento_id = registrar_evento(
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

    if evento_id is None:
        raise HTTPException(
            status_code=500,
            detail="Falha ao gravar evento no banco de dados. Verifique os logs.",
        )

    return EventoResponse(
        message=f"Evento {payload.evento} registrado para placa {payload.placa}.",
        evento_id=evento_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Healthcheck
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Healthcheck simples — confirma que a API está respondendo."""
    return {"status": "ok", "service": "jump_park_worker_api"}
