"""数据库 -> Word 模板上下文映射。

Word 六节结构与数据源对应（强制规范，对齐参考 docx）：
  一、基本信息       <- handover_station_meta
  二、设备变更情况   <- device_changes
  三、重点工作完成情况 <- 仅紧急/重点级别工作；先未完成后已完成
  四、需交接的工作   <- 第三、六节之外的未完成/进行中工作；先未完成后已完成
  五、对外委单位的考核 <- 固定五列表头 + 占位行
  六、定期工作完成情况 <- 内置模板库实例（6.1 月度 / 6.2 季度 / 6.3 年度）
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


def cn_date_md(iso: str | None) -> str:
    """2026-08-31 -> 8月31日（季度/年度定期工作用）；空值 -> —"""
    if not iso:
        return "—"
    try:
        _y, m, d = iso.split("-")
        return f"{int(m)}月{int(d)}日"
    except Exception:  # noqa: BLE001
        return iso


def cn_date_d(iso: str | None) -> str:
    """2026-08-01 -> 1日（月度定期工作截止用）；空值 -> —"""
    if not iso:
        return "—"
    try:
        _y, _m, d = iso.split("-")
        return f"{int(d)}日"
    except Exception:  # noqa: BLE001
        return iso


def _status_text(status: str) -> str:
    return "已完成" if status == "completed" else "未完成"


def build_context(db, batch: HandoverBatch, meta: HandoverStationMeta) -> dict:
    station = db.get(Station, meta.station_id)
    operators = json.loads(meta.operators_json or "[]")

    items = (db.query(HandoverItem)
             .filter(HandoverItem.station_meta_id == meta.id).all())

    # 第三节：仅紧急/重点；第四节：其余全部（含已完成普通项），两节互不重复
    important, handover_list = [], []
    colors_important, colors_handover = [], []
    prios_important, prios_handover = [], []
    for it in items:
        row_base = {
            "title": it.title_snapshot,
            "start": cn_date(it.start_date),
            "end": cn_date(it.end_date),
            "status_text": _status_text(it.status),
        }
        color = rules.professional_color(it.priority, it.status)
        if it.priority in ("urgent", "important"):
            important.append({
                **row_base,
                "owner": it.previous_owner or it.next_owner,
                "remark": it.latest_progress or it.summary,
            })
            colors_important.append(color)
            prios_important.append(it.priority)
        else:
            handover_list.append({
                **row_base,
                "prev_owner": it.previous_owner,
                "next_owner": it.next_owner,
                "remark": it.latest_progress or it.summary,
            })
            colors_handover.append(color)
            prios_handover.append(it.priority)

    # 强制排序：先未完成（超期红优先），后已完成
    important, colors_important, _ = rules.sort_incomplete_first(
        important, colors_important, prios_important)
    handover_list, colors_handover, _ = rules.sort_incomplete_first(
        handover_list, colors_handover, prios_handover)

    # 第六节：定期工作（内置模板库），按 6.1 月度 / 6.2 季度 / 6.3 年度分小节
    sections = {"monthly": ([], []), "quarterly": ([], []), "yearly": ([], [])}
    generals = (db.query(HandoverGeneralItem)
                .filter(HandoverGeneralItem.station_meta_id == meta.id).all())
    for g in generals:
        plan = db.get(MonthlyPlanItem, g.monthly_plan_item_id)
        if plan is None:
            continue
        cat = plan.category if plan.category in sections else "monthly"
        if cat == "monthly":
            start_text, end_text = "—", cn_date_d(plan.plan_end)
        else:
            start_text, end_text = (cn_date_md(plan.plan_start),
                                    cn_date_md(plan.plan_end))
        row = {
            "title": plan.title,
            "start": start_text,
            "end": end_text,
            "status_text": _status_text(g.status),
            "owner": g.owner,
            "remark": g.note,
        }
        color = rules.general_color(g.status, plan.plan_end,
                                    batch.handover_date)
        sections[cat][0].append(row)
        sections[cat][1].append(color)

    monthly, colors_monthly = rules.sort_incomplete_first(
        *sections["monthly"])[:2]
    quarterly, colors_quarterly = rules.sort_incomplete_first(
        *sections["quarterly"])[:2]
    yearly, colors_yearly = rules.sort_incomplete_first(
        *sections["yearly"])[:2]

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
            if devices else [],
            "important_items": important,
            "handover_items": handover_list,
            # 第五节：即使无考核内容也保留五列表头 + 占位行
            "external_rows": [{"no": i} for i in (1, 2, 3)],
            "general_monthly": monthly,
            "general_quarterly": quarterly,
            "general_yearly": yearly,
        },
        # 各表数据行的颜色（与模板表格顺序对应，渲染后按行号着色）
        "colors": {
            "important": colors_important,
            "handover": colors_handover,
            "monthly": colors_monthly,
            "quarterly": colors_quarterly,
            "yearly": colors_yearly,
        },
    }
