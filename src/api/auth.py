"""
src/api/auth.py
Módulo de autenticação por e-mail, PIN no primeiro acesso e controle de permissões em JSON.
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

# Diretório raiz e caminho do arquivo de usuários autorizados
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "config" / "authorized_users.json"
FALLBACK_CONFIG_FILE = BASE_DIR / "authorized_users.json"

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


def load_auth_config() -> dict:
    """Carrega as configurações e normaliza a lista de usuários autorizados."""
    config_path = _get_config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        default_data = {
            "authorized_users": []
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[AUTH] Falha ao criar arquivo de config padrão: {e}")
        return default_data

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Normaliza formato legado "authorized_emails: [str]" para "authorized_users: [dict]"
        users = []
        if "authorized_users" in data and isinstance(data["authorized_users"], list):
            for u in data["authorized_users"]:
                if isinstance(u, dict) and u.get("email"):
                    email = str(u["email"]).strip().lower()
                    name = u.get("name") or format_display_name_from_email(email)
                    pin = str(u.get("pin", "")).strip()
                    users.append({"email": email, "name": name, "pin": pin})
                elif isinstance(u, str) and u.strip():
                    email = u.strip().lower()
                    users.append({"email": email, "name": format_display_name_from_email(email), "pin": ""})
        elif "authorized_emails" in data and isinstance(data["authorized_emails"], list):
            for e in data["authorized_emails"]:
                if isinstance(e, str) and e.strip():
                    email = e.strip().lower()
                    users.append({"email": email, "name": format_display_name_from_email(email), "pin": ""})

        data["authorized_users"] = users
        return data
    except Exception as e:
        log.error(f"[AUTH] Erro ao ler {config_path}: {e}")
        return {"authorized_users": []}


def save_auth_config(data: dict) -> bool:
    """Salva atomicamente as configurações de usuários no arquivo JSON."""
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(config_path)
        return True
    except Exception as e:
        log.error(f"[AUTH] Falha ao salvar {config_path}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False


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
    """Cadastra ou atualiza o PIN de um e-mail autorizado no JSON."""
    email_clean = email.strip().lower()
    pin_clean = str(pin).strip()
    if not pin_clean or len(pin_clean) < 4:
        raise ValueError("O PIN deve ter no mínimo 4 dígitos.")

    cfg = load_auth_config()
    user_found = None
    for u in cfg.get("authorized_users", []):
        if u.get("email") == email_clean:
            # Salva o PIN (usando hash para segurança)
            u["pin"] = hash_pin(pin_clean)
            if not u.get("name"):
                u["name"] = format_display_name_from_email(email_clean)
            user_found = u
            break

    if not user_found:
        return None

    if save_auth_config(cfg):
        log.info("[AUTH] PIN cadastrado com sucesso para o operador %s (%s).", user_found["name"], email_clean)
        return user_found
    return None


def verify_user_pin(email: str, pin: str) -> Optional[dict]:
    """Verifica se o PIN informado confere com o PIN cadastrado no JSON."""
    user = get_user_by_email(email)
    if not user:
        return None

    stored_pin = str(user.get("pin", "")).strip()
    if not stored_pin:
        # Sem PIN configurado -> primeiro acesso
        return None

    input_pin = str(pin).strip()
    # Suporta tanto hash SHA256 quanto texto puro (caso editado manualmente no JSON)
    hashed_input = hash_pin(input_pin)

    if hmac.compare_digest(stored_pin, hashed_input) or hmac.compare_digest(stored_pin, input_pin):
        return user

    log.warning("[AUTH] PIN incorreto fornecido para o e-mail: %s", email)
    return None


def get_google_client_id() -> str:
    """Retorna o Google Client ID configurado no JSON ou no .env se presente."""
    cfg = load_auth_config()
    client_id = cfg.get("google_client_id") or os.getenv("GOOGLE_CLIENT_ID", "")
    return client_id.strip()


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
        return {
            "email": email,
            "name": info.get("name") or format_display_name_from_email(email),
            "picture": info.get("picture", ""),
        }
    except Exception as e:
        log.error(f"[AUTH] Erro ao validar Google token: {e}")
        return None


def create_session_token(user_data: dict) -> str:
    """Gera um token de sessão assinado com HMAC-SHA256."""
    payload = {
        "email": user_data["email"],
        "name": user_data.get("name", "") or format_display_name_from_email(user_data["email"]),
        "picture": user_data.get("picture", ""),
        "exp": int(time.time()) + SESSION_DURATION_SECONDS,
        "iat": int(time.time()),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")

    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{b64_payload}.{signature}"


def verify_session_token(token: str) -> Optional[dict]:
    """Valida a assinatura e expiração de um token de sessão."""
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

        return payload
    except Exception as e:
        return None


def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Dependência FastAPI que protege rotas operacionais."""
    cfg = load_auth_config()
    authorized_list = cfg.get("authorized_users", [])

    if not authorized_list:
        return {"email": "operador@local", "name": "Operador", "picture": ""}

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
