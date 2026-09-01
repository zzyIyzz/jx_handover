"""应用入口。

第一版原则：数据库是真相，Web 是编辑入口，AI 是整理工具，
Word 是正式输出，云盘是分发渠道。
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import config
from app.api import admin, handovers, imports, session
from app.audit import audit_requests
from app.bootstrap import initialize_application_data
from app.cloud_security import protect_cloud_requests
from app.db import engine
from app.security import initialize_session_secret
from app.services.backup import backup_status, maybe_daily_backup, pending_restore_status

APP_VERSION = config.APP_VERSION

app = FastAPI(
    title="江西片区智能交接班系统",
    version=APP_VERSION,
    docs_url=None if config.APP_MODE == "cloud" else "/docs",
    redoc_url=None if config.APP_MODE == "cloud" else "/redoc",
    openapi_url=None if config.APP_MODE == "cloud" else "/openapi.json",
)

if config.TRUSTED_HOSTS:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=config.TRUSTED_HOSTS,
    )

# Only local development needs cross-origin Vite requests.  Production cloud
# browsers are same-origin through Nginx, so no CORS trust is added there.
if config.APP_MODE != "cloud":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.middleware("http")(audit_requests)
app.middleware("http")(protect_cloud_requests)

app.include_router(session.router)
app.include_router(admin.router)
app.include_router(imports.router)
app.include_router(handovers.router)


@app.on_event("startup")
def startup() -> None:
    config.validate_runtime_configuration()
    initialize_application_data()
    initialize_session_secret()
    if config.APP_MODE in {"server", "cloud"}:
        try:
            maybe_daily_backup()
        except Exception:  # noqa: BLE001 - service must still start
            import logging
            logging.getLogger(__name__).exception("Automatic daily backup failed")


@app.get("/api/health")
def health():
    journal_mode = ""
    if engine.url.get_backend_name() == "sqlite":
        try:
            with engine.connect() as connection:
                journal_mode = str(
                    connection.execute(text("PRAGMA journal_mode")).scalar() or ""
                ).lower()
        except Exception:  # noqa: BLE001 - health still reports service state
            journal_mode = "unknown"
    public_payload = {
        "status": "ok",
        "service": "jx-handover",
        "version": APP_VERSION,
        "port": config.APP_PORT,
        "mode": config.APP_MODE,
        "auth_required": config.AUTH_REQUIRED,
    }
    if config.APP_MODE == "cloud":
        public_payload["public_port"] = config.PUBLIC_PORT
        if journal_mode == "unknown":
            return JSONResponse(
                status_code=503,
                content={**public_payload, "status": "degraded", "database": "unavailable"},
            )
        return {**public_payload, "database": "ok"}

    try:
        backups = backup_status()
    except Exception:  # noqa: BLE001 - health must remain available
        backups = {"status": "unavailable"}
    return {
        **public_payload,
        "host": config.APP_HOST,
        "public_url": config.PUBLIC_URL,
        "database_backend": engine.url.get_backend_name(),
        "database_journal_mode": journal_mode,
        "ai_mode": config.AI_MODE,
        "ai_model": config.QWEN_MODEL if config.AI_MODE == "qwen" else "mock",
        "ai_configured": bool(config.QWEN_API_KEY) if config.AI_MODE == "qwen" else True,
        "data_root": str(config.USER_DATA_ROOT),
        "backup": backups,
        "restore_pending": pending_restore_status() is not None,
    }


if config.APP_MODE == "cloud":
    @app.get("/docs", include_in_schema=False)
    @app.get("/redoc", include_in_schema=False)
    @app.get("/openapi.json", include_in_schema=False)
    def disabled_api_documentation():
        raise HTTPException(status_code=404, detail="Not found")


# 生产构建存在时直接由 FastAPI 提供前端静态文件
if config.FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST),
                               html=True), name="frontend")
