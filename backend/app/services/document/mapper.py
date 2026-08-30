"""Map persisted handover data to the seven-table Word template context."""
from __future__ import annotations

import json
import re

from app.models import (
    DeviceChange,
    ExternalAssessment,
    HandoverBatch,
    HandoverGeneralItem,
    HandoverItem,
    HandoverStationMeta,
    MonthlyPlanItem,
    Station,
)
from app.services import rules


STATUS_LABELS = {
    "completed": "已完成",
    "in_progress": "进行中",
    "blocked": "受阻",
    "pending": "待启动",
    "unknown": "待确认",
}


def cn_date(iso: str | None) -> str:
    """2026-08-23 -> 2026.8.23; an empty date becomes an em dash."""
    if not iso:
        return "—"
    try:
        year, month, day = iso.split("-")
        return f"{int(year)}.{int(month)}.{int(day)}"
    except (TypeError, ValueError):
        return iso


def cn_date_short(iso: str | None) -> str:
    """2026-08-23 -> 8.23 for the narrow section 3/4 date columns."""
    if not iso:
        return "—"
    try:
        _year, month, day = iso.split("-")
        return f"{int(month)}.{int(day)}"
    except (TypeError, ValueError):
        return iso


def cn_date_md(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        _year, month, day = iso.split("-")
        return f"{int(month)}月{int(day)}日"
    except (TypeError, ValueError):
        return iso


def cn_date_d(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        _year, _month, day = iso.split("-")
        return f"{int(day)}日"
    except (TypeError, ValueError):
        return iso


def _normalized(value: str | None) -> str:
    return re.sub(r"[\s，。；、,.!！?？:：()（）\-—_]+", "", value or "").casefold()


def _remark(item: HandoverItem) -> str:
    """Keep supplemental progress while removing title/summary repetition."""
    title_key = _normalized(item.title_snapshot)
    seen = {title_key}
    parts: list[str] = []
    for value in (item.latest_progress, item.blocker, item.next_action, item.summary):
        value = (value or "").strip()
        key = _normalized(value)
        if not key or key in seen:
            continue
        if title_key and (key == title_key or key in title_key):
            continue
        seen.add(key)
        parts.append(value.rstrip("；。"))
    return "；".join(parts)


def _status_text(status: str) -> str:
    return STATUS_LABELS.get(status, "待确认")


def _periodic_sort_key(plan: MonthlyPlanItem) -> tuple:
    library_id = plan.library_id or ""
    match = re.search(r"(\d+)$", library_id)
    number = int(match.group(1)) if match else 10**9
    return (plan.plan_start or "", number, library_id, plan.title)


def build_context(db, batch: HandoverBatch, meta: HandoverStationMeta) -> dict:
    station = db.get(Station, meta.station_id)
    operators = json.loads(meta.operators_json or "[]")

    items = (
        db.query(HandoverItem)
        .filter(HandoverItem.station_meta_id == meta.id)
        .order_by(HandoverItem.section, HandoverItem.sort_order, HandoverItem.created_at)
        .all()
    )
    important: list[dict] = []
    handover: list[dict] = []
    colors_important: list[str] = []
    colors_handover: list[str] = []
    for item in items:
        base = {
            "id": item.id,
            "title": item.title_snapshot.strip(),
            "start": cn_date_short(item.start_date),
            "end": cn_date_short(item.end_date),
            "remark": _remark(item),
        }
        color = rules.professional_color(item.priority, item.status)
        if item.section == "important":
            important.append({**base, "completed_by": item.completed_by.strip()})
            colors_important.append(color)
        else:
            handover.append({
                **base,
                "previous_owner": item.previous_owner.strip(),
                "next_owner": item.next_owner.strip(),
                "status_text": _status_text(item.status),
            })
            colors_handover.append(color)

    external_rows = [
        {
            "id": row.id,
            "contractor": row.contractor.strip(),
            "work_content": row.work_content.strip(),
            "assessment": row.assessment.strip(),
            "remark": row.remark.strip(),
        }
        for row in (
            db.query(ExternalAssessment)
            .filter(ExternalAssessment.station_meta_id == meta.id)
            .order_by(ExternalAssessment.sort_order, ExternalAssessment.created_at)
            .all()
        )
    ]

    periodic: dict[str, list[tuple[MonthlyPlanItem, HandoverGeneralItem, dict, str]]] = {
        "monthly": [],
        "quarterly": [],
        "yearly": [],
    }
    general_items = (
        db.query(HandoverGeneralItem)
        .filter(HandoverGeneralItem.station_meta_id == meta.id)
        .all()
    )
    for general in general_items:
        plan = db.get(MonthlyPlanItem, general.monthly_plan_item_id)
        if plan is None:
            continue
        category = plan.category if plan.category in periodic else "monthly"
        if category == "monthly":
            start_text, end_text = "—", cn_date_d(plan.plan_end)
        else:
            start_text, end_text = cn_date_md(plan.plan_start), cn_date_md(plan.plan_end)
        row = {
            "id": general.id,
            "title": plan.title.strip(),
            "start": start_text,
            "end": end_text,
            "status_text": "已完成" if general.status == "completed" else "未完成",
            "owner": general.owner.strip(),
            "remark": general.note.strip(),
        }
        periodic[category].append((
            plan,
            general,
            row,
            rules.general_color(general.status, plan.plan_end, batch.handover_date),
        ))

    periodic_rows: dict[str, list[dict]] = {}
    periodic_colors: dict[str, list[str]] = {}
    for category, values in periodic.items():
        values.sort(key=lambda value: _periodic_sort_key(value[0]))
        periodic_rows[category] = [value[2] for value in values]
        periodic_colors[category] = [value[3] for value in values]

    devices = (
        db.query(DeviceChange)
        .filter(DeviceChange.station_meta_id == meta.id)
        .order_by(DeviceChange.created_at, DeviceChange.id)
        .all()
    )

    context = {
        "station_name": station.name if station else "",
        "period_cn": f"{cn_date(batch.start_date)}~{cn_date(batch.end_date)}",
        "start_date_cn": cn_date(batch.start_date),
        "end_date_cn": cn_date(batch.end_date),
        "handover_date_cn": cn_date(batch.handover_date),
        "duty_leader": meta.duty_leader.strip() or "—",
        "temp_leader": meta.temp_leader.strip() or "无",
        "operators": "、".join(str(name).strip() for name in operators if str(name).strip()) or "—",
        "device_changes": [device.content.strip() for device in devices if device.content.strip()],
        "important_items": important,
        "handover_items": handover,
        "external_rows": external_rows,
        "general_monthly": periodic_rows["monthly"],
        "general_quarterly": periodic_rows["quarterly"],
        "general_yearly": periodic_rows["yearly"],
    }
    return {
        "ctx": context,
        "colors": {
            "important": colors_important,
            "handover": colors_handover,
            "monthly": periodic_colors["monthly"],
            "quarterly": periodic_colors["quarterly"],
            "yearly": periodic_colors["yearly"],
        },
        "expected": {
            "important": len(important),
            "handover": len(handover),
            "external": len(external_rows),
            "monthly": len(periodic_rows["monthly"]),
            "quarterly": len(periodic_rows["quarterly"]),
            "yearly": len(periodic_rows["yearly"]),
        },
    }
