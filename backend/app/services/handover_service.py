"""交接班核心服务：新建班次、详情聚合、人工编辑（乐观锁）、审批。

新建交接班流程：
  专业记录闭区间硬过滤 -> 规则+AI 合并产生事项 -> 本班快照
  内置定期工作模板库按周期自动筛选实例化 -> 本班定期工作（第六节）
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from app.models import (
    DeviceChange,
    DocumentSnapshot,
    HandoverBatch,
    HandoverGeneralItem,
    HandoverItem,
    HandoverStationMeta,
    MonthlyPlanItem,
    SourceRecord,
    Station,
    WorkItem,
    WorkItemUpdate,
    new_id,
    now_iso,
)
from app.services import merger, periodic, rules
from app.services.ai.adapter import get_ai

# 人工编辑允许的字段
_EDITABLE_FIELDS = (
    "title_snapshot", "status", "priority", "summary", "latest_progress",
    "blocker", "next_action", "previous_owner", "next_owner",
    "start_date", "end_date",
)

# 定期工作（通用工作）人工编辑允许的字段（匹配实际完成情况）
_EDITABLE_GENERAL_FIELDS = ("status", "owner", "note")


def create_batch(db, *, start_date: str, end_date: str, handover_date: str,
                 station_ids: list[int],
                 meta_overrides: dict[int, dict] | None = None) -> HandoverBatch:
    if start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于截止日期")
    if not (start_date <= handover_date <= end_date):
        raise HTTPException(422, "交接班日必须在交接时间窗内")

    batch = HandoverBatch(start_date=start_date, end_date=end_date,
                          handover_date=handover_date, status="review")
    db.add(batch)
    db.flush()

    ai = get_ai()
    meta_overrides = meta_overrides or {}

    for sid in station_ids:
        station = db.get(Station, sid)
        if station is None:
            raise HTTPException(422, f"场站不存在: {sid}")
        ov = meta_overrides.get(sid, {})
        meta = HandoverStationMeta(
            batch_id=batch.id,
            station_id=sid,
            duty_leader=ov.get("duty_leader", ""),
            temp_leader=ov.get("temp_leader", "无"),
            operators_json=json.dumps(ov.get("operators", []),
                                      ensure_ascii=False),
        )
        db.add(meta)
        db.flush()

        # ---- 专业工作：闭区间硬过滤 ----
        records = (db.query(SourceRecord)
                   .filter(SourceRecord.station_id == sid)
                   .order_by(SourceRecord.source_date)
                   .all())
        window_records = [r for r in records
                          if rules.record_in_window(r.source_date,
                                                    start_date, end_date)]
        touched_items = merger.process_records(db, sid, window_records, ai)

        for item in touched_items:
            updates = (db.query(WorkItemUpdate)
                       .filter(WorkItemUpdate.work_item_id == item.id,
                               WorkItemUpdate.update_date >= start_date,
                               WorkItemUpdate.update_date <= end_date)
                       .order_by(WorkItemUpdate.update_date)
                       .all())
            if not updates:
                continue
            evidence = [{"date": u.update_date, "text": u.progress_text,
                         "status_hint": u.status_hint} for u in updates]
            result = ai.summarize_cluster(item.canonical_title, evidence)
            operators = ov.get("operators", [])
            db.add(HandoverItem(
                batch_id=batch.id,
                station_meta_id=meta.id,
                work_item_id=item.id,
                title_snapshot=item.canonical_title,
                status=item.status,
                priority=item.priority,
                summary=result.get("summary", ""),
                latest_progress=result.get("latest_progress", ""),
                blocker=result.get("blocker", ""),
                next_action=result.get("next_action", ""),
                previous_owner=ov.get("duty_leader", ""),
                next_owner=operators[0] if operators else "",
                start_date=updates[0].update_date,
                end_date=(updates[-1].update_date
                          if item.status == "completed" else None),
                source_ids_json=json.dumps(
                    [u.source_record_id for u in updates if u.source_record_id]),
                ai_confidence=0.0,
            ))

        # ---- 定期工作：内置模板库按周期自动筛选实例化 ----
        # 模板有的项全部生成、没有的严禁添加；实际完成情况由已有执行记录
        # （同 library_id + plan_month 的 monthly_plan_items）携带，无记录默认未完成。
        selected = periodic.select_for_window(start_date, end_date,
                                              handover_date)
        plan_month = handover_date[:7]
        for cat in ("monthly", "quarterly", "yearly"):
            for inst in selected[cat]:
                tpl = inst["item"]
                plan = (db.query(MonthlyPlanItem)
                        .filter(MonthlyPlanItem.library_id == tpl.library_id,
                                MonthlyPlanItem.plan_month == plan_month,
                                ((MonthlyPlanItem.station_id == sid)
                                 | (MonthlyPlanItem.station_id.is_(None))))
                        .first())
                if plan is None:
                    plan = MonthlyPlanItem(
                        plan_month=plan_month,
                        scope_type="region",
                        station_id=None,
                        title=tpl.name,
                        category=cat,
                        library_id=tpl.library_id,
                        plan_start=inst["plan_start"],
                        plan_end=inst["plan_end"],
                        owner=tpl.owner,
                        status="pending",
                        notes="",
                    )
                    db.add(plan)
                    db.flush()
                db.add(HandoverGeneralItem(
                    batch_id=batch.id,
                    station_meta_id=meta.id,
                    monthly_plan_item_id=plan.id,
                    status=plan.status,
                    owner=plan.owner or "",
                    note=plan.notes,
                ))

        # ---- 设备变更：来自班次外的预设（可由 API 补充） ----

    batch.status = "review"
    db.commit()
    return batch


# ---------- 详情聚合 ----------

def batch_detail(db, batch_id: str) -> dict:
    batch = db.get(HandoverBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "交接班不存在")

    stations_out = []
    metas = (db.query(HandoverStationMeta)
             .filter(HandoverStationMeta.batch_id == batch_id).all())
    for meta in metas:
        station = db.get(Station, meta.station_id)

        items_out = []
        items = (db.query(HandoverItem)
                 .filter(HandoverItem.station_meta_id == meta.id).all())
        for it in items:
            items_out.append({
                "id": it.id,
                "work_item_id": it.work_item_id,
                "title": it.title_snapshot,
                "status": it.status,
                "priority": it.priority,
                "summary": it.summary,
                "latest_progress": it.latest_progress,
                "blocker": it.blocker,
                "next_action": it.next_action,
                "previous_owner": it.previous_owner,
                "next_owner": it.next_owner,
                "start_date": it.start_date,
                "end_date": it.end_date,
                "review_status": it.review_status,
                "human_edited": bool(it.human_edited),
                "revision": it.revision,
                "source_ids": json.loads(it.source_ids_json or "[]"),
                "color": rules.professional_color(it.priority, it.status),
                # 强制规范：第三节仅紧急/重点；其余（含已完成普通项）归第四节
                "section": ("important"
                            if it.priority in ("urgent", "important")
                            else "handover"),
            })

        general_out = {"monthly": [], "quarterly": [], "yearly": []}
        generals = (db.query(HandoverGeneralItem)
                    .filter(HandoverGeneralItem.station_meta_id == meta.id)
                    .all())
        for g in generals:
            plan = db.get(MonthlyPlanItem, g.monthly_plan_item_id)
            if plan is None:
                continue
            overdue = rules.is_overdue(g.status, plan.plan_end,
                                       batch.handover_date)
            tpl = periodic.LIBRARY_BY_ID.get(plan.library_id or "")
            row = {
                "id": g.id,
                "plan_id": plan.id,
                "library_id": plan.library_id,
                "title": plan.title,
                "category": plan.category,
                "plan_start": plan.plan_start,
                "plan_end": plan.plan_end,
                "status": g.status,
                "owner": g.owner,
                "note": g.note,
                "revision": g.revision,
                "overdue": overdue,
                "color": rules.general_color(g.status, plan.plan_end,
                                             batch.handover_date),
                "template_meta": ({
                    "schedule": tpl.schedule,
                    "doc_list": tpl.doc_list,
                    "doc_dir": tpl.doc_dir,
                    "content": tpl.content,
                    "reviewer": tpl.reviewer,
                    "remark": tpl.remark,
                } if tpl else None),
            }
            if plan.category in ("quarterly", "yearly"):
                general_out[plan.category].append(row)
            else:
                general_out["monthly"].append(row)

        devices = (db.query(DeviceChange)
                   .filter(DeviceChange.station_meta_id == meta.id).all())
        snapshots = (db.query(DocumentSnapshot)
                     .filter(DocumentSnapshot.station_meta_id == meta.id)
                     .order_by(DocumentSnapshot.version.desc()).all())

        stations_out.append({
            "station_meta_id": meta.id,
            "station_id": meta.station_id,
            "station_code": station.code if station else "",
            "station_name": station.name if station else "",
            "duty_leader": meta.duty_leader,
            "temp_leader": meta.temp_leader,
            "operators": json.loads(meta.operators_json or "[]"),
            "items": items_out,
            "general": general_out,
            "device_changes": [{"id": d.id, "content": d.content}
                               for d in devices],
            "snapshots": [{"id": s.id, "version": s.version,
                           "status": s.status, "created_at": s.created_at,
                           "docx_path": s.docx_path} for s in snapshots],
        })

    return {
        "id": batch.id,
        "start_date": batch.start_date,
        "end_date": batch.end_date,
        "handover_date": batch.handover_date,
        "status": batch.status,
        "created_at": batch.created_at,
        "stations": stations_out,
    }


def list_batches(db) -> list[dict]:
    out = []
    for b in (db.query(HandoverBatch)
              .order_by(HandoverBatch.created_at.desc()).all()):
        metas = (db.query(HandoverStationMeta)
                 .filter(HandoverStationMeta.batch_id == b.id).all())
        names = []
        for m in metas:
            st = db.get(Station, m.station_id)
            if st:
                names.append(st.name)
        item_total = (db.query(HandoverItem)
                      .filter(HandoverItem.batch_id == b.id).count())
        pending = (db.query(HandoverItem)
                   .filter(HandoverItem.batch_id == b.id,
                           HandoverItem.review_status == "pending").count())
        out.append({
            "id": b.id,
            "start_date": b.start_date,
            "end_date": b.end_date,
            "handover_date": b.handover_date,
            "status": b.status,
            "stations": names,
            "item_total": item_total,
            "pending_review": pending,
            "created_at": b.created_at,
        })
    return out


# ---------- 人工编辑（乐观锁） ----------

def patch_item(db, item_id: str, revision: int, fields: dict) -> dict:
    item = db.get(HandoverItem, item_id)
    if item is None:
        raise HTTPException(404, "事项不存在")
    if item.revision != revision:
        raise HTTPException(
            409, {"code": "REVISION_CONFLICT",
                  "message": "该事项已被其他用户修改，请刷新后重新编辑。",
                  "current_revision": item.revision})
    for key, value in fields.items():
        if key in _EDITABLE_FIELDS:
            setattr(item, key, value)
    item.review_status = "edited"
    item.human_edited = 1
    item.revision += 1
    item.updated_at = now_iso()
    db.commit()
    return {"id": item.id, "revision": item.revision,
            "review_status": item.review_status,
            "human_edited": bool(item.human_edited)}


def approve_item(db, item_id: str, revision: int | None) -> dict:
    item = db.get(HandoverItem, item_id)
    if item is None:
        raise HTTPException(404, "事项不存在")
    if revision is not None and item.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "该事项已被修改，请刷新。",
                                  "current_revision": item.revision})
    item.review_status = "approved"
    item.revision += 1
    item.updated_at = now_iso()
    db.commit()
    return {"id": item.id, "revision": item.revision,
            "review_status": item.review_status}


def review_item(db, item_id: str, revision: int, fields: dict) -> dict:
    """一次事务完成“保存修改 + 确认”，避免前端连续两次提交。"""
    item = db.get(HandoverItem, item_id)
    if item is None:
        raise HTTPException(404, "事项不存在")
    if item.revision != revision:
        raise HTTPException(
            409, {"code": "REVISION_CONFLICT",
                  "message": "该事项已被其他用户修改，请刷新后重新编辑。",
                  "current_revision": item.revision})
    edited = False
    for key, value in fields.items():
        if key in _EDITABLE_FIELDS:
            setattr(item, key, value)
            edited = True
    if edited:
        item.human_edited = 1
    item.review_status = "approved"
    item.revision += 1
    item.updated_at = now_iso()
    db.commit()
    return {"id": item.id, "revision": item.revision,
            "review_status": item.review_status,
            "human_edited": bool(item.human_edited)}


def approve_all_items(db, batch_id: str, station_meta_id: str) -> dict:
    """批量确认一个场站的全部待复核事项，单事务提交。"""
    meta = db.get(HandoverStationMeta, station_meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    pending = (db.query(HandoverItem)
               .filter(HandoverItem.batch_id == batch_id,
                       HandoverItem.station_meta_id == station_meta_id,
                       HandoverItem.review_status == "pending")
               .all())
    updated_at = now_iso()
    for item in pending:
        item.review_status = "approved"
        item.revision += 1
        item.updated_at = updated_at
    db.commit()
    return {"approved": len(pending), "station_meta_id": station_meta_id}


def patch_general_item(db, item_id: str, revision: int,
                       fields: dict) -> dict:
    """定期工作执行记录编辑（匹配实际完成情况），乐观锁。
    同步更新底层 MonthlyPlanItem，保证跨班次复用一致。
    """
    g = db.get(HandoverGeneralItem, item_id)
    if g is None:
        raise HTTPException(404, "定期工作记录不存在")
    if g.revision != revision:
        raise HTTPException(
            409, {"code": "REVISION_CONFLICT",
                  "message": "该记录已被其他用户修改，请刷新后重新编辑。",
                  "current_revision": g.revision})
    for key, value in fields.items():
        if key in _EDITABLE_GENERAL_FIELDS:
            setattr(g, key, value)
    g.revision += 1
    g.updated_at = now_iso()
    plan = db.get(MonthlyPlanItem, g.monthly_plan_item_id)
    if plan is not None:
        if "status" in fields:
            plan.status = fields["status"]
        if "owner" in fields:
            plan.owner = fields["owner"]
        if "note" in fields:
            plan.notes = fields["note"]
    db.commit()
    return {"id": g.id, "revision": g.revision, "status": g.status,
            "owner": g.owner, "note": g.note}


def patch_station_meta(db, meta_id: str, fields: dict) -> dict:
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None:
        raise HTTPException(404, "场站班次信息不存在")
    if "duty_leader" in fields:
        meta.duty_leader = fields["duty_leader"]
    if "temp_leader" in fields:
        meta.temp_leader = fields["temp_leader"]
    if "operators" in fields:
        meta.operators_json = json.dumps(fields["operators"],
                                         ensure_ascii=False)
        # 同步事项交接后责任人默认值
        ops = fields["operators"]
        if ops:
            (db.query(HandoverItem)
             .filter(HandoverItem.station_meta_id == meta_id,
                     HandoverItem.next_owner == "")
             .update({HandoverItem.next_owner: ops[0]},
                     synchronize_session=False))
    if "duty_leader" in fields and fields["duty_leader"]:
        (db.query(HandoverItem)
         .filter(HandoverItem.station_meta_id == meta_id,
                 HandoverItem.previous_owner == "")
         .update({HandoverItem.previous_owner: fields["duty_leader"]},
                 synchronize_session=False))
    meta.revision += 1
    meta.updated_at = now_iso()
    db.commit()
    return {"id": meta.id, "revision": meta.revision}


def add_device_change(db, batch_id: str, meta_id: str, content: str) -> dict:
    dc = DeviceChange(batch_id=batch_id, station_meta_id=meta_id,
                      content=content)
    db.add(dc)
    db.commit()
    return {"id": dc.id, "content": dc.content}


def item_sources(db, work_item_id: str) -> list[dict]:
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise HTTPException(404, "事项不存在")
    out = []
    updates = (db.query(WorkItemUpdate)
               .filter(WorkItemUpdate.work_item_id == work_item_id)
               .order_by(WorkItemUpdate.update_date).all())
    for u in updates:
        rec = (db.get(SourceRecord, u.source_record_id)
               if u.source_record_id else None)
        out.append({
            "date": u.update_date,
            "text": rec.raw_text if rec else u.progress_text,
            "sheet": rec.sheet_name if rec else "",
            "row_no": rec.row_no if rec else None,
            "status_hint": u.status_hint,
        })
    return out
