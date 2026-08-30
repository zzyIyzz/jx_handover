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
    ExternalAssessment,
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
    "start_date", "end_date", "section", "completed_by", "sort_order",
)

# 定期工作（通用工作）人工编辑允许的字段（匹配实际完成情况）
_EDITABLE_GENERAL_FIELDS = ("status", "owner", "note")

_SECTIONS = {"important", "handover"}
_STATUSES = {"pending", "in_progress", "blocked", "completed", "unknown"}
_PRIORITIES = {"urgent", "important", "normal"}


def _validate_item_fields(fields: dict) -> None:
    if "section" in fields and fields["section"] is not None and fields["section"] not in _SECTIONS:
        raise HTTPException(422, "章节必须是第三章或第四章。")
    if "status" in fields and fields["status"] not in _STATUSES:
        raise HTTPException(422, "事项状态无效。")
    if "priority" in fields and fields["priority"] not in _PRIORITIES:
        raise HTTPException(422, "优先级无效。")
    if "title_snapshot" in fields and not str(fields["title_snapshot"]).strip():
        raise HTTPException(422, "工作内容不能为空。")


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

        for item_index, item in enumerate(touched_items, start=1):
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
                section=("important" if item.status == "completed" else "handover"),
                completed_by="",
                sort_order=item_index,
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
                 .filter(HandoverItem.station_meta_id == meta.id)
                 .order_by(HandoverItem.section, HandoverItem.sort_order,
                           HandoverItem.created_at)
                 .all())
        for it in items:
            items_out.append({
                "id": it.id,
                "work_item_id": it.work_item_id,
                "title": it.title_snapshot,
                "status": it.status,
                "priority": it.priority,
                "section": it.section,
                "completed_by": it.completed_by,
                "sort_order": it.sort_order,
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
        assessments = (db.query(ExternalAssessment)
                       .filter(ExternalAssessment.station_meta_id == meta.id)
                       .order_by(ExternalAssessment.sort_order,
                                 ExternalAssessment.created_at)
                       .all())
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
            "device_changes": [{"id": d.id, "content": d.content,
                                "revision": d.revision}
                               for d in devices],
            "external_assessments": [{
                "id": row.id,
                "contractor": row.contractor,
                "work_content": row.work_content,
                "assessment": row.assessment,
                "remark": row.remark,
                "sort_order": row.sort_order,
                "revision": row.revision,
                "source_type": row.source_type,
            } for row in assessments],
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
    _validate_item_fields(fields)
    moved = fields.get("section") not in (None, item.section)
    if moved and "sort_order" not in fields:
        highest = (db.query(HandoverItem.sort_order)
                   .filter(HandoverItem.station_meta_id == item.station_meta_id,
                           HandoverItem.section == fields["section"])
                   .order_by(HandoverItem.sort_order.desc()).first())
        fields["sort_order"] = (highest[0] if highest else 0) + 10
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
    _validate_item_fields(fields)
    moved = fields.get("section") not in (None, item.section)
    if moved and "sort_order" not in fields:
        highest = (db.query(HandoverItem.sort_order)
                   .filter(HandoverItem.station_meta_id == item.station_meta_id,
                           HandoverItem.section == fields["section"])
                   .order_by(HandoverItem.sort_order.desc()).first())
        fields["sort_order"] = (highest[0] if highest else 0) + 10
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


def approve_all_items(db, batch_id: str, station_meta_id: str,
                      section: str | None = None) -> dict:
    """批量确认一个场站的全部待复核事项，单事务提交。"""
    meta = db.get(HandoverStationMeta, station_meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    query = (db.query(HandoverItem)
             .filter(HandoverItem.batch_id == batch_id,
                     HandoverItem.station_meta_id == station_meta_id,
                     HandoverItem.review_status == "pending"))
    if section is not None:
        if section not in _SECTIONS:
            raise HTTPException(422, "章节无效")
        query = query.filter(HandoverItem.section == section)
    pending = query.all()
    updated_at = now_iso()
    for item in pending:
        item.review_status = "approved"
        item.revision += 1
        item.updated_at = updated_at
    db.commit()
    return {"approved": len(pending), "station_meta_id": station_meta_id,
            "section": section}


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
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    if not content.strip():
        raise HTTPException(422, "设备变更内容不能为空")
    dc = DeviceChange(batch_id=batch_id, station_meta_id=meta_id,
                      content=content.strip())
    db.add(dc)
    db.commit()
    return {"id": dc.id, "content": dc.content, "revision": dc.revision}


def patch_device_change(db, change_id: str, revision: int, content: str) -> dict:
    row = db.get(DeviceChange, change_id)
    if row is None:
        raise HTTPException(404, "设备变更不存在")
    if row.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "设备变更已被修改，请刷新。",
                                  "current_revision": row.revision})
    if not content.strip():
        raise HTTPException(422, "设备变更内容不能为空")
    row.content = content.strip()
    row.revision += 1
    row.updated_at = now_iso()
    db.commit()
    return {"id": row.id, "content": row.content, "revision": row.revision}


def delete_device_change(db, change_id: str, revision: int) -> dict:
    row = db.get(DeviceChange, change_id)
    if row is None:
        raise HTTPException(404, "设备变更不存在")
    if row.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "设备变更已被修改，请刷新。",
                                  "current_revision": row.revision})
    db.delete(row)
    db.commit()
    return {"deleted": row.id}


def add_handover_item(db, batch_id: str, fields: dict) -> dict:
    meta_id = fields.pop("station_meta_id", "")
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    _validate_item_fields(fields)
    title = str(fields.get("title_snapshot") or "").strip()
    if not title:
        raise HTTPException(422, "工作内容不能为空")
    batch = db.get(HandoverBatch, batch_id)
    status = fields.get("status", "pending")
    priority = fields.get("priority", "normal")
    section = fields.get("section") or (
        "important" if status == "completed" else "handover"
    )
    highest = (db.query(HandoverItem.sort_order)
               .filter(HandoverItem.station_meta_id == meta_id,
                       HandoverItem.section == section)
               .order_by(HandoverItem.sort_order.desc()).first())
    sort_order = int(fields.get("sort_order") or ((highest[0] if highest else 0) + 10))
    work = WorkItem(
        station_id=meta.station_id,
        canonical_title=title,
        canonical_key="",
        status=status,
        priority=priority,
        first_seen_date=fields.get("start_date") or batch.start_date,
        last_seen_date=fields.get("end_date") or batch.handover_date,
        is_closed=1 if status == "completed" else 0,
    )
    db.add(work)
    db.flush()
    item = HandoverItem(
        batch_id=batch_id,
        station_meta_id=meta_id,
        work_item_id=work.id,
        title_snapshot=title,
        status=status,
        priority=priority,
        section=section,
        completed_by=str(fields.get("completed_by") or ""),
        sort_order=sort_order,
        summary=str(fields.get("summary") or ""),
        latest_progress=str(fields.get("latest_progress") or ""),
        blocker=str(fields.get("blocker") or ""),
        next_action=str(fields.get("next_action") or ""),
        previous_owner=str(fields.get("previous_owner") or ""),
        next_owner=str(fields.get("next_owner") or ""),
        start_date=fields.get("start_date"),
        end_date=fields.get("end_date"),
        source_ids_json="[]",
        review_status="approved",
        human_edited=1,
    )
    db.add(item)
    db.commit()
    return {"id": item.id, "work_item_id": work.id,
            "revision": item.revision, "section": item.section,
            "sort_order": item.sort_order}


def delete_handover_item(db, item_id: str, revision: int) -> dict:
    item = db.get(HandoverItem, item_id)
    if item is None:
        raise HTTPException(404, "事项不存在")
    if item.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "该事项已被修改，请刷新。",
                                  "current_revision": item.revision})
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


def reorder_handover_items(db, batch_id: str, meta_id: str,
                           section: str, ordered_ids: list[str]) -> dict:
    if section not in _SECTIONS:
        raise HTTPException(422, "章节无效")
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    rows = (db.query(HandoverItem)
            .filter(HandoverItem.station_meta_id == meta_id,
                    HandoverItem.section == section).all())
    current_ids = {row.id for row in rows}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != current_ids:
        raise HTTPException(409, "事项列表已变化，请刷新后重新排序。")
    by_id = {row.id: row for row in rows}
    updated_at = now_iso()
    for index, row_id in enumerate(ordered_ids, start=1):
        row = by_id[row_id]
        row.sort_order = index * 10
        row.revision += 1
        row.updated_at = updated_at
    db.commit()
    return {"section": section, "ordered_ids": ordered_ids}


def add_external_assessment(db, batch_id: str, fields: dict) -> dict:
    meta_id = fields.get("station_meta_id", "")
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    if not str(fields.get("work_content") or "").strip():
        raise HTTPException(422, "工作内容不能为空")
    highest = (db.query(ExternalAssessment.sort_order)
               .filter(ExternalAssessment.station_meta_id == meta_id)
               .order_by(ExternalAssessment.sort_order.desc()).first())
    row = ExternalAssessment(
        batch_id=batch_id,
        station_meta_id=meta_id,
        contractor=str(fields.get("contractor") or "").strip(),
        work_content=str(fields.get("work_content") or "").strip(),
        assessment=str(fields.get("assessment") or "").strip(),
        remark=str(fields.get("remark") or "").strip(),
        sort_order=int(fields.get("sort_order") or ((highest[0] if highest else 0) + 10)),
        source_type=str(fields.get("source_type") or "manual"),
        source_json=json.dumps(fields.get("source") or {}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "revision": row.revision,
            "sort_order": row.sort_order}


def patch_external_assessment(db, row_id: str, revision: int,
                              fields: dict) -> dict:
    row = db.get(ExternalAssessment, row_id)
    if row is None:
        raise HTTPException(404, "外委考核记录不存在")
    if row.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "外委考核已被修改，请刷新。",
                                  "current_revision": row.revision})
    allowed = {"contractor", "work_content", "assessment", "remark", "sort_order"}
    for key, value in fields.items():
        if key in allowed:
            setattr(row, key, value.strip() if isinstance(value, str) else value)
    if not row.work_content:
        raise HTTPException(422, "工作内容不能为空")
    row.revision += 1
    row.updated_at = now_iso()
    db.commit()
    return {"id": row.id, "revision": row.revision,
            "sort_order": row.sort_order}


def delete_external_assessment(db, row_id: str, revision: int) -> dict:
    row = db.get(ExternalAssessment, row_id)
    if row is None:
        raise HTTPException(404, "外委考核记录不存在")
    if row.revision != revision:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": "外委考核已被修改，请刷新。",
                                  "current_revision": row.revision})
    db.delete(row)
    db.commit()
    return {"deleted": row_id}


def reorder_external_assessments(db, batch_id: str, meta_id: str,
                                 ordered_ids: list[str]) -> dict:
    meta = db.get(HandoverStationMeta, meta_id)
    if meta is None or meta.batch_id != batch_id:
        raise HTTPException(404, "交接班或场站信息不存在")
    rows = (db.query(ExternalAssessment)
            .filter(ExternalAssessment.station_meta_id == meta_id).all())
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != {r.id for r in rows}:
        raise HTTPException(409, "外委考核列表已变化，请刷新后重新排序。")
    by_id = {row.id: row for row in rows}
    updated_at = now_iso()
    for index, row_id in enumerate(ordered_ids, start=1):
        row = by_id[row_id]
        row.sort_order = index * 10
        row.revision += 1
        row.updated_at = updated_at
    db.commit()
    return {"ordered_ids": ordered_ids}


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
