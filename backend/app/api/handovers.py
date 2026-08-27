from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DocumentSnapshot, Staff, Station
from app.services import handover_service as hs, periodic
from app.services.document import publish

router = APIRouter(prefix="/api", tags=["handovers"])


class CreateBatchReq(BaseModel):
    start_date: str
    end_date: str
    handover_date: str
    station_ids: list[int]
    # 可选：{station_id: {duty_leader, temp_leader, operators}}
    meta_overrides: Optional[dict] = None


class PatchItemReq(BaseModel):
    revision: int
    title_snapshot: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    summary: Optional[str] = None
    latest_progress: Optional[str] = None
    blocker: Optional[str] = None
    next_action: Optional[str] = None
    previous_owner: Optional[str] = None
    next_owner: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class ApproveReq(BaseModel):
    revision: Optional[int] = None


class PatchMetaReq(BaseModel):
    duty_leader: Optional[str] = None
    temp_leader: Optional[str] = None
    operators: Optional[list[str]] = None


class DeviceChangeReq(BaseModel):
    station_meta_id: str
    content: str


class PatchGeneralReq(BaseModel):
    revision: int
    status: Optional[str] = None
    owner: Optional[str] = None
    note: Optional[str] = None


class StaffReq(BaseModel):
    station_code: str
    name: str
    role: str
    note: Optional[str] = None


class RenderReq(BaseModel):
    station_meta_id: str


@router.get("/handovers")
def list_handovers(db: Session = Depends(get_db)):
    return hs.list_batches(db)


@router.post("/handovers")
def create_handover(req: CreateBatchReq, db: Session = Depends(get_db)):
    overrides = {}
    if req.meta_overrides:
        for k, v in req.meta_overrides.items():
            overrides[int(k)] = v
    batch = hs.create_batch(
        db, start_date=req.start_date, end_date=req.end_date,
        handover_date=req.handover_date, station_ids=req.station_ids,
        meta_overrides=overrides)
    return {"id": batch.id, "status": batch.status}


@router.get("/handovers/{batch_id}")
def get_handover(batch_id: str, db: Session = Depends(get_db)):
    return hs.batch_detail(db, batch_id)


@router.patch("/handover-items/{item_id}")
def patch_item(item_id: str, req: PatchItemReq, db: Session = Depends(get_db)):
    fields = {k: v for k, v in req.model_dump(exclude={"revision"}).items()
              if v is not None}
    return hs.patch_item(db, item_id, req.revision, fields)


@router.post("/handover-items/{item_id}/approve")
def approve_item(item_id: str, req: ApproveReq, db: Session = Depends(get_db)):
    return hs.approve_item(db, item_id, req.revision)


@router.patch("/handover-station-meta/{meta_id}")
def patch_meta(meta_id: str, req: PatchMetaReq, db: Session = Depends(get_db)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    return hs.patch_station_meta(db, meta_id, fields)


@router.post("/handovers/{batch_id}/device-changes")
def add_device_change(batch_id: str, req: DeviceChangeReq,
                      db: Session = Depends(get_db)):
    return hs.add_device_change(db, batch_id, req.station_meta_id, req.content)


# ---------- 定期工作（内置模板库） ----------

@router.get("/periodic/library")
def periodic_library(category: Optional[str] = None):
    """内置定期工作模板库（全场站通用，只读）。"""
    cats = [category] if category in periodic.CATEGORIES else list(
        periodic.CATEGORIES)
    out = []
    for c in cats:
        for it in periodic.LIBRARY[c]:
            out.append({
                "library_id": it.library_id,
                "category": it.category,
                "category_cn": periodic.CATEGORY_CN[it.category],
                "name": it.name,
                "doc_list": it.doc_list,
                "doc_dir": it.doc_dir,
                "content": it.content,
                "schedule": it.schedule,
                "owner": it.owner,
                "reviewer": it.reviewer,
                "remark": it.remark,
            })
    return {"summary": periodic.library_summary(), "items": out}


@router.patch("/general-items/{item_id}")
def patch_general_item(item_id: str, req: PatchGeneralReq,
                       db: Session = Depends(get_db)):
    fields = {k: v for k, v in req.model_dump(exclude={"revision"}).items()
              if v is not None}
    return hs.patch_general_item(db, item_id, req.revision, fields)


# ---------- 人员字典 ----------

def _staff_row(s: Staff) -> dict:
    return {"id": s.id, "station_code": s.station_code, "name": s.name,
            "role": s.role, "note": s.note, "is_active": bool(s.is_active)}


@router.get("/staff")
def list_staff(station_code: Optional[str] = None,
               db: Session = Depends(get_db)):
    """人员字典：未指定场站返回全部；指定后返回片区通用(REGION)+该场站。"""
    q = db.query(Staff).filter(Staff.is_active == 1)
    if station_code:
        q = q.filter(Staff.station_code.in_(["REGION", station_code]))
    return [_staff_row(s) for s in q.order_by(Staff.station_code,
                                              Staff.id).all()]


@router.post("/staff")
def add_staff(req: StaffReq, db: Session = Depends(get_db)):
    s = Staff(station_code=req.station_code, name=req.name, role=req.role,
              note=req.note or "", is_active=1)
    db.add(s)
    db.commit()
    return _staff_row(s)


@router.get("/work-items/{work_item_id}/sources")
def work_item_sources(work_item_id: str, db: Session = Depends(get_db)):
    return hs.item_sources(db, work_item_id)


@router.post("/handovers/{batch_id}/render")
def render(batch_id: str, req: RenderReq, db: Session = Depends(get_db)):
    return publish.render_and_snapshot(db, batch_id, req.station_meta_id)


@router.get("/documents/{snapshot_id}/download")
def download_document(snapshot_id: str, db: Session = Depends(get_db)):
    snap = db.get(DocumentSnapshot, snapshot_id)
    if snap is None:
        from fastapi import HTTPException
        raise HTTPException(404, "快照不存在")
    meta = None
    from app.models import HandoverStationMeta
    meta = db.get(HandoverStationMeta, snap.station_meta_id)
    station = db.get(Station, meta.station_id) if meta else None
    name = (f"{station.name if station else 'doc'}_V{snap.version}.docx")
    return FileResponse(snap.docx_path, filename=name,
                        media_type="application/vnd.openxmlformats-"
                                   "officedocument.wordprocessingml.document")
