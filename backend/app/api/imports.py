import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Station
from app.services.importer import sections as section_importer
from app.services.importer import xlsx as xlsx_importer

router = APIRouter(prefix="/api", tags=["imports"])

MAX_XLSX_BYTES = 50 * 1024 * 1024


class CommitPreviewReq(BaseModel):
    rows: list[dict] | None = None


async def _save_upload(file: UploadFile, directory: str) -> Path:
    """校验并保留原始文件名，便于导入记录追溯。"""
    original_name = (file.filename or "upload.xlsx").replace("/", "_").replace("\\", "_")
    if Path(original_name).suffix.lower() != ".xlsx":
        raise HTTPException(422, "仅支持 .xlsx 文件，请先从腾讯文档或 Excel 导出为 XLSX。")
    payload = await file.read()
    if not payload:
        raise HTTPException(422, "上传文件为空，请重新选择。")
    if len(payload) > MAX_XLSX_BYTES:
        raise HTTPException(413, "文件超过 50 MB，请精简后重试。")
    target = Path(directory) / original_name
    target.write_bytes(payload)
    return target


@router.get("/stations")
def list_stations(db: Session = Depends(get_db)):
    import json
    return [
        {"id": s.id, "code": s.code, "name": s.name,
         "aliases": json.loads(s.aliases_json or "[]")}
        for s in db.query(Station).filter(Station.is_active == 1).all()
    ]


@router.post("/imports/xlsx")
async def import_xlsx(
    file: UploadFile = File(...),
    default_year: int | None = Form(None),
    station_code: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """导入腾讯文档导出的班会记录 XLSX。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = await _save_upload(file, tmp_dir)
        return xlsx_importer.import_meeting_xlsx(
            db, tmp_path, default_year=default_year,
            force_station_code=station_code)


@router.post("/imports/monthly-plan")
async def import_monthly_plan(
    file: UploadFile = File(...),
    plan_month: str = Form(...),
    category: str = Form("monthly"),
    station_code: str | None = Form(None),
    default_year: int | None = Form(None),
    db: Session = Depends(get_db),
):
    """导入月度/季度定期工作计划 XLSX。"""
    if category not in {"monthly", "quarterly", "yearly"}:
        raise HTTPException(422, "计划类型必须是月度、季度或年度。")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = await _save_upload(file, tmp_dir)
        return xlsx_importer.import_monthly_plan_xlsx(
            db, tmp_path, plan_month=plan_month, category=category,
            station_code=station_code, default_year=default_year)


@router.get("/imports/handover-template")
def download_handover_template():
    if not config.STANDARD_IMPORT_TEMPLATE.exists():
        raise HTTPException(503, "标准导入模板尚未安装，请重新安装完整发布包。")
    return FileResponse(
        config.STANDARD_IMPORT_TEMPLATE,
        filename="交接班系统标准导入模板_V0.3.0.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/handovers/{batch_id}/imports/preview")
async def preview_section_import(
    batch_id: str,
    station_meta_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """解析 XLSX 并返回可编辑预览；此步骤不写入正式事项。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = await _save_upload(file, tmp_dir)
        return section_importer.create_preview(
            db, batch_id, station_meta_id, tmp_path)


@router.get("/handovers/{batch_id}/imports/{preview_id}")
def get_section_import_preview(
    batch_id: str,
    preview_id: str,
    db: Session = Depends(get_db),
):
    result = section_importer.get_preview(db, preview_id)
    if result["batch_id"] != batch_id:
        raise HTTPException(404, "导入预览不存在")
    return result


@router.post("/handovers/{batch_id}/imports/{preview_id}/commit")
def commit_section_import(
    batch_id: str,
    preview_id: str,
    req: CommitPreviewReq,
    db: Session = Depends(get_db),
):
    return section_importer.commit_preview(
        db, batch_id, preview_id, req.rows)
