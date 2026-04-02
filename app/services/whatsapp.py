import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SESSION = "default"


def _headers() -> dict:
    return {"X-Api-Key": settings.WAHA_API_KEY, "Content-Type": "application/json"}


async def send_text(text: str) -> None:
    """Envia mensagem de texto para o dono (self-chat).

    Raises:
        httpx.HTTPStatusError: se o WAHA retornar status de erro.
        httpx.TimeoutException: se o WAHA não responder a tempo.
    """
    url = f"{settings.WAHA_URL}/api/sendText"
    payload = {
        "session": _SESSION,
        "chatId": f"{settings.OWNER_PHONE}@c.us",
        "text": text,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_headers())

    if response.status_code not in (200, 201):
        logger.error("Falha ao enviar mensagem. status=%s body=%s", response.status_code, response.text)
        response.raise_for_status()

    logger.debug("Mensagem enviada com sucesso.")


async def download_audio(message: dict) -> bytes:
    """Baixa o arquivo de áudio via URL fornecida pelo WAHA no campo media."""
    media = message.get("media") or {}
    url = media.get("url", "")
    if not url:
        raise ValueError("URL de áudio não encontrada no payload.")
    # WAHA retorna localhost:3000 — substituir pelo hostname interno do Docker
    url = url.replace("localhost:3000", "waha:3000")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=_headers())
    response.raise_for_status()
    return response.content
