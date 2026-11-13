import logging
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from app.config import get_settings
from app.routers import auth, health, imports, pages
from app.security import get_csrf_token
from app.web import BASE_DIR, templates

logger = logging.getLogger(__name__)

settings = get_settings()
is_production = settings.environment.casefold() in {"production", "prod"}
session_secret_key = settings.session_secret_key
if is_production and len(session_secret_key) < 32:
    raise RuntimeError(
        "SESSION_SECRET_KEY must contain at least 32 characters in production."
    )
if not session_secret_key:
    session_secret_key = secrets.token_urlsafe(32)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_key,
    session_cookie="akashic_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=is_production,
)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(imports.router)
app.include_router(health.router)


@app.exception_handler(404)
async def not_found_page(request: Request, _exception: Exception) -> HTMLResponse:
    del _exception
    logger.info("Page not found: %s %s", request.method, request.url.path)
    return templates.TemplateResponse(
        request=request,
        name="errors/404.html",
        context={"app_name": get_settings().app_name},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden_page(request: Request, exception: HTTPException) -> HTMLResponse:
    message = (
        exception.detail
        if isinstance(exception.detail, str)
        else "この操作を行う権限がありません。"
    )
    return templates.TemplateResponse(
        request=request,
        name="errors/403.html",
        context={
            "app_name": settings.app_name,
            "current_user": getattr(request.state, "current_user", None),
            "csrf_token": get_csrf_token(request),
            "error_message": message,
        },
        status_code=403,
    )


@app.middleware("http")
async def add_security_headers(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response
