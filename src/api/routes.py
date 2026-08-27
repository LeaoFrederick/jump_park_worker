"""
src/api/routes.py
Aplicação FastAPI e definição dos endpoints HTTP.
"""

import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.schemas import (
    ActionRequest,
    ActionResponse,
    EventoRequest,
    EventoResponse,
)
from src.config import get_ready_establishments
from src.core.jumppark_client import (
    block_vehicle_on_establishment,
    unblock_vehicle_on_establishment,
)
from src.database import registrar_evento

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


def _get_ready_configs():
    ready = get_ready_establishments()
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
    Vincula a placa ao cliente 'CARRO BLOQUEADO' via API Jump Park
    e registra o evento no banco de dados local.
    """
    plate = payload.plate.upper().strip()
    if not plate or len(plate) < 7:
        raise HTTPException(status_code=400, detail="Placa inválida. Deve ter 7 caracteres.")

    ready = _get_ready_configs()
    resultados = []
    sucesso_em = []
    ja_bloqueada_em = []
    falha_em = []

    for config in ready:
        resultado = block_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)
        elif resultado["status"] == "already_blocked":
            ja_bloqueada_em.append(config.label)
        else:
            falha_em.append(resultado.get("detail", config.label))

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
        message = f"Placa {plate} bloqueada com sucesso em todos os estabelecimentos."
        log.info("[BLOCK] Placa %s bloqueada com sucesso em todos os estabelecimentos por %s.", plate, payload.autor)
    elif len(ja_bloqueada_em) == total:
        status = "already_blocked"
        message = f"A placa {plate} já está cadastrada como bloqueada em todos os estabelecimentos."
        log.info("[BLOCK] Placa %s já estava cadastrada em todos os estabelecimentos. Operador: %s", plate, payload.autor)
    elif len(sucesso_em) > 0:
        status = "partial"
        partes = []
        if sucesso_em:
            partes.append(f"bloqueada em {', '.join(sucesso_em)}")
        if ja_bloqueada_em:
            partes.append(f"já constava em {', '.join(ja_bloqueada_em)}")
        if falha_em:
            partes.append(f"falhas em {', '.join(falha_em)}")
        message = f"Placa {plate}: " + "; ".join(partes) + "."
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
    Remove a placa do cliente 'CARRO BLOQUEADO' via API Jump Park
    e registra o evento no banco de dados local.
    """
    plate = payload.plate.upper().strip()
    if not plate or len(plate) < 7:
        raise HTTPException(status_code=400, detail="Placa inválida. Deve ter 7 caracteres.")

    ready = _get_ready_configs()
    resultados = []
    sucesso_em = []
    nao_encontrada_em = []
    falha_em = []

    for config in ready:
        resultado = unblock_vehicle_on_establishment(config, plate)
        resultados.append(resultado)
        if resultado["status"] == "ok":
            sucesso_em.append(config.label)
        elif resultado["status"] == "not_found":
            nao_encontrada_em.append(config.label)
        else:
            falha_em.append(resultado.get("detail", config.label))

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
        message = f"Placa {plate} desbloqueada com sucesso em todos os estabelecimentos."
        log.info("[UNBLOCK] Placa %s desbloqueada com sucesso em todos os estabelecimentos por %s.", plate, payload.autor)
    elif len(nao_encontrada_em) == total:
        status = "not_found"
        message = f"A placa {plate} não foi encontrada na lista de bloqueados de nenhum estabelecimento."
        log.info("[UNBLOCK] Tentativa de desbloqueio para placa %s que não constava na lista de bloqueio. Operador: %s", plate, payload.autor)
    elif len(sucesso_em) > 0:
        status = "partial"
        partes = []
        if sucesso_em:
            partes.append(f"desbloqueada em {', '.join(sucesso_em)}")
        if nao_encontrada_em:
            partes.append(f"não constava em {', '.join(nao_encontrada_em)}")
        if falha_em:
            partes.append(f"falhas em {', '.join(falha_em)}")
        message = f"Placa {plate}: " + "; ".join(partes) + "."
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


@app.post("/api/eventos", response_model=EventoResponse, status_code=201)
def criar_evento(payload: EventoRequest):
    """Registra um evento de bloqueio ou desbloqueio no banco de dados."""
    if payload.evento not in ("BLOQUEIO", "DESBLOQUEIO"):
        raise HTTPException(
            status_code=422,
            detail=f'Campo "evento" deve ser "BLOQUEIO" ou "DESBLOQUEIO", recebido: "{payload.evento}"',
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


@app.get("/api/health")
def health():
    """Healthcheck simples — confirma que a API está respondendo."""
    return {"status": "ok", "service": "jump_park_worker_api"}
