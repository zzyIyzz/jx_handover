from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Station
from app.services.importer import xlsx as xlsx_importer

router = APIRouter(prefix="/api", tags=["imports"])


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
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        return xlsx_importer.import_meeting_xlsx(
            db, tmp_path, default_year=default_year,
            force_station_code=station_code)
    finally:
        tmp_path.unlink(missing_ok=True)


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
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        return xlsx_importer.import_monthly_plan_xlsx(
            db, tmp_path, plan_month=plan_month, category=category,
            station_code=station_code, default_year=default_year)
    finally:
        tmp_path.unlink(missing_ok=True)
