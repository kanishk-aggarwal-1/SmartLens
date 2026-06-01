from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .settings import settings
from .services.ai import (
    AIServiceError,
    get_cache_stats as get_ai_cache_stats,
    gemini_chat,
    gemini_translate,
    mock_chat,
    mock_translate,
)
from .services.directions import (
    get_cache_stats as get_route_cache_stats,
    get_walking_route_steps,
    RouteServiceError,
)
from .services.monitoring import logger, metrics
from .services.security import (
    authenticated_username,
    clear_session_cookie,
    create_session_token,
    enforce_rate_limit,
    require_authenticated_request,
    set_session_cookie,
    hash_secret,
    verify_secret,
)
from .services.users import UserRecord, create_user, get_user_by_email, get_user_by_id, init_user_store, update_user_keys

app = FastAPI(title="Smart Glasses Demo")

allowed_hosts = [item.strip() for item in settings.ALLOWED_HOSTS.split(",") if item.strip()]
if allowed_hosts and allowed_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

cors_origins = [item.strip() for item in settings.CORS_ORIGINS.split(",") if item.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def error_payload(
    *,
    code: str,
    message: str,
    provider: str | None = None,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if provider:
        payload["provider"] = provider
    if details:
        payload["details"] = details
    return payload


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        metrics.record_latency(f"http {request.url.path}", duration_ms, success=status_code < 500)
        logger.info(
            "request path=%s method=%s status=%s duration_ms=%.2f",
            request.url.path,
            request.method,
            status_code,
            duration_ms,
        )


@app.middleware("http")
async def apply_security_headers(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_size = int(content_length)
        except ValueError:
            body_size = 0
        if body_size > settings.MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content=error_payload(
                    code="REQUEST_TOO_LARGE",
                    message="Request body is too large.",
                    retryable=False,
                ),
            )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=(self)")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https" and settings.HSTS_MAX_AGE_S > 0:
        response.headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={settings.HSTS_MAX_AGE_S}; includeSubDomains",
        )
    return response


@app.on_event("startup")
async def validate_config_on_startup() -> None:
    settings.require_runtime_ready()
    init_user_store()


@app.get("/health")
async def health():
    issues = settings.runtime_issues()
    return JSONResponse(
        {
            "status": "ok" if not issues else "degraded",
            "ai_mode": settings.normalized_ai_mode(),
            "issues": issues,
        }
    )


@app.get("/metrics")
async def get_metrics(_user: str = Depends(require_authenticated_request)):
    return JSONResponse(
        {
            "status": "ok",
            "metrics": metrics.summary(),
            "cache": {
                **get_route_cache_stats(),
                **get_ai_cache_stats(),
            },
        }
    )


@app.get("/metrics/prometheus")
async def get_prometheus_metrics(_user: str = Depends(require_authenticated_request)):
    return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = current_user_from_request(request)
    if settings.REQUIRE_AUTH and not user:
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "ALLOW_SIGNUPS": settings.ALLOW_SIGNUPS,
            },
        )

    google_maps_api_key = user.google_maps_api_key if user else settings.GOOGLE_MAPS_API_KEY
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "GOOGLE_MAPS_API_KEY": google_maps_api_key,
            "USE_MOCK_MAPS": request.query_params.get("mockMaps") == "1",
            "USER_EMAIL": user.email if user else "",
        },
    )


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    google_maps_api_key: str = Field(min_length=1)
    gemini_api_key: str | None = None


class KeysRequest(BaseModel):
    google_maps_api_key: str = Field(min_length=1)
    gemini_api_key: str | None = None


def current_user_from_request(request: Request) -> UserRecord | None:
    subject = authenticated_username(request)
    if not subject or subject == "api-key":
        return None
    return get_user_by_id(subject)


def require_current_user(request: Request) -> UserRecord:
    if not settings.REQUIRE_AUTH:
        return UserRecord(
            id="anonymous",
            email="",
            password_hash="",
            google_maps_api_key_encrypted=None,
            gemini_api_key_encrypted=None,
        )

    subject = authenticated_username(request)
    if subject == "api-key":
        return UserRecord(
            id="api-key",
            email="api-key",
            password_hash="",
            google_maps_api_key_encrypted=None,
            gemini_api_key_encrypted=None,
        )

    user = current_user_from_request(request)
    if user:
        return user

    metrics.increment("auth_failed", tags={"path": request.url.path})
    raise HTTPException(
        status_code=401,
        detail={"code": "AUTH_REQUIRED", "message": "Authentication is required.", "retryable": False},
    )


@app.post("/auth/signup")
async def signup(payload: SignupRequest, request: Request, response: Response):
    if not settings.ALLOW_SIGNUPS:
        raise HTTPException(
            status_code=403,
            detail={"code": "SIGNUPS_DISABLED", "message": "Signups are disabled.", "retryable": False},
        )
    enforce_rate_limit(request, scope="signup", limit=settings.LOGIN_RATE_LIMIT_PER_MINUTE)

    existing = get_user_by_email(payload.email)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"code": "EMAIL_EXISTS", "message": "An account already exists.", "retryable": False},
        )

    user = create_user(
        email=payload.email,
        password_hash=hash_secret(payload.password),
        google_maps_api_key=payload.google_maps_api_key,
        gemini_api_key=payload.gemini_api_key,
    )
    set_session_cookie(response, create_session_token(user.id))
    metrics.increment("auth_signup_success")
    return {"status": "ok", "email": user.email}


@app.post("/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    enforce_rate_limit(
        request,
        scope="login",
        limit=settings.LOGIN_RATE_LIMIT_PER_MINUTE,
    )

    user = get_user_by_email(payload.email)
    if not user or not verify_secret(payload.password, user.password_hash):
        metrics.increment("auth_failed", tags={"path": "/auth/login"})
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Invalid username or password.",
                "retryable": False,
            },
        )

    token = create_session_token(user.id)
    set_session_cookie(response, token)
    metrics.increment("auth_login_success")
    return {"status": "ok", "email": user.email}


@app.post("/auth/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/auth/me")
async def auth_me(request: Request):
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Authentication is required.", "retryable": False},
        )
    return {
        "status": "ok",
        "email": user.email,
        "has_google_maps_api_key": bool(user.google_maps_api_key_encrypted),
        "has_gemini_api_key": bool(user.gemini_api_key_encrypted),
    }


@app.post("/auth/keys")
async def save_keys(payload: KeysRequest, request: Request, response: Response):
    user = require_current_user(request)
    updated = update_user_keys(
        user_id=user.id,
        google_maps_api_key=payload.google_maps_api_key,
        gemini_api_key=payload.gemini_api_key,
    )
    set_session_cookie(response, create_session_token(updated.id))
    return {"status": "ok", "email": updated.email}


class RouteRequest(BaseModel):
    origin: dict[str, float]
    destination: dict[str, float]
    waypoints: list[dict[str, float]] = Field(default_factory=list)
    route_index: int = 0
    include_alternatives: bool = True


@app.post("/api/route")
async def api_route(
    payload: RouteRequest,
    request: Request,
    user: UserRecord = Depends(require_current_user),
):
    enforce_rate_limit(request, scope="api", limit=settings.API_RATE_LIMIT_PER_MINUTE)
    try:
        data = await get_walking_route_steps(
            google_api_key=user.google_maps_api_key,
            origin=payload.origin,
            destination=payload.destination,
            waypoints=payload.waypoints,
            route_index=payload.route_index,
            include_alternatives=payload.include_alternatives,
        )
    except RouteServiceError as exc:
        raise HTTPException(
            status_code=502 if exc.retryable else 400,
            detail=error_payload(
                code=exc.code,
                message=str(exc),
                provider=exc.provider,
                retryable=exc.retryable,
            ),
        ) from exc
    return JSONResponse(data)


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "auto"
    target_lang: str = "en"


@app.post("/api/translate")
async def api_translate(
    payload: TranslateRequest,
    request: Request,
    user: UserRecord = Depends(require_current_user),
):
    enforce_rate_limit(request, scope="api", limit=settings.API_RATE_LIMIT_PER_MINUTE)
    try:
        mode = settings.normalized_ai_mode()
        gemini_api_key = user.gemini_api_key
        if mode == "gemini" and gemini_api_key:
            data = await gemini_translate(
                gemini_api_key,
                settings.GEMINI_MODEL,
                payload.text,
                payload.source_lang,
                payload.target_lang,
            )
        else:
            data = mock_translate(payload.text, payload.target_lang, payload.source_lang)
    except AIServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_payload(
                code="AI_TRANSLATION_FAILED",
                message=str(exc),
                provider="gemini",
                retryable=True,
            ),
        ) from exc
    return JSONResponse(data)


class ChatRequest(BaseModel):
    message: str
    intent: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/chat")
async def api_chat(
    payload: ChatRequest,
    request: Request,
    user: UserRecord = Depends(require_current_user),
):
    enforce_rate_limit(request, scope="api", limit=settings.API_RATE_LIMIT_PER_MINUTE)
    try:
        mode = settings.normalized_ai_mode()
        gemini_api_key = user.gemini_api_key
        if mode == "gemini" and gemini_api_key:
            data = await gemini_chat(
                gemini_api_key,
                settings.GEMINI_MODEL,
                user.google_maps_api_key,
                payload.message,
                context=payload.context,
                intent=payload.intent,
            )
        else:
            data = mock_chat(payload.message, context=payload.context, intent=payload.intent)
    except AIServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_payload(
                code="AI_CHAT_FAILED",
                message=str(exc),
                provider="gemini",
                retryable=True,
            ),
        ) from exc
    return JSONResponse(data)
