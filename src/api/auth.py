"""
src/api/auth.py
Módulo de autenticação por e-mail, PIN no primeiro acesso e controle de permissões em JSON.
Separa a lista de usuários autorizados (no Git) das credenciais de PIN (armazenadas localmente).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import HTTPException, Header

log = logging.getLogger(__name__)

# Diretório raiz e caminhos dos arquivos
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "config" / "authorized_users.json"
FALLBACK_CONFIG_FILE = BASE_DIR / "authorized_users.json"

# Arquivo de credenciais locais (PINs criptografados) — NUNCA versionado no Git
CREDENTIALS_FILE = BASE_DIR / "config" / "user_credentials.json"

# Chave secreta para assinatura dos tokens de sessão (HMAC-SHA256)
AUTH_SECRET = os.getenv("AUTH_SECRET") or os.getenv("SECRET_KEY") or "jump_park_secret_key_prod_2026"
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60  # 7 dias de sessão válida


def _get_config_path() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    if FALLBACK_CONFIG_FILE.exists():
        return FALLBACK_CONFIG_FILE
    return CONFIG_FILE


def format_display_name_from_email(email: str) -> str:
    """Extrai e formata um nome amigável a partir do e-mail."""
    if not email or "@" not in email:
        return "Operador"
    name_part = email.split("@")[0].strip()
    clean = re.sub(r"\d+$", "", name_part).replace(".", " ").replace("_", " ").replace("-", " ").strip()
    parts = clean.split()
    if parts:
        formatted = " ".join(p.capitalize() for p in parts)
        if "frederick" in formatted.lower():
            return "Frederick"
        if "francisco" in formatted.lower():
            return "Francisco"
        return formatted
    return name_part.capitalize()


def hash_pin(pin: str) -> str:
    """Gera hash SHA-256 seguro para o PIN com a chave secreta do servidor."""
    raw = f"{AUTH_SECRET}:{pin.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_credentials() -> dict:
    """Lê o arquivo local de credenciais e PINs dos usuários."""
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in data.items()}
    except Exception as e:
        log.error(f"[AUTH] Erro ao ler credenciais de {CREDENTIALS_FILE}: {e}")
    return {}


def save_credentials(creds: dict) -> bool:
    """Salva atomicamente as credenciais dos usuários no arquivo local."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = CREDENTIALS_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(creds, f, indent=2, ensure_ascii=False)
        tmp_file.replace(CREDENTIALS_FILE)
        return True
    except Exception as e:
        log.error(f"[AUTH] Falha ao salvar credenciais em {CREDENTIALS_FILE}: {e}")
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return False


def load_auth_config() -> dict:
    """
    Carrega as configurações públicas de usuários e mescla dinamicamente com os PINs locais salvos.
    Isso garante que um git pull na lista de usuários nunca apague os PINs cadastrados na VM.
    """
    config_path = _get_config_path()
    raw_users = []

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "authorized_users" in data and isinstance(data["authorized_users"], list):
                raw_users = data["authorized_users"]
            elif "authorized_emails" in data and isinstance(data["authorized_emails"], list):
                raw_users = data["authorized_emails"]
        except Exception as e:
            log.error(f"[AUTH] Erro ao ler {config_path}: {e}")
            raw_users = []

    # Carrega PINs salvos localmente
    saved_creds = load_credentials()

    # Normaliza lista de usuários autorizados
    merged_users = []
    for item in raw_users:
        if isinstance(item, dict) and item.get("email"):
            email = str(item["email"]).strip().lower()
            name = item.get("name") or format_display_name_from_email(email)
            # Prioriza o PIN salvo em user_credentials.json
            pin = saved_creds.get(email) or str(item.get("pin", "")).strip()
            # Preserva o role definido no JSON; padrão OPERATOR se ausente
            role = str(item.get("role", "OPERATOR")).upper().strip()
            if role not in ("ADMIN", "OPERATOR"):
                role = "OPERATOR"
            merged_users.append({"email": email, "name": name, "pin": pin, "role": role})
        elif isinstance(item, str) and item.strip():
            email = item.strip().lower()
            name = format_display_name_from_email(email)
            pin = saved_creds.get(email, "")
            merged_users.append({"email": email, "name": name, "pin": pin, "role": "OPERATOR"})

    return {"authorized_users": merged_users}


def get_user_by_email(email: str) -> Optional[dict]:
    """Retorna o registro do usuário pelo e-mail se cadastrado."""
    if not email:
        return None
    email_clean = email.strip().lower()
    cfg = load_auth_config()
    for u in cfg.get("authorized_users", []):
        if u.get("email") == email_clean:
            return u
    return None


def is_email_authorized(email: str) -> bool:
    """Verifica se o e-mail está na lista de autorizados."""
    return get_user_by_email(email) is not None


def check_user_auth_status(email: str) -> dict:
    """
    Verifica se o e-mail é autorizado e se é o primeiro acesso (sem PIN cadastrado).
    Retorna: { authorized: bool, first_access: bool, name: str }
    """
    user = get_user_by_email(email)
    if not user:
        return {"authorized": False, "first_access": False, "name": ""}
    
    pin = str(user.get("pin", "")).strip()
    return {
        "authorized": True,
        "first_access": (pin == ""),
        "name": user.get("name") or format_display_name_from_email(email),
    }


def set_user_pin(email: str, pin: str) -> Optional[dict]:
    """
    Cadastra o PIN do usuário salvando diretamente em user_credentials.json.
    Dessa forma, o PIN persiste mesmo após commits e pulls do Git.
    """
    email_clean = email.strip().lower()
    pin_clean = str(pin).strip()
    if not pin_clean or len(pin_clean) < 4:
        raise ValueError("O PIN deve ter no mínimo 4 dígitos.")

    if not is_email_authorized(email_clean):
        return None

    # Salva no arquivo de credenciais local
    creds = load_credentials()
    hashed = hash_pin(pin_clean)
    creds[email_clean] = hashed

    if save_credentials(creds):
        user = get_user_by_email(email_clean)
        log.info("[AUTH] PIN cadastrado com sucesso para o operador %s (%s).", user.get("name"), email_clean)
        return user
    return None


def verify_user_pin(email: str, pin: str) -> Optional[dict]:
    """Verifica se o PIN informado confere com o PIN cadastrado localmente."""
    user = get_user_by_email(email)
    if not user:
        return None

    stored_pin = str(user.get("pin", "")).strip()
    if not stored_pin:
        # Sem PIN configurado -> primeiro acesso
        return None

    input_pin = str(pin).strip()
    hashed_input = hash_pin(input_pin)

    if hmac.compare_digest(stored_pin, hashed_input) or hmac.compare_digest(stored_pin, input_pin):
        return user

    log.warning("[AUTH] PIN incorreto fornecido para o e-mail: %s", email)
    return None


def get_google_client_id() -> str:
    """Retorna o Google Client ID configurado no JSON ou no .env se presente."""
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def verify_google_token(credential: str) -> Optional[dict]:
    """Valida token Google oficial se utilizado."""
    if not credential or not isinstance(credential, str):
        return None
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}"
        resp = requests.get(url, timeout=6)
        if resp.status_code != 200:
            return None
        info = resp.json()
        email = info.get("email", "").strip().lower()
        if not email or not info.get("email_verified") in (True, "true", "True"):
            return None
        # Busca o role do usuário cadastrado localmente (se existir)
        registered = get_user_by_email(email)
        role = registered.get("role", "OPERATOR") if registered else "OPERATOR"
        return {
            "email": email,
            "name": info.get("name") or format_display_name_from_email(email),
            "picture": info.get("picture", ""),
            "role": role,
        }
    except Exception as e:
        log.error(f"[AUTH] Erro ao validar Google token: {e}")
        return None


def create_session_token(user_data: dict, duration_seconds: Optional[int] = None) -> str:
    """
    Gera um token de sessão assinado com HMAC-SHA256.
    Inclui role e impersonated_by (se presente) no payload.
    Aceita duration_seconds opcional para tokens de impersonation (ex: 7200 = 2h).
    """
    duration = duration_seconds if duration_seconds is not None else SESSION_DURATION_SECONDS
    role = str(user_data.get("role", "OPERATOR")).upper()
    if role not in ("ADMIN", "OPERATOR"):
        role = "OPERATOR"

    payload: dict = {
        "email": user_data["email"],
        "name": user_data.get("name", "") or format_display_name_from_email(user_data["email"]),
        "picture": user_data.get("picture", ""),
        "role": role,
        "exp": int(time.time()) + duration,
        "iat": int(time.time()),
    }
    # Preserva impersonated_by se presente (sessão de impersonation)
    if user_data.get("impersonated_by"):
        payload["impersonated_by"] = str(user_data["impersonated_by"]).strip().lower()

    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")

    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{b64_payload}.{signature}"


def verify_session_token(token: str) -> Optional[dict]:
    """Valida a assinatura e expiração de um token de sessão. Retorna payload com role."""
    if not token or "." not in token:
        return None

    try:
        b64_payload, signature = token.split(".", 1)

        expected_sig = hmac.new(
            AUTH_SECRET.encode("utf-8"),
            b64_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        padding = "=" * (-len(b64_payload) % 4)
        raw_payload = base64.urlsafe_b64decode((b64_payload + padding).encode("utf-8"))
        payload = json.loads(raw_payload.decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            return None

        if not is_email_authorized(payload.get("email", "")):
            return None

        # Garante que role sempre está presente; fallback para o valor cadastrado
        if not payload.get("role"):
            registered = get_user_by_email(payload.get("email", ""))
            payload["role"] = registered.get("role", "OPERATOR") if registered else "OPERATOR"

        return payload
    except Exception:
        return None


def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Dependência FastAPI que protege rotas operacionais. Retorna payload com role."""
    cfg = load_auth_config()
    authorized_list = cfg.get("authorized_users", [])

    if not authorized_list:
        return {"email": "operador@local", "name": "Operador", "picture": "", "role": "ADMIN"}

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Autenticação obrigatória. Faça login no painel.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Formato de token inválido. Use 'Bearer <token>'.",
        )

    user = verify_session_token(token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida ou expirada. Faça login novamente com seu PIN.",
        )

    return user


# ---------------------------------------------------------------------------
# RBAC — Controle de Acesso Baseado em Roles
# ---------------------------------------------------------------------------

def is_admin(user: dict) -> bool:
    """Retorna True se o usuário possui role ADMIN."""
    return str(user.get("role", "")).upper() == "ADMIN"


from fastapi import Depends  # noqa: E402 — import aqui para evitar circular


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """
    Dependência FastAPI que restringe rotas exclusivas de ADMIN.
    Retorna HTTP 403 para usuários com role OPERATOR.
    """
    if not is_admin(user):
        log.warning(
            "[AUTH] Acesso negado a rota restrita para %s (role=%s).",
            user.get("email"),
            user.get("role"),
        )
        raise HTTPException(
            status_code=403,
            detail="Permissão negada. Apenas administradores podem realizar esta ação.",
        )
    return user


def create_impersonation_token(admin: dict, target_email: str) -> Optional[dict]:
    """
    Gera um token de sessão temporário (2h) para que um ADMIN
    atue em nome de um OPERATOR sem necessitar do PIN do alvo.
    Retorna dict com token e dados do usuário impersonado, ou None se inválido.
    """
    target = get_user_by_email(target_email)
    if not target:
        return None

    # Impersonation de outro ADMIN é bloqueada por segurança
    if is_admin(target):
        return None

    impersonated_user = {
        "email": target["email"],
        "name": target.get("name") or format_display_name_from_email(target["email"]),
        "picture": "",
        "role": target.get("role", "OPERATOR"),
        "impersonated_by": str(admin["email"]).strip().lower(),
    }
    token = create_session_token(impersonated_user, duration_seconds=7200)  # 2h
    log.info(
        "[IMPERSONATE] Admin %s iniciou sessão como operador %s.",
        admin["email"],
        target["email"],
    )
    return {"token": token, "user": impersonated_user}
