"""数据库 -> Word 模板上下文映射。

Word 六节结构与数据源对应：
  一、基本信息       <- handover_station_meta
  二、设备变更情况   <- device_changes
  三、重点工作完成情况 <- 紧急/重点专业事项 + 已完成事项
  四、需交接的工作   <- 普通且未完成专业事项（不与第三节重复）
  五、对外委单位的考核 <- 第一版留空行
  六、定期工作完成情况 <- handover_general_items（月度/季度分开）
"""
from __future__ import annotations

import json

from app.models import (
    DeviceChange,
    HandoverBatch,
    HandoverGeneralItem,
    HandoverItem,
    HandoverStationMeta,
    MonthlyPlanItem,
    Station,
)
from app.services import rules


def cn_date(iso: str | None) -> str:
    """2026-08-23 -> 2026.8.23；空值 -> —"""
    if not iso:
        return "—"
    try:
        y, m, d = iso.split("-")
        return f"{int(y)}.{int(m)}.{int(d)}"
    except Exception:  # noqa: BLE001
        return iso


def _status_text(status: str) -> str:
    return "已完成" if status == "completed" else "未完成"


def build_context(db, batch: HandoverBatch, meta: HandoverStationMeta) -> dict:
    station = db.get(Station, meta.station_id)
    operators = json.loads(meta.operators_json or "[]")

    items = (db.query(HandoverItem)
             .filter(HandoverItem.station_meta_id == meta.id).all())

    # 第三节：紧急/重点 或 已完成；第四节：普通且未完成（不重复）
    important, handover_list = [], []
    colors_important, colors_handover = [], []
    for it in items:
        row_base = {
            "title": it.title_snapshot,
            "start": cn_date(it.start_date),
            "end": cn_date(it.end_date),
        }
        if it.priority in ("urgent", "important") or it.status == "completed":
            remark_parts = [p for p in
                            (it.summary, it.latest_progress, it.blocker) if p]
            important.append({
                **row_base,
                "owner": it.previous_owner or it.next_owner,
                "remark": it.latest_progress or it.summary,
            })
            colors_important.append(
                rules.professional_color(it.priority, it.status))
        else:
            handover_list.append({
                **row_base,
                "prev_owner": it.previous_owner,
                "next_owner": it.next_owner,
                "status_text": _status_text(it.status),
                "remark": it.latest_progress or it.summary,
            })
            colors_handover.append(
                rules.professional_color(it.priority, it.status))

    # 第六节：定期工作，程序计算颜色（含超期判断）
    monthly, quarterly = [], []
    colors_monthly, colors_quarterly = [], []
    generals = (db.query(HandoverGeneralItem)
                .filter(HandoverGeneralItem.station_meta_id == meta.id).all())
    for g in generals:
        plan = db.get(MonthlyPlanItem, g.monthly_plan_item_id)
        if plan is None:
            continue
        row = {
            "title": plan.title,
            "start": cn_date(plan.plan_start),
            "end": cn_date(plan.plan_end),
            "status_text": _status_text(g.status),
            "owner": g.owner,
            "remark": g.note,
        }
        color = rules.general_color(g.status, plan.plan_end,
                                    batch.handover_date)
        if plan.category == "quarterly":
            quarterly.append(row)
            colors_quarterly.append(color)
        else:
            monthly.append(row)
            colors_monthly.append(color)

    devices = (db.query(DeviceChange)
               .filter(DeviceChange.station_meta_id == meta.id).all())

    return {
        "ctx": {
            "station_name": station.name if station else "",
            "period_cn": f"{cn_date(batch.start_date)}~{cn_date(batch.end_date)}",
            "start_date_cn": cn_date(batch.start_date),
            "end_date_cn": cn_date(batch.end_date),
            "handover_date_cn": cn_date(batch.handover_date),
            "duty_leader": meta.duty_leader or "—",
            "temp_leader": meta.temp_leader or "无",
            "operators": "、".join(operators) if operators else "—",
            "device_changes": [d.content for d in devices]
            if devices else ["本班无设备变更"],
            "important_items": important,
            "handover_items": handover_list,
            "external_rows": [{"no": i} for i in (1, 2, 3)],
            "general_monthly": monthly,
            "general_quarterly": quarterly,
        },
        # 各表数据行的颜色（与模板表格顺序对应，渲染后按行号着色）
        "colors": {
            "important": colors_important,
            "handover": colors_handover,
            "monthly": colors_monthly,
            "quarterly": colors_quarterly,
        },
    }
