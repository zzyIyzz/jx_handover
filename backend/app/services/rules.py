"""业务硬规则：日期窗、超期判断、颜色规则全部由程序完成，
绝不交给 AI 或人工排版决定。"""
from __future__ import annotations

# ---------- 颜色 ----------
COLOR_RED = "red"        # 紧急 / 超期未完成
COLOR_YELLOW = "yellow"  # 重点
COLOR_GREEN = "green"    # 定期工作已完成
COLOR_WHITE = "white"    # 普通 / 期限内未完成


def professional_color(priority: str, status: str) -> str:
    """专业工作颜色：紧急=红（红底白字）、重点=黄（黄底黑字）、其余=白。
    字体颜色由 renderer 按同规则处理。"""
    if priority == "urgent":
        return COLOR_RED
    if priority == "important":
        return COLOR_YELLOW
    return COLOR_WHITE


def is_overdue(status: str, plan_end: str | None, handover_date: str) -> bool:
    """超期唯一规则：未完成 且 截止日 < 交接班日（ISO 字符串可直接比较）。
    注意不是 plan_end < 今天。"""
    return status != "completed" and bool(plan_end) and plan_end < handover_date


def general_color(status: str, plan_end: str | None, handover_date: str) -> str:
    """定期工作颜色：已完成=绿、期限内未完成=白、超期未完成=红。"""
    if status == "completed":
        return COLOR_GREEN
    if is_overdue(status, plan_end, handover_date):
        return COLOR_RED
    return COLOR_WHITE


def plan_in_window(plan_start: str | None, plan_end: str | None,
                   win_start: str, win_end: str) -> bool:
    """月度/季度计划采用区间相交规则：
    plan_start <= 交接截止 AND plan_end >= 交接开始。"""
    if plan_end and plan_end < win_start:
        return False
    if plan_start and plan_start > win_end:
        return False
    return True


def record_in_window(source_date: str | None, win_start: str, win_end: str) -> bool:
    """专业记录采用闭区间硬过滤：[开始日, 截止日] 两端都包含。"""
    if not source_date:
        return False
    return win_start <= source_date <= win_end


# ---------- 排序 ----------

_PRIORITY_ORDER = {"urgent": 0, "important": 1, "normal": 2}


def sort_incomplete_first(rows: list[dict], colors: list[str],
                          priorities: list[str] | None = None
                          ) -> tuple[list[dict], list[str]]:
    """强制排序：先未完成（超期优先、紧急>重点），后已完成；
    行数据、行颜色（及可选优先级）同步重排。"""
    n = len(rows)
    idx = list(range(n))

    def completed(i: int) -> int:
        return 0 if rows[i].get("status_text") == "未完成" else 1

    def overdue(i: int) -> int:
        return 0 if colors[i] == "red" else 1

    def prio(i: int) -> int:
        if priorities is None:
            return 0
        return _PRIORITY_ORDER.get(priorities[i], 2)

    idx.sort(key=lambda i: (completed(i), overdue(i), prio(i)))
    new_rows = [rows[i] for i in idx]
    new_colors = [colors[i] for i in idx]
    new_prios = [priorities[i] for i in idx] if priorities else None
    return new_rows, new_colors, new_prios  # type: ignore[return-value]
