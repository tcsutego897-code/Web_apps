import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import health, imports, pages
from app.web import BASE_DIR, templates
logger = logging.getLogger(__name__)

app = FastAPI(title=get_settings().app_name)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
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
