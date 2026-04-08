from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .settings import settings
from .services.directions import get_walking_route_steps
from .services.ai import (
    AIServiceError,
    gemini_chat,
    gemini_translate,
    mock_chat,
    mock_translate,
)

app = FastAPI(title="Smart Glasses Demo")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
async def validate_config_on_startup() -> None:
    settings.require_runtime_ready()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "GOOGLE_MAPS_API_KEY": settings.GOOGLE_MAPS_API_KEY},
    )


class RouteRequest(BaseModel):
    origin: dict[str, float]
    destination: dict[str, float]


@app.post("/api/route")
async def api_route(payload: RouteRequest):
    data = await get_walking_route_steps(
        google_api_key=settings.GOOGLE_MAPS_API_KEY,
        origin=payload.origin,
        destination=payload.destination,
    )
    return JSONResponse(data)


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "auto"
    target_lang: str = "en"


@app.post("/api/translate")
async def api_translate(payload: TranslateRequest):
    try:
        mode = settings.normalized_ai_mode()
        if mode == "gemini" and settings.GEMINI_API_KEY:
            data = await gemini_translate(
                settings.GEMINI_API_KEY,
                settings.GEMINI_MODEL,
                payload.text,
                payload.source_lang,
                payload.target_lang,
            )
        else:
            data = mock_translate(payload.text, payload.target_lang, payload.source_lang)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(data)


class ChatRequest(BaseModel):
    message: str
    intent: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/chat")
async def api_chat(payload: ChatRequest):
    try:
        mode = settings.normalized_ai_mode()
        if mode == "gemini" and settings.GEMINI_API_KEY:
            data = await gemini_chat(
                settings.GEMINI_API_KEY,
                settings.GEMINI_MODEL,
                settings.GOOGLE_MAPS_API_KEY,
                payload.message,
                context=payload.context,
                intent=payload.intent,
            )
        else:
            data = mock_chat(payload.message, context=payload.context, intent=payload.intent)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(data)
