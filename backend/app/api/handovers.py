from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DocumentSnapshot, Station
from app.services import handover_service as hs
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
