import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_BRT = ZoneInfo("America/Sao_Paulo")


def _hoje() -> date:
    """Dia operacional (06:00–05:59). Antes das 6h conta como dia anterior."""
    agora = datetime.now(_BRT)
    if agora.hour < 6:
        return (agora - timedelta(days=1)).date()
    return agora.date()


from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.database import (
    init_db, close_db,
    buscar_entradas_dashboard, buscar_saidas_dashboard,
    buscar_estoque_resumo, buscar_fluxo_caixa,
)
from app.dashboard import DASHBOARD_HTML
import httpx

from app.services.whatsapp import send_text, download_audio
from app.services import transcription, nlu, router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Ciclo de vida
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Banco de dados conectado.")
    yield
    await close_db()
    logger.info("Banco de dados desconectado.")


app = FastAPI(title="Choperia Bot", lifespan=lifespan)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/api/entradas")
async def api_entradas(de: str | None = None, ate: str | None = None):
    today = _hoje()
    d_de = date.fromisoformat(de) if de else today.replace(day=1)
    d_ate = date.fromisoformat(ate) if ate else today
    rows = await buscar_entradas_dashboard(d_de, d_ate)
    return JSONResponse([
        {**r, "criado_em": r["criado_em"].isoformat(), "litros": float(r["litros"]) if r["litros"] is not None else None,
         "quantidade": float(r["quantidade"]), "valor_unitario": float(r["valor_unitario"]), "valor_total": float(r["valor_total"])}
        for r in rows
    ])


@app.get("/api/saidas")
async def api_saidas(de: str | None = None, ate: str | None = None):
    today = _hoje()
    d_de = date.fromisoformat(de) if de else today.replace(day=1)
    d_ate = date.fromisoformat(ate) if ate else today
    rows = await buscar_saidas_dashboard(d_de, d_ate)
    return JSONResponse([
        {**r, "criado_em": r["criado_em"].isoformat(),
         "quantidade": float(r["quantidade"]), "valor_unitario": float(r["valor_unitario"]), "valor_total": float(r["valor_total"])}
        for r in rows
    ])


@app.get("/api/estoque")
async def api_estoque():
    rows = await buscar_estoque_resumo()
    return JSONResponse(rows)


@app.get("/api/fluxo")
async def api_fluxo(de: str | None = None, ate: str | None = None):
    today = _hoje()
    d_de = date.fromisoformat(de) if de else today.replace(day=1)
    d_ate = date.fromisoformat(ate) if ate else today
    data = await buscar_fluxo_caixa(d_de, d_ate)
    return JSONResponse(data)


@app.post("/webhook")
async def webhook(request: Request):
    raw: dict[str, Any] = await request.json()

    # DEBUG: loga evento e todas as chaves do payload
    event = raw.get("event", "")
    payload_debug = raw.get("payload", {})
    if payload_debug.get("fromMe") and payload_debug.get("source") == "app":
        logger.info("DEBUG payload keys: %s", list(payload_debug.keys()))
        logger.info("DEBUG payload parcial: from=%s to=%s chatId=%s _data.self=%s",
            payload_debug.get("from"),
            payload_debug.get("to"),
            payload_debug.get("chatId"),
            payload_debug.get("_data", {}).get("self"),
        )
    logger.info(
        "Webhook recebido: event=%s fromMe=%s source=%s from=%s type=%s",
        event,
        payload_debug.get("fromMe"),
        payload_debug.get("source"),
        payload_debug.get("from"),
        payload_debug.get("type"),
    )

    # WAHA Core envia apenas "message.any"
    if event not in ("message", "message.any"):
        return {"status": "ignored"}

    payload = raw.get("payload", {})
    if not payload:
        return {"status": "ignored"}

    # Só processa mensagens do autochat (conversa consigo mesmo).
    # fromMe=True + source="app" → dono digitou/gravou no celular ✅
    # to termina com @lid → autochat (mensagens para outros usam @c.us) ✅
    if not payload.get("fromMe", False) or payload.get("source") != "app":
        return {"status": "ignored"}

    to = payload.get("to", "")
    if not to.endswith("@lid"):
        return {"status": "ignored"}

    # WAHA coloca o type em _data.type, não no topo do payload
    msg_type = payload.get("type") or payload.get("_data", {}).get("type", "")
    logger.info("Mensagem recebida. tipo=%s", msg_type)

    try:
        if msg_type == "chat" or (not msg_type and payload.get("body")):
            text = payload.get("body", "").strip()
            await _process_text(text)

        elif msg_type in ("audio", "ptt"):
            await _process_audio(payload)

        else:
            logger.info("Tipo de mensagem não suportado: %s", msg_type)

    except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
        logger.error("Falha ao enviar resposta via WAHA: %s", exc)
    except Exception as exc:
        logger.exception("Erro ao processar mensagem: %s", exc)
        try:
            await send_text("⚠️ *Erro interno.* Tente novamente.")
        except (httpx.HTTPStatusError, httpx.TimeoutException):
            logger.error("Falha ao enviar mensagem de erro via WAHA.")

    return {"status": "ok"}


# ------------------------------------------------------------------
# Processadores
# ------------------------------------------------------------------

async def _process_text(text: str, transcription_prefix: str = "") -> None:
    if not text:
        return
    logger.info("Processando texto: %s", text)

    # Intercepta confirmação de pagamento sem chamar o NLU
    if router.has_pending_payment() and text.strip().lower() in (
        "sim", "s", "yes", "confirma", "confirmar",
        "não", "nao", "no", "n", "cancela", "cancelar",
    ):
        action = {"intent": "desconhecido", "params": {"mensagem": text.strip()}}
    else:
        action = await nlu.extract_action(text)

    reply = await router.dispatch(action)
    await send_text(f"{transcription_prefix}{reply}" if transcription_prefix else reply)


async def _process_audio(payload: dict) -> None:
    logger.info("Processando áudio...")
    try:
        audio_bytes = await download_audio(payload)
    except Exception as exc:
        logger.exception("Falha ao baixar áudio: %s", exc)
        await send_text("⚠️ Não consegui baixar o áudio. Tente enviar novamente ou digite o comando.")
        return

    try:
        text = await transcription.transcribe(audio_bytes)
    except Exception as exc:
        logger.exception("Falha na transcrição: %s", exc)
        await send_text("⚠️ Não consegui transcrever o áudio. Tente novamente ou digite o comando.")
        return

    prefix = f"🎤 *Entendi:* _{text}_\n\n"
    await _process_text(text, transcription_prefix=prefix)
