$ErrorActionPreference = "Stop"

# 使用脚本所在目录，避免中文路径编码问题
Set-Location $PSScriptRoot

# 首次运行若未初始化数据库，自动初始化
& ".\.venv\Scripts\python.exe" "backend\scripts\init_db.py"

# 正式启动（生产模式，前端构建产物由 FastAPI 托管）
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app `
    --app-dir backend `
    --host 127.0.0.1 `
    --port 8080
