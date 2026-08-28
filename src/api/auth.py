"""
src/api/auth.py
Módulo de autenticação Google e controle de permissões por lista branca em JSON.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import HTTPException, Header, Request

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


def load_auth_config() -> dict:
    """Carrega as configurações de e-mails autorizados e Google Client ID do JSON."""
    config_path = _get_config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        default_data = {
            "google_client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "authorized_emails": [
                email.strip().lower()
                for email in os.getenv("AUTHORIZED_EMAILS", "").split(",")
                if email.strip()
            ],
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
            # Normaliza os e-mails para minúsculas
            if "authorized_emails" in data and isinstance(data["authorized_emails"], list):
                data["authorized_emails"] = [
                    str(e).strip().lower() for e in data["authorized_emails"] if e
                ]
            return data
    except Exception as e:
        log.error(f"[AUTH] Erro ao ler {config_path}: {e}")
        return {
            "google_client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "authorized_emails": [],
        }


def get_google_client_id() -> str:
    """Retorna o Google Client ID configurado no JSON ou no .env."""
    cfg = load_auth_config()
    client_id = cfg.get("google_client_id") or os.getenv("GOOGLE_CLIENT_ID", "")
    return client_id.strip()


def is_email_authorized(email: str) -> bool:
    """Verifica se o e-mail está na lista de e-mails autorizados do JSON ou .env."""
    if not email:
        return False
    email_clean = email.strip().lower()

    cfg = load_auth_config()
    authorized = cfg.get("authorized_emails", [])
    
    # Se lista no JSON estiver vazia, verifica se há no .env
    if not authorized and os.getenv("AUTHORIZED_EMAILS"):
        authorized = [
            e.strip().lower()
            for e in os.getenv("AUTHORIZED_EMAILS", "").split(",")
            if e.strip()
        ]

    # Se ainda estiver vazia (modo permissivo inicial se configurado)
    if not authorized:
        log.warning("[AUTH] Nenhum e-mail configurado em authorized_users.json.")
        return False

    return email_clean in authorized


def verify_google_token(credential: str) -> Optional[dict]:
    """
    Valida a credencial Google (ID Token JWT) via endpoint oficial do Google.
    Retorna os dados do perfil (email, nome, foto) ou None se inválido.
    """
    if not credential or not isinstance(credential, str):
        return None

    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}"
        resp = requests.get(url, timeout=6)
        if resp.status_code != 200:
            log.warning(f"[AUTH] Google TokenInfo retornou status {resp.status_code}: {resp.text}")
            return None

        info = resp.json()
        email = info.get("email", "").strip().lower()
        email_verified = info.get("email_verified") in (True, "true", "True")

        if not email or not email_verified:
            log.warning(f"[AUTH] E-mail não verificado ou ausente no token Google: {info}")
            return None

        return {
            "email": email,
            "name": info.get("name") or info.get("given_name") or email.split("@")[0],
            "picture": info.get("picture", ""),
            "sub": info.get("sub", ""),
        }
    except Exception as e:
        log.error(f"[AUTH] Erro ao validar token com a Google: {e}")
        return None


def format_display_name_from_email(email: str) -> str:
    """Extrai e formata um nome amigável a partir do e-mail."""
    if not email or "@" not in email:
        return "Operador"
    name_part = email.split("@")[0].strip()
    import re
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


def create_session_token(user_data: dict) -> str:
    """Gera um token de sessão assinado com HMAC-SHA256."""
    payload = {
        "email": user_data["email"],
        "name": user_data.get("name", ""),
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

        # Valida assinatura
        expected_sig = hmac.new(
            AUTH_SECRET.encode("utf-8"),
            b64_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            log.warning("[AUTH] Assinatura de sessão inválida.")
            return None

        # Decodifica payload com padding
        padding = "=" * (-len(b64_payload) % 4)
        raw_payload = base64.urlsafe_b64decode((b64_payload + padding).encode("utf-8"))
        payload = json.loads(raw_payload.decode("utf-8"))

        # Checa expiração
        if payload.get("exp", 0) < time.time():
            log.info(f"[AUTH] Sessão expirada para {payload.get('email')}.")
            return None

        # Checa se o e-mail ainda está na lista autorizada
        if not is_email_authorized(payload.get("email", "")):
            log.warning(f"[AUTH] E-mail {payload.get('email')} foi removido da lista de autorizados.")
            return None

        return payload
    except Exception as e:
        log.warning(f"[AUTH] Erro ao decodificar token de sessão: {e}")
        return None


def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """
    Dependência FastAPI que garante que a requisição é de um usuário autorizado.
    Se a autenticação estiver ativa e o token não for enviado ou for inválido, lança HTTP 401.
    """
    google_id = get_google_client_id()
    cfg = load_auth_config()
    authorized_list = cfg.get("authorized_emails", [])

    # Se não houver google_client_id ou emails configurados, permite acesso em modo legado
    if not google_id and not authorized_list:
        return {"email": "operador@local", "name": "Operador", "picture": ""}

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Autenticação obrigatória. Por favor, faça login com sua conta Google.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Formato de cabeçalho de autorização inválido. Use 'Bearer <token>'.",
        )

    user = verify_session_token(token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida ou expirada. Faça login novamente.",
        )

    return user
