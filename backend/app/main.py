"""应用入口。

第一版原则：数据库是真相，Web 是编辑入口，AI 是整理工具，
Word 是正式输出，云盘是分发渠道。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import handovers, imports

app = FastAPI(title="江西片区智能交接班系统", version="0.1.0")

# 开发期允许 Vite dev server 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(imports.router)
app.include_router(handovers.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_mode": config.AI_MODE}


# 生产构建存在时直接由 FastAPI 提供前端静态文件
if config.FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST),
                               html=True), name="frontend")
