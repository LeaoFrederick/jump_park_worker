"""
src/core/jumppark_client.py
Cliente HTTP para integração com a API da Jump Park.
"""

import logging
from datetime import datetime, timedelta
import requests

from src.config import BASE_URL, WINDOW_DAYS, EstablishmentConfig, build_headers

log = logging.getLogger(__name__)


def get_blocked_plates(config: EstablishmentConfig) -> list[str]:
    """Busca as placas vinculadas ao cliente 'CARRO BLOQUEADO' de um estabelecimento."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles"
    )
    try:
        response = requests.get(url, headers=build_headers(config), timeout=10)
        if response.status_code == 200:
            data = response.json().get("data", [])
            plates = [v.get("plate") for v in data if v.get("plate")]
            return plates
        if response.status_code == 401:
            log.error(
                "[CACHE][%s] Erro ao buscar veículos: HTTP 401 (Não autorizado). "
                "Verifique se ACCESS_TOKEN e INTEGRATION_ID estão corretos e se "
                "ORIGIN está definido com o domínio cadastrado no Site Admin.\n"
                "  URL usada: %s", config.label, url,
            )
        else:
            log.error("[CACHE][%s] Erro ao buscar veículos: HTTP %s", config.label, response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[CACHE][%s] Exceção na requisição: %s", config.label, exc)
    return []


def get_service_orders(config: EstablishmentConfig, window_days: int = WINDOW_DAYS) -> list[dict]:
    """Busca ordens de serviço dos últimos `window_days` dias de um estabelecimento."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        "/serviceorders/export/json"
    )
    agora  = datetime.now()
    inicio = agora - timedelta(days=window_days)
    params = {
        "startDate": inicio.strftime("%Y-%m-%d"),
        "endDate":   agora.strftime("%Y-%m-%d"),
        "startTime": "00:00:00",
        "endTime":   "23:59:59",
    }
    try:
        response = requests.get(url, headers=build_headers(config), params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("data", {}).get("content", [])
        log.error("[API][%s] Erro ao buscar ordens: HTTP %s", config.label, response.status_code)
    except requests.exceptions.RequestException as exc:
        log.error("[API][%s] Exceção na requisição: %s", config.label, exc)
    return []


def unlock_vehicle(config: EstablishmentConfig, plate: str) -> bool:
    """Remove o veículo da conta 'CARRO BLOQUEADO', liberando-o no sistema."""
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/{plate}"
    )
    try:
        response = requests.delete(url, headers=build_headers(config), timeout=10)
        if response.status_code == 200:
            log.info("[UNLOCK][%s] Placa %s desbloqueada com sucesso.", config.label, plate)
            return True
        log.error(
            "[UNLOCK][%s] Falha ao desbloquear %s: HTTP %s — %s",
            config.label, plate, response.status_code, response.text,
        )
    except requests.exceptions.RequestException as exc:
        log.error("[UNLOCK][%s] Exceção ao desbloquear %s: %s", config.label, plate, exc)
    return False


def block_vehicle_on_establishment(config: EstablishmentConfig, plate: str) -> dict:
    """
    Adiciona veículo (placa) ao cliente 'CARRO BLOQUEADO' de um estabelecimento.
    POST /api/{integrationId}/public/establishment/{establishmentId}/clients/{clientId}/vehicles/new
    """
    plate_norm = plate.upper().strip()

    # 1. Verifica previamente se a placa já consta no cadastro deste estabelecimento
    try:
        current_plates = get_blocked_plates(config)
        if plate_norm in current_plates:
            log.info("[BLOCK][%s] Placa %s já consta na lista de bloqueados.", config.label, plate_norm)
            return {"estabelecimento": config.label, "status": "already_blocked", "detail": "Placa já cadastrada"}
    except Exception:
        pass

    # 2. Se não constava na checagem, tenta cadastrar via POST
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/new"
    )
    headers = build_headers(config)
    headers["Content-Type"] = "application/json"

    body = {
        "establishmentId": int(config.establishment_id),
        "plate": plate_norm,
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("response") == "success":
                log.info(
                    "[BLOCK][%s] Placa %s vinculada ao CARRO BLOQUEADO com sucesso.",
                    config.label, plate_norm,
                )
                return {"estabelecimento": config.label, "status": "ok", "detail": "Placa bloqueada com sucesso"}
            else:
                detail = data.get("data", {})
                msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else str(detail)
                is_already = any(k in msg.lower() for k in ["cadastrad", "já existe", "ja existe", "duplicad", "already", "vinculad", "já está", "ja esta"])
                if is_already:
                    log.info("[BLOCK][%s] Placa %s já está vinculada: %s", config.label, plate_norm, msg)
                    return {"estabelecimento": config.label, "status": "already_blocked", "detail": "Placa já cadastrada"}
                log.warning("[BLOCK][%s] Resposta inesperada para %s: %s", config.label, plate_norm, msg)
                return {"estabelecimento": config.label, "status": "warning", "detail": msg}

        # Tratamento de status codes diferentes de 200 (ex: 400 com "Placa já está cadastrada para este cliente")
        try:
            data = response.json()
            detail = data.get("data", {})
            msg = detail.get("msg", str(detail)) if isinstance(detail, dict) else (data.get("message") or response.text[:200])
        except Exception:
            msg = response.text[:200]

        is_already = any(k in msg.lower() for k in ["cadastrad", "já existe", "ja existe", "duplicad", "already", "vinculad", "já está", "ja esta"])
        if is_already:
            log.info("[BLOCK][%s] Placa %s já estava cadastrada: %s", config.label, plate_norm, msg)
            return {"estabelecimento": config.label, "status": "already_blocked", "detail": "Placa já cadastrada"}

        log.error(
            "[BLOCK][%s] Falha ao bloquear %s: HTTP %s — %s",
            config.label, plate_norm, response.status_code, msg,
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"{config.label} (HTTP {response.status_code}: {msg})",
        }
    except requests.exceptions.RequestException as exc:
        log.error("[BLOCK][%s] Exceção ao bloquear %s: %s", config.label, plate_norm, exc)
        return {"estabelecimento": config.label, "status": "error", "detail": str(exc)}


def unblock_vehicle_on_establishment(config: EstablishmentConfig, plate: str) -> dict:
    """
    Remove veículo (placa) do cliente "CARRO BLOQUEADO" de um estabelecimento.
    DELETE /api/{integrationId}/public/establishment/{establishmentId}/clients/{clientId}/vehicles/{plate}
    """
    plate_norm = plate.upper().strip()

    # 1. Verifica se a placa REALMENTE existe na lista de bloqueados deste estabelecimento
    try:
        current_plates = get_blocked_plates(config)
        if plate_norm not in current_plates:
            log.info("[UNBLOCK][%s] Placa %s não consta na lista de bloqueados.", config.label, plate_norm)
            return {"estabelecimento": config.label, "status": "not_found", "detail": "Placa não encontrada"}
    except Exception as exc:
        log.warning("[UNBLOCK][%s] Não foi possível verificar lista prévia de placas: %s", config.label, exc)

    # 2. Se a placa existe na lista, executa a requisição DELETE para remover
    url = (
        f"{BASE_URL}/api/{config.integration_id}"
        f"/public/establishment/{config.establishment_id}"
        f"/clients/{config.blocked_client_id}/vehicles/{plate_norm}"
    )
    try:
        response = requests.delete(url, headers=build_headers(config), timeout=10)
        if response.status_code == 200:
            log.info(
                "[UNBLOCK][%s] Placa %s removida do CARRO BLOQUEADO com sucesso.",
                config.label, plate_norm,
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
            return {"estabelecimento": config.label, "status": "not_found", "detail": "Placa não encontrada"}

        log.error(
            "[UNBLOCK][%s] Falha ao desbloquear %s: HTTP %s — %s",
            config.label, plate_norm, response.status_code, msg,
        )
        return {
            "estabelecimento": config.label,
            "status": "error",
            "detail": f"{config.label} (HTTP {response.status_code}: {msg})",
        }
    except requests.exceptions.RequestException as exc:
        log.error("[UNBLOCK][%s] Exceção ao desbloquear %s: %s", config.label, plate_norm, exc)
        return {"estabelecimento": config.label, "status": "error", "detail": str(exc)}
