"""同一事项合并：规则优先、AI 只判模糊区。

评分 = 0.45 x 设备ID匹配 + 0.30 x 文本相似度 + 0.15 x 关键词Jaccard
       + 0.10 x 时间接近度

初始阈值（工程阈值，后续用真实数据校准）：
  S >= 0.86          直接合并
  0.68 <= S < 0.86   交给 AI 判断
  S < 0.68           默认不同事项

硬规则优先级高于分数：
  不同场站 => 禁止合并（入库前已按场站分组）
  明确不同设备编号（F08 vs F13）=> 禁止合并
"""
from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

from app.models import SourceRecord, WorkItem, WorkItemUpdate
from app.services.ai.adapter import BaseAdapter, extract_equipment

MERGE_DIRECT = 0.86
MERGE_AI_MIN = 0.68
# 设备编号完全相同时，每日进展文本天然差异大、分数偏低，
# 放宽进入 AI 判断的下限，让同设备的后续跟踪记录仍能归并。
MERGE_AI_MIN_SAME_EQUIP = 0.60

_COMPLETED = ("已完成", "恢复正常", "已恢复", "已消除", "已处理完毕",
              "试运正常", "完成更换", "试送后设备恢复正常")
_IN_PROGRESS = ("未完成", "继续", "仍未", "跟踪", "处理中", "推进",
                "持续", "尚未", "待厂家", "联系")
_URGENT = ("紧急",)
_IMPORTANT = ("重点",)


def infer_status(text: str) -> str:
    # 先查未完成类，避免“未完成”中的“完成”被误判为已完成；
    # “已完成”在未完成关键词之前优先判断。
    if "已完成" in text:
        return "completed"
    if any(k in text for k in _IN_PROGRESS):
        return "in_progress"
    if any(k in text for k in _COMPLETED):
        return "completed"
    return "pending"


def infer_priority(text: str) -> str:
    if any(k in text for k in _URGENT):
        return "urgent"
    if any(k in text for k in _IMPORTANT):
        return "important"
    return "normal"


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s，。、；：,.:;（）()]", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else {t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _time_proximity(d1: str, d2: str) -> float:
    try:
        delta = abs((date.fromisoformat(d1) - date.fromisoformat(d2)).days)
    except Exception:  # noqa: BLE001
        return 0.0
    return max(0.0, 1.0 - delta / 14.0)


def score_pair(item: WorkItem, latest_text: str, latest_date: str,
               new_text: str, new_date: str) -> float | None:
    """返回分数；返回 -1 表示硬规则禁止合并。"""
    eq_item = item.canonical_key or ""
    eq_new = extract_equipment(new_text)
    if eq_item and eq_new and eq_item != eq_new:
        return -1.0  # 明确不同设备，禁止合并

    eq_score = 1.0 if (eq_item and eq_item == eq_new) else 0.0
    text_sim = fuzz.token_set_ratio(latest_text, new_text) / 100.0
    kw = _jaccard(_bigrams(latest_text), _bigrams(new_text))
    time_p = _time_proximity(latest_date or "", new_date or "")
    return 0.45 * eq_score + 0.30 * text_sim + 0.15 * kw + 0.10 * time_p


def process_records(db, station_id: int, records: list[SourceRecord],
                    ai: BaseAdapter) -> list[WorkItem]:
    """把场站时间窗内的原始记录归并为 work_items，
    返回本次被触达（有新 update）的事项列表。"""
    touched: dict[str, WorkItem] = {}
    ordered = sorted(records, key=lambda r: (r.source_date or "", r.row_no or 0))

    for rec in ordered:
        text = rec.normalized_text or rec.raw_text
        status_hint = infer_status(text)
        equip = extract_equipment(text)

        # 候选：该场站未关闭事项
        candidates = (db.query(WorkItem)
                      .filter(WorkItem.station_id == station_id,
                              WorkItem.is_closed == 0)
                      .all())

        best_item, best_score, best_same_equip = None, 0.0, False
        for item in candidates:
            latest_update = (db.query(WorkItemUpdate)
                             .filter(WorkItemUpdate.work_item_id == item.id)
                             .order_by(WorkItemUpdate.update_date.desc())
                             .first())
            latest_text = (latest_update.progress_text
                           if latest_update else item.canonical_title)
            latest_date = (latest_update.update_date
                           if latest_update else item.last_seen_date)
            s = score_pair(item, latest_text, latest_date, text, rec.source_date)
            if s is not None and s < 0:
                continue  # 硬规则禁止
            if s > best_score:
                best_item, best_score = item, s
                best_same_equip = bool(item.canonical_key)
                best_same_equip = best_same_equip and (
                    item.canonical_key == extract_equipment(text))

        ai_floor = (MERGE_AI_MIN_SAME_EQUIP if best_same_equip
                    else MERGE_AI_MIN)
        chosen = None
        if best_item is not None and best_score >= MERGE_DIRECT:
            chosen = best_item
        elif best_item is not None and best_score >= ai_floor:
            latest_update = (db.query(WorkItemUpdate)
                             .filter(WorkItemUpdate.work_item_id == best_item.id)
                             .order_by(WorkItemUpdate.update_date.desc())
                             .first())
            verdict = ai.judge_merge(
                {"text": latest_update.progress_text if latest_update
                 else best_item.canonical_title,
                 "date": best_item.last_seen_date},
                {"text": text, "date": rec.source_date},
            )
            if verdict.get("same_item"):
                chosen = best_item

        if chosen is None:
            # 新事项。标题：去除日期与标点后取核心描述
            title = _make_title(text)
            chosen = WorkItem(
                station_id=station_id,
                canonical_title=title,
                canonical_key=equip,
                status=status_hint,
                priority=infer_priority(text),
                first_seen_date=rec.source_date or "",
                last_seen_date=rec.source_date or "",
            )
            db.add(chosen)
            db.flush()

        # 追加进展（同一事项同一来源不重复）
        exists = (db.query(WorkItemUpdate.id)
                  .filter(WorkItemUpdate.work_item_id == chosen.id,
                          WorkItemUpdate.source_record_id == rec.id)
                  .first())
        if not exists:
            db.add(WorkItemUpdate(
                work_item_id=chosen.id,
                source_record_id=rec.id,
                update_date=rec.source_date or "",
                progress_text=text,
                status_hint=status_hint,
                ai_confidence=0.0,
            ))

        chosen.last_seen_date = max(chosen.last_seen_date or "",
                                    rec.source_date or "")
        chosen.status = status_hint
        if status_hint == "completed":
            chosen.is_closed = 1
        if infer_priority(text) != "normal":
            chosen.priority = infer_priority(text)
        touched[chosen.id] = chosen

    db.commit()
    return list(touched.values())


def _make_title(text: str) -> str:
    """生成规范标题：保留设备编号与核心描述，截断过长文本。"""
    t = re.sub(r"[，。；,;]\s*(已完成|完成|恢复正常).*$", "", text)
    if len(t) > 40:
        t = t[:40] + "……"
    return t
