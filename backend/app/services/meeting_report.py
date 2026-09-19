# -*- coding: utf-8 -*-
"""月度例会材料与全年定期工作一览。

设计原则（与全站一致）：
- 只读聚合，全部数据来自既有表，不新增写入路径，不增加服务器负担；
- 按月/按年各一次 SQL 聚合，单 Uvicorn worker + SQLite WAL 下毫秒级完成；
- 导出 Excel 在请求时即时生成到 generated 目录，生成后即可下载。
"""
from __future__ import annotations

import calendar
import os
from pathlib import Path
import threading
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import config
from app.models import (
    HandoverBatch,
    HandoverGeneralItem,
    HandoverItem,
    HandoverStationMeta,
    MonthlyPlanItem,
    Station,
)
from app.services import periodic


_EXPORT_LOCK = threading.Lock()

# ---------- 展示映射 ----------

MEETING_STATUS_LABEL = {
    "completed": "已完成",
    "in_progress": "正在开展",
    "blocked": "未开展（受阻）",
    "pending": "未开展",
    "unknown": "待确认",
}
PRIORITY_LABEL = {"urgent": "紧急", "important": "重点", "normal": "普通"}
CATEGORY_LABEL = {"monthly": "月度定期", "quarterly": "季度定期", "yearly": "年度定期"}

# 会议材料分组顺序：已完成 → 正在开展 → 未开展（含受阻）
STATUS_GROUP_ORDER = ("completed", "in_progress", "pending")
STATUS_GROUP_LABEL = {
    "completed": "一、当月已完成的工作",
    "in_progress": "二、正在开展的工作",
    "pending": "三、当月未开展 / 需交接的工作",
}


def _month_bounds(month: str) -> tuple[str, str]:
    try:
        year, mon = (int(part) for part in month.split("-"))
        last = calendar.monthrange(year, mon)[1]
    except (ValueError, TypeError):
        raise HTTPException(422, "月份格式应为 YYYY-MM，例如 2026-09。")
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def _meeting_group(status: str) -> str:
    if status == "completed":
        return "completed"
    if status == "in_progress":
        return "in_progress"
    return "pending"  # pending / blocked / unknown 都归入“未开展”


def _batches_in_month(db: Session, month_start: str, month_end: str
                      ) -> list[HandoverBatch]:
    return (
        db.query(HandoverBatch)
        .filter(HandoverBatch.status == "published",
                HandoverBatch.start_date <= month_end,
                HandoverBatch.end_date >= month_start)
        .order_by(HandoverBatch.handover_date)
        .all()
    )


def monthly_meeting(db: Session, month: str) -> dict:
    """聚合当月所有班次的三、四、六章内容为例会材料表格。"""
    month_start, month_end = _month_bounds(month)
    batches = _batches_in_month(db, month_start, month_end)
    batch_ids = [b.id for b in batches]

    station_by_meta: dict[str, str] = {}
    if batch_ids:
        metas = (db.query(HandoverStationMeta)
                 .filter(HandoverStationMeta.batch_id.in_(batch_ids)).all())
        station_ids = {m.station_id for m in metas}
        stations = {s.id: s.name for s in
                    db.query(Station).filter(Station.id.in_(station_ids)).all()}
        for m in metas:
            station_by_meta[m.id] = stations.get(m.station_id, "")

    rows: list[dict] = []

    # 第三、第四章专业事项
    if batch_ids:
        items = (db.query(HandoverItem)
                 .filter(HandoverItem.batch_id.in_(batch_ids))
                 .order_by(HandoverItem.sort_order, HandoverItem.created_at)
                 .all())
        for it in items:
            owner = (it.completed_by if it.section == "important"
                     else it.next_owner or it.previous_owner)
            rows.append({
                "kind": "important" if it.section == "important" else "handover",
                "kind_label": "重点工作" if it.section == "important" else "交接工作",
                "title": it.title_snapshot,
                "station": station_by_meta.get(it.station_meta_id, ""),
                "status": it.status,
                "status_label": MEETING_STATUS_LABEL.get(it.status, it.status),
                "group": _meeting_group(it.status),
                "priority": it.priority,
                "priority_label": PRIORITY_LABEL.get(it.priority, it.priority),
                "owner": owner,
                "progress": it.latest_progress or it.summary,
                "next_action": it.next_action or it.blocker,
            })

    # 第六章定期工作执行记录
    if batch_ids:
        generals = (db.query(HandoverGeneralItem)
                    .filter(HandoverGeneralItem.batch_id.in_(batch_ids)).all())
        plan_ids = {g.monthly_plan_item_id for g in generals}
        plans = {p.id: p for p in db.query(MonthlyPlanItem)
                 .filter(MonthlyPlanItem.id.in_(plan_ids)).all()} if plan_ids else {}
        for g in generals:
            plan = plans.get(g.monthly_plan_item_id)
            if plan is None:
                continue
            overdue = bool(plan.plan_end and plan.plan_end < month_end
                           and g.status != "completed")
            rows.append({
                "kind": "periodic",
                "kind_label": CATEGORY_LABEL.get(plan.category, "定期工作"),
                "title": plan.title,
                "station": station_by_meta.get(g.station_meta_id, ""),
                "status": g.status,
                "status_label": "已完成" if g.status == "completed" else "未开展",
                "group": "completed" if g.status == "completed" else "pending",
                "priority": "important" if overdue else "normal",
                "priority_label": "重点（超期）" if overdue else "普通",
                "owner": g.owner or plan.owner,
                "progress": g.note,
                "next_action": "",
            })

    # 当月应开展但任何班次都没有记录的年度定期工作（未开展兜底提示）
    window = periodic.select_for_window(month_start, month_end, month_start)
    recorded_yearly = {
        r["title"] for r in rows
        if r["kind"] == "periodic" and r["kind_label"] == "年度定期"
    }
    for inst in window["yearly"]:
        item: periodic.TemplateItem = inst["item"]
        if item.name in recorded_yearly:
            continue
        rows.append({
            "kind": "periodic",
            "kind_label": "年度定期",
            "title": item.name,
            "station": "片区",
            "status": "pending",
            "status_label": "未开展",
            "group": "pending",
            "priority": "normal",
            "priority_label": "普通",
            "owner": item.owner,
            "progress": "",
            "next_action": f"计划时间：{item.schedule}",
        })

    groups = []
    for key in STATUS_GROUP_ORDER:
        members = [r for r in rows if r["group"] == key]
        # 组内按轻重缓急排序：紧急 > 重点 > 普通
        rank = {"urgent": 0, "important": 1, "normal": 2}
        members.sort(key=lambda r: (rank.get(r["priority"], 2), r["station"],
                                    r["title"]))
        groups.append({"key": key, "label": STATUS_GROUP_LABEL[key],
                       "count": len(members), "rows": members})

    return {
        "month": month,
        "month_label": f"{int(month[:4])} 年 {int(month[5:7])} 月",
        "batch_count": len(batches),
        "summary": {
            "completed": sum(g["count"] for g in groups if g["key"] == "completed"),
            "in_progress": sum(g["count"] for g in groups if g["key"] == "in_progress"),
            "pending": sum(g["count"] for g in groups if g["key"] == "pending"),
            "urgent": sum(1 for r in rows if r["priority"] == "urgent"),
        },
        "groups": groups,
    }


# ---------- 全年定期工作一览（含“记忆”） ----------

def _latest_general(db: Session, plan_ids: list[str]) -> dict[str, HandoverGeneralItem]:
    """每个计划条目最近一次执行记录（按更新时间）。"""
    if not plan_ids:
        return {}
    latest: dict[str, HandoverGeneralItem] = {}
    records = (db.query(HandoverGeneralItem)
               .filter(HandoverGeneralItem.monthly_plan_item_id.in_(plan_ids))
               .all())
    for rec in records:
        current = latest.get(rec.monthly_plan_item_id)
        if current is None or (rec.updated_at, rec.id) > (current.updated_at, current.id):
            latest[rec.monthly_plan_item_id] = rec
    return latest


def yearly_overview(db: Session, year: int) -> dict:
    """全年 33 项年度定期工作：本年开展情况 + 最近一次（含往年）记录。

    “记忆”：去年或更早填过的完成状态、完成人和备注会带出来，
    录入本年情况时可直接参考，不必翻历史交接班。
    """
    if year < 2000 or year > 2100:
        raise HTTPException(422, "年份格式不正确。")
    prefix = f"{year}-"
    plans = (db.query(MonthlyPlanItem)
             .filter(MonthlyPlanItem.category == "yearly",
                     MonthlyPlanItem.plan_month.like(f"{prefix}%"))
             .all())
    this_year_by_library: dict[str, list[MonthlyPlanItem]] = {}
    for p in plans:
        if p.library_id:
            this_year_by_library.setdefault(p.library_id, []).append(p)

    history = (db.query(MonthlyPlanItem)
               .filter(MonthlyPlanItem.category == "yearly",
                       MonthlyPlanItem.library_id != "",
                       MonthlyPlanItem.plan_month < f"{prefix}01")
               .all())
    history_ids = [p.id for p in history]
    history_latest = _latest_general(db, history_ids)
    plan_by_id = {p.id: p for p in history}
    # 每个模板项最近一次历史记录
    memory_by_library: dict[str, HandoverGeneralItem] = {}
    for plan_id, rec in history_latest.items():
        plan = plan_by_id[plan_id]
        current = memory_by_library.get(plan.library_id)
        if current is None or (rec.updated_at, rec.id) > (current.updated_at, current.id):
            memory_by_library[plan.library_id] = rec

    this_year_ids = [p.id for ps in this_year_by_library.values() for p in ps]
    this_year_latest = _latest_general(db, this_year_ids)

    items = []
    done = active = 0
    for tpl in periodic.LIBRARY["yearly"]:
        year_plans = this_year_by_library.get(tpl.library_id, [])
        records = [this_year_latest[p.id] for p in year_plans
                   if p.id in this_year_latest]
        completed = [r for r in records if r.status == "completed"]
        if completed:
            state, state_label = "completed", "已开展"
            done += 1
        elif records:
            state, state_label = "in_progress", "正在开展"
            active += 1
        else:
            state, state_label = "pending", "未开展"
        latest_rec = max(records, key=lambda r: (r.updated_at, r.id),
                         default=None)
        memory = memory_by_library.get(tpl.library_id)
        windows = periodic._yearly_windows(tpl.schedule, year)
        items.append({
            "library_id": tpl.library_id,
            "name": tpl.name,
            "schedule": tpl.schedule,
            "owner": tpl.owner,
            "reviewer": tpl.reviewer,
            "doc_list": tpl.doc_list,
            "plan_windows": windows,
            "state": state,
            "state_label": state_label,
            "times_recorded": len(records),
            "latest": ({
                "status_label": "已完成" if latest_rec.status == "completed" else "未完成",
                "owner": latest_rec.owner,
                "note": latest_rec.note,
                "updated_at": latest_rec.updated_at,
            } if latest_rec else None),
            "memory": ({
                "status_label": "已完成" if memory.status == "completed" else "未完成",
                "owner": memory.owner,
                "note": memory.note,
                "updated_at": memory.updated_at,
            } if memory else None),
        })

    return {
        "year": year,
        "summary": {"total": len(items), "completed": done,
                    "in_progress": active,
                    "pending": len(items) - done - active},
        "items": items,
    }


def periodic_memory(
    db: Session,
    library_ids: list[str],
    *,
    before_year: int | None = None,
) -> dict:
    """6.3 录入时带出本年度之前最近一次填写的内容。"""
    ids = [lid for lid in library_ids if lid in periodic.LIBRARY_BY_ID][:200]
    if not ids:
        return {"memory": {}}
    query = db.query(MonthlyPlanItem).filter(MonthlyPlanItem.library_id.in_(ids))
    if before_year is not None:
        query = query.filter(MonthlyPlanItem.plan_month < f"{before_year:04d}-01")
    plans = query.all()
    latest = _latest_general(db, [p.id for p in plans])
    plan_by_id = {p.id: p for p in plans}
    out: dict[str, dict] = {}
    for plan_id, rec in latest.items():
        plan = plan_by_id[plan_id]
        current = out.get(plan.library_id)
        if current and (current["updated_at"], "") >= (rec.updated_at, rec.id):
            continue
        out[plan.library_id] = {
            "status_label": "已完成" if rec.status == "completed" else "未完成",
            "owner": rec.owner,
            "note": rec.note,
            "updated_at": rec.updated_at,
            "plan_month": plan.plan_month,
        }
    return {"memory": out}


# ---------- Excel 导出 ----------

def export_monthly_meeting_xlsx(db: Session, month: str) -> Path:
    """生成月度例会材料 Excel，返回文件路径（保存在 generated 目录）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    data = monthly_meeting(db, month)
    wb = Workbook()
    ws = wb.active
    ws.title = f"{month}例会材料"

    title_font = Font(name="微软雅黑", size=16, bold=True)
    head_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    group_font = Font(name="微软雅黑", size=11, bold=True)
    body_font = Font(name="微软雅黑", size=10)
    thin = Side(style="thin", color="B0B7C3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1D4F88")
    group_fill = PatternFill("solid", fgColor="DCE6F2")
    priority_fill = {
        "紧急": PatternFill("solid", fgColor="F8CBCB"),
        "重点": PatternFill("solid", fgColor="FFF2B8"),
        "重点（超期）": PatternFill("solid", fgColor="FFF2B8"),
    }
    status_fill = {
        "已完成": PatternFill("solid", fgColor="DDEEDD"),
        "正在开展": PatternFill("solid", fgColor="DDEBF7"),
        "未开展": PatternFill("solid", fgColor="F2F2F2"),
        "未开展（受阻）": PatternFill("solid", fgColor="F8CBCB"),
    }
    wrap = Alignment(vertical="center", wrap_text=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    columns = ["序号", "类别", "工作内容", "场站", "轻重缓急", "开展状态",
               "责任人", "进展 / 备注", "下一步 / 计划"]
    widths = [6, 10, 46, 12, 10, 12, 12, 30, 26]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    cell = ws.cell(row=1, column=1,
                   value=f"江西片区检修中心 {data['month_label']} 月度例会工作材料")
    cell.font = title_font
    cell.alignment = center
    ws.row_dimensions[1].height = 32

    s = data["summary"]
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
    info = ws.cell(row=2, column=1,
                   value=f"当月交接班 {data['batch_count']} 个班次；已完成 {s['completed']} 项，"
                         f"正在开展 {s['in_progress']} 项，未开展 {s['pending']} 项"
                         f"（其中紧急 {s['urgent']} 项）。"
                         "轻重缓急：紧急=红，重点=黄，普通=白。")
    info.font = body_font
    info.alignment = wrap
    ws.row_dimensions[2].height = 22

    row_no = 3
    seq = 0
    for group in data["groups"]:
        ws.merge_cells(start_row=row_no, start_column=1,
                       end_row=row_no, end_column=len(columns))
        gcell = ws.cell(row=row_no, column=1,
                        value=f"{group['label']}（{group['count']} 项）")
        gcell.font = group_font
        gcell.fill = group_fill
        gcell.alignment = wrap
        row_no += 1
        for col, name in enumerate(columns, start=1):
            hcell = ws.cell(row=row_no, column=col, value=name)
            hcell.font = head_font
            hcell.fill = head_fill
            hcell.alignment = center
            hcell.border = border
        row_no += 1
        for item in group["rows"]:
            seq += 1
            values = [seq, item["kind_label"], item["title"], item["station"],
                      item["priority_label"], item["status_label"], item["owner"],
                      item["progress"], item["next_action"]]
            for col, value in enumerate(values, start=1):
                bcell = ws.cell(row=row_no, column=col, value=value)
                bcell.font = body_font
                bcell.border = border
                bcell.alignment = center if col in (1, 2, 4, 5, 6, 7) else wrap
                if col == 5:
                    fill = priority_fill.get(str(value))
                    if fill:
                        bcell.fill = fill
                if col == 6:
                    fill = status_fill.get(str(value))
                    if fill:
                        bcell.fill = fill
            row_no += 1

    out_dir = Path(config.GENERATED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"月度例会工作材料_{month}.xlsx"
    temporary = out_dir / f".meeting-{uuid.uuid4().hex}.xlsx.tmp"
    try:
        with _EXPORT_LOCK:
            wb.save(temporary)
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
