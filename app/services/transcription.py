import io
import logging

from groq import AsyncGroq

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.GROQ_API_KEY, timeout=60.0)
    return _client


async def transcribe(audio_bytes: bytes) -> str:
    """Transcreve áudio OGG/OPus (WhatsApp) usando Groq Whisper."""
    client = _get_client()
    audio_file = ("audio.ogg", io.BytesIO(audio_bytes), "audio/ogg")
    transcription = await client.audio.transcriptions.create(
        file=audio_file,
        model="whisper-large-v3-turbo",
        language="pt",
        response_format="text",
    )
    text = transcription.strip() if isinstance(transcription, str) else transcription.text.strip()
    logger.info("Transcrição: %s", text)
    return text
