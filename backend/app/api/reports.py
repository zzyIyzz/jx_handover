# -*- coding: utf-8 -*-
"""月度例会材料与全年定期工作一览接口（全部只读聚合，登录后即可用）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import require_identity
from app.services import meeting_report

router = APIRouter(
    prefix="/api/reports",
    tags=["reports"],
    dependencies=[Depends(require_identity)],
)


class MemoryReq(BaseModel):
    library_ids: list[str] = []


@router.get("/monthly-meeting")
def get_monthly_meeting(month: str = Query(..., description="YYYY-MM"),
                        db: Session = Depends(get_db)):
    return meeting_report.monthly_meeting(db, month)


@router.get("/monthly-meeting/export")
def export_monthly_meeting(month: str = Query(..., description="YYYY-MM"),
                           db: Session = Depends(get_db)):
    path = meeting_report.export_monthly_meeting_xlsx(db, month)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet",
    )


@router.get("/yearly-plan")
def get_yearly_plan(year: int = Query(..., ge=2000, le=2100),
                    db: Session = Depends(get_db)):
    return meeting_report.yearly_overview(db, year)


@router.post("/periodic-memory")
def post_periodic_memory(req: MemoryReq, db: Session = Depends(get_db)):
    return meeting_report.periodic_memory(db, req.library_ids)
