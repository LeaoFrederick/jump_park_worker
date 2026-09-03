"""
src/api/routes.py
Aplicação FastAPI e definição dos endpoints HTTP.
"""

import logging
from pathlib import Path
from pydantic import BaseModel
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.api.auth import (
    check_user_auth_status,
    create_impersonation_token,
    create_session_token,
    format_display_name_from_email,
    get_current_user,
    get_google_client_id,
    is_admin,
    is_email_authorized,
    load_auth_config,
    require_admin,
    set_user_pin,
    verify_google_token,
    verify_user_pin,
)
from src.api.schemas import (
    ActionRequest,
    ActionResponse,
    EventoRequest,
    EventoResponse,
    ImpersonateRequest,
)
from src.config import get_ready_establishments
from src.core.jumppark_client import (
    block_vehicle_on_establishment,
    get_blocked_vehicles_details,
    unblock_vehicle_on_establishment,
)
from src.database import registrar_evento
from src.database.connection import get_session
from src.database.models import Evento

log = logging.getLogger(__name__)

# Diretório de arquivos estáticos do frontend standalone
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Jump Park Worker API",
    description="API para registro de eventos de bloqueio/desbloqueio de veículos.",
    version="2.0.0",
)

# Monta arquivos estáticos caso existam
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# CORS restrito — frontend e API compartilham o mesmo domínio via Nginx.
# Apenas origens confiáveis são permitidas (DuckDNS + desenvolvimento local).
_ALLOWED_ORIGINS = [
    "https://painelrestricaocentro.duckdns.org",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve a interface mobile standalone diretamente na raiz com anti-cache."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(
            str(index_file),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse("<h1>Jump Park Worker API está ativa.</h1><p>Frontend não encontrado em src/static/index.html</p>")



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


class GoogleAuthPayload(BaseModel):
    credential: str


class CheckEmailPayload(BaseModel):
    email: str


class SetPinPayload(BaseModel):
    email: str
    pin: str


class LoginPinPayload(BaseModel):
    email: str
    pin: str


# ──────────────────────────────────────────────────────────────────────────────
# Rotas — Autenticação & PIN (Primeiro Acesso)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/auth/config")
def auth_config():
    """Retorna as configurações públicas de autenticação."""
    client_id = get_google_client_id()
    cfg = load_auth_config()
    authorized_list = cfg.get("authorized_users", [])
    return {
        "auth_enabled": bool(client_id or authorized_list),
        "google_client_id": client_id,
    }


@app.post("/api/auth/check-email")
def auth_check_email(payload: CheckEmailPayload):
    """
    Verifica se o e-mail é autorizado e se é o primeiro acesso (sem PIN cadastrado).
    """
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="E-mail inválido.")

    status = check_user_auth_status(email)
    if not status["authorized"]:
        log.warning("[AUTH] E-mail não autorizado tentou checagem: %s", email)
        raise HTTPException(
            status_code=403,
            detail=f"O e-mail '{email}' não está autorizado a acessar este painel. Solicite permissão ao administrador.",
        )

    return {
        "status": "ok",
        "email": email,
        "name": status["name"],
        "first_access": status["first_access"],
    }


@app.post("/api/auth/set-pin")
def auth_set_pin(payload: SetPinPayload):
    """
    Cadastra o PIN do gestor no primeiro acesso e retorna a sessão autenticada.
    """
    email = payload.email.strip().lower()
    pin = payload.pin.strip()

    if len(pin) < 4:
        raise HTTPException(status_code=400, detail="O PIN deve ter no mínimo 4 dígitos.")

    status = check_user_auth_status(email)
    if not status["authorized"]:
        raise HTTPException(status_code=403, detail="E-mail não autorizado.")

    # Se já tiver PIN cadastrado, não permite sobrescrever por esta rota
    if not status["first_access"]:
        raise HTTPException(status_code=400, detail="Este e-mail já possui um PIN cadastrado. Faça login normalmente.")

    user = set_user_pin(email, pin)
    if not user:
        raise HTTPException(status_code=500, detail="Falha ao gravar PIN no arquivo de usuários.")

    user_info = {
        "email": email,
        "name": user.get("name") or format_display_name_from_email(email),
        "picture": "",
    }
    token = create_session_token(user_info)
    log.info("[AUTH] Primeiro acesso concluído com sucesso para %s (%s).", user_info["name"], email)
    return {
        "status": "ok",
        "token": token,
        "user": user_info,
    }


@app.post("/api/auth/login-pin")
def auth_login_pin(payload: LoginPinPayload):
    """
    Valida o PIN informado pelo gestor e gera a sessão de autenticação.
    """
    email = payload.email.strip().lower()
    pin = payload.pin.strip()

    status = check_user_auth_status(email)
    if not status["authorized"]:
        raise HTTPException(status_code=403, detail="E-mail não autorizado.")

    if status["first_access"]:
        raise HTTPException(status_code=400, detail="Primeiro acesso detectado. É necessário cadastrar um PIN primeiro.")

    user = verify_user_pin(email, pin)
    if not user:
        raise HTTPException(status_code=401, detail="PIN incorreto. Tente novamente.")

    user_info = {
        "email": email,
        "name": user.get("name") or format_display_name_from_email(email),
        "picture": "",
    }
    token = create_session_token(user_info)
    log.info("[AUTH] Operador %s (%s) logado com sucesso via PIN.", user_info["name"], email)
    return {
        "status": "ok",
        "token": token,
        "user": user_info,
    }


@app.post("/api/auth/google")
def auth_google(payload: GoogleAuthPayload):
    """
    Valida a credencial Google (ID token), verifica se o e-mail
    consta na whitelist de authorized_users.json e retorna a sessão.
    """
    user_info = verify_google_token(payload.credential)
    if not user_info:
        raise HTTPException(
            status_code=400,
            detail="Credencial do Google inválida ou expirada. Tente novamente.",
        )

    email = user_info["email"]
    if not is_email_authorized(email):
        log.warning("[AUTH] Tentativa de acesso bloqueada para e-mail não autorizado: %s", email)
        raise HTTPException(
            status_code=403,
            detail=f"O e-mail '{email}' não está autorizado a acessar este painel. Solicite permissão ao administrador.",
        )

    token = create_session_token(user_info)
    log.info("[AUTH] Usuário %s (%s) logado com sucesso via Google.", user_info["name"], email)
    return {
        "status": "ok",
        "token": token,
        "user": user_info,
    }


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(get_current_user)):
    """Retorna os dados do operador atualmente autenticado."""
    return user


# ──────────────────────────────────────────────────────────────────────────────
# Rotas — Consulta de Placas Bloqueadas por Estabelecimento
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/bloqueados")
def listar_placas_bloqueadas(user: dict = Depends(get_current_user)):
    """
    Retorna a lista de veículos e placas bloqueadas atualmente em cada estabelecimento Jump Park.
    """
    ready = _get_ready_configs()
    resultado = []
    total_placas_geral = 0
    placas_unicas = set()

    for config in ready:
        veiculos = get_blocked_vehicles_details(config)
        total_unidade = len(veiculos)
        total_placas_geral += total_unidade
        for v in veiculos:
            if v.get("plate"):
                placas_unicas.add(v["plate"])

        resultado.append({
            "estabelecimento": config.label,
            "prefix": config.prefix,
            "establishment_id": config.establishment_id,
            "total": total_unidade,
            "veiculos": veiculos,
        })

    return {
        "status": "ok",
        "total_estabelecimentos": len(resultado),
        "total_bloqueios": total_placas_geral,
        "total_placas_unicas": len(placas_unicas),
        "estabelecimentos": resultado,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Rotas — Bloqueio / Desbloqueio
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/api/bloquear", response_model=ActionResponse)
def bloquear(payload: ActionRequest, user: dict = Depends(get_current_user)):
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
def desbloquear(payload: ActionRequest, user: dict = Depends(require_admin)):
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
def criar_evento(payload: EventoRequest, user: dict = Depends(get_current_user)):
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


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(get_current_user)):
    """
    Retorna o perfil do usuário autenticado com role e status de impersonation.
    Usado pelo frontend para decidir quais controles exibir.
    """
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role", "OPERATOR"),
        "impersonated_by": user.get("impersonated_by"),
    }


@app.post("/api/auth/impersonate")
def impersonate(payload: ImpersonateRequest, admin: dict = Depends(require_admin)):
    """
    Permite que um ADMIN inicie uma sessão em nome de um OPERATOR.
    Gera um token temporário de 2h sem precisar do PIN do operador alvo.
    Impersonation de outro ADMIN é bloqueada.
    """
    result = create_impersonation_token(admin, payload.target_email)
    if result is None:
        # Pode ser: operador não encontrado ou tentativa de impersonar ADMIN
        target = load_auth_config()
        target_user = next(
            (u for u in target.get("authorized_users", []) if u["email"] == payload.target_email.strip().lower()),
            None,
        )
        if target_user and is_admin(target_user):
            raise HTTPException(
                status_code=403,
                detail="Não é permitido impersonar outro administrador.",
            )
        raise HTTPException(
            status_code=404,
            detail=f"Operador '{payload.target_email}' não encontrado na lista de autorizados.",
        )

    return {
        "token": result["token"],
        "user": result["user"],
        "expires_in": 7200,
        "message": f"Sessão iniciada como {result['user']['name']} (expira em 2h).",
    }


@app.get("/api/historico")
def listar_historico(limite: int = 50, user: dict = Depends(get_current_user)):
    """
    Retorna os últimos eventos registrados no banco de dados.
    Suporta o parâmetro opcional `limite` (padrão: 50, máximo: 200).
    """
    limite = min(max(1, limite), 200)  # garante entre 1 e 200
    try:
        with get_session() as session:
            eventos = (
                session.query(Evento)
                .order_by(Evento.timestamp.desc())
                .limit(limite)
                .all()
            )
            return [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.strftime("%d/%m/%Y %H:%M:%S") if e.timestamp else None,
                    "evento": e.evento,
                    "metodo": e.metodo,
                    "autor": e.autor,
                    "motivo": e.motivo,
                    "placa": e.placa,
                    "estabelecimento_origem": e.estabelecimento_origem,
                    "estabelecimentos_afetados": e.estabelecimentos_afetados,
                    "valor_taxa": e.valor_taxa,
                    "status_financeiro": e.status_financeiro,
                }
                for e in eventos
            ]
    except Exception as exc:
        log.error("[HISTORICO] Erro ao consultar eventos: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Erro ao consultar o histórico de eventos.")
