# -*- coding: utf-8 -*-
"""内置定期工作模板库核心：全场站通用的六大类模板 + 周期自动筛选。

规则（全部由程序计算）：
  1. 模板中有的项目全部生成，没有的严禁添加；
  2. 月度：交接班日所在月的全部月度项（时间 "N日" 解析为当月截止日）；
  3. 季度：执行月份清单包含交接班日的月份，窗口为该月 1 日~月末；
  4. 年度：执行月/月区间与交接窗口相交即入选（跨年区间按两段处理）；
  5. 日/周/专项：保留在库中供追溯，不参与第六节月度/季度/年度小节筛选。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from app.services.periodic._daily_weekly import DAILY, WEEKLY
from app.services.periodic._monthly import MONTHLY
from app.services.periodic._quarterly_yearly import QUARTERLY, YEARLY
from app.services.periodic._special import SPECIAL

CATEGORIES = ("daily", "weekly", "monthly", "quarterly", "yearly", "special")
CATEGORY_CN = {
    "daily": "日", "weekly": "周", "monthly": "月度",
    "quarterly": "季度", "yearly": "年度", "special": "专项",
}


@dataclass(frozen=True)
class TemplateItem:
    library_id: str
    category: str
    name: str
    doc_list: str        # 资料清单
    doc_dir: str         # 留存目录
    content: str         # 工作内容、要求及标准
    schedule: str        # 模板原始时间
    owner: str           # 责任人
    reviewer: str        # 审核人
    remark: str          # 备注


def _build(category: str, rows: list[tuple]) -> list[TemplateItem]:
    return [TemplateItem(r[0], category, r[1], r[2], r[3], r[4], r[5],
                         r[6], r[7], r[8]) for r in rows]


LIBRARY: dict[str, list[TemplateItem]] = {
    "daily": _build("daily", DAILY),
    "weekly": _build("weekly", WEEKLY),
    "monthly": _build("monthly", MONTHLY),
    "quarterly": _build("quarterly", QUARTERLY),
    "yearly": _build("yearly", YEARLY),
    "special": _build("special", SPECIAL),
}

LIBRARY_BY_ID: dict[str, TemplateItem] = {
    it.library_id: it
    for items in LIBRARY.values() for it in items
}


def library_summary() -> dict:
    return {c: len(v) for c, v in LIBRARY.items()}


# ---------- 时间解析 ----------

def _month_end(year: int, month: int) -> str:
    return f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _iso(year: int, month: int, day: int) -> str:
    day = min(day, calendar.monthrange(year, month)[1])
    return f"{year}-{month:02d}-{day:02d}"


def _parse_days(schedule: str) -> list[int] | None:
    """'6日' / '20日/30日' / '28日/29日' -> [6] / [20, 30]。"""
    days = []
    for part in schedule.replace("（根据电网短息）", "").split("/"):
        part = part.strip()
        if part.endswith("日") and part[:-1].isdigit():
            days.append(int(part[:-1]))
    return days or None


def _parse_months(text: str) -> list[int]:
    """'2、4、6、8、10、12月' -> [2,4,6,8,10,12]"""
    months = []
    for part in text.replace("、", ",").replace("月", ",").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 12:
            months.append(int(part))
    return months


def _yearly_windows(schedule: str, year: int) -> list[tuple[str, str]]:
    """解析年度项时间，返回 [(开始, 结束)] 列表；无固定时间返回 []。"""
    s = schedule.strip()
    if "月至来年" in s or "月至次年" in s:
        start_m = int(s.split("月")[0])
        end_m = int(s.replace("至来年", ",").replace("至次年", ",")
                    .split(",")[1].replace("月", ""))
        return [(f"{year}-{start_m:02d}-01", _month_end(year, 12)),
                (f"{year + 1}-01-01", _month_end(year + 1, end_m))]
    if "月至" in s:
        a, b = s.replace("月", "").split("至")
        m1, m2 = int(a), int(b)
        return [(f"{year}-{m1:02d}-01", _month_end(year, m2))]
    if s == "1-12月":
        return [(f"{year}-01-01", _month_end(year, 12))]
    months = _parse_months(s)
    if months:
        return [(f"{year}-{m:02d}-01", _month_end(year, m)) for m in months]
    return []  # 一年2次 / 参照计划执行 等：无固定窗口


def select_for_window(win_start: str, win_end: str, handover_date: str
                      ) -> dict[str, list[dict]]:
    """按交接窗口筛选各周期应执行的模板项，返回带完整元数据的实例清单。

    每个实例：{item: TemplateItem, plan_start, plan_end}
    """
    ws, we = date.fromisoformat(win_start), date.fromisoformat(win_end)
    hd = date.fromisoformat(handover_date)
    out: dict[str, list[dict]] = {"monthly": [], "quarterly": [],
                                  "yearly": []}

    # 月度：交接班日所在月（窗口跨月时逐月展开）
    cur = date(hd.year, hd.month, 1)
    months = []
    while cur <= we:
        months.append((cur.year, cur.month))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    if not months:
        months = [(hd.year, hd.month)]
    for item in LIBRARY["monthly"]:
        days = _parse_days(item.schedule)
        for (y, m) in months:
            if days:
                plan_end = _iso(y, m, max(days))
            else:  # 以通知为准 / 若有：无固定截止
                plan_end = None
            out["monthly"].append({"item": item, "plan_start": None,
                                   "plan_end": plan_end})

    # 季度：执行月份清单包含交接班日月份，窗口为该自然月
    for item in LIBRARY["quarterly"]:
        if hd.month in _parse_months(item.schedule):
            out["quarterly"].append({
                "item": item,
                "plan_start": f"{hd.year}-{hd.month:02d}-01",
                "plan_end": _month_end(hd.year, hd.month),
            })

    # 年度：执行月/区间与交接窗口相交
    for year in (ws.year, we.year):
        for item in LIBRARY["yearly"]:
            windows = _yearly_windows(item.schedule, year)
            hit = [w for w in windows if w[0] <= win_end and w[1] >= win_start]
            if hit:
                out["yearly"].append({
                    "item": item,
                    "plan_start": min(w[0] for w in hit),
                    "plan_end": max(w[1] for w in hit),
                })
            elif not windows and item.schedule not in ("据实开展", "必要时开展"):
                # 一年2次 / 参照计划执行：全年开放、无固定截止
                out["yearly"].append({"item": item, "plan_start": None,
                                      "plan_end": None})
    # 跨年去重（无窗口项会重复）
    seen, uniq = set(), []
    for inst in out["yearly"]:
        key = inst["item"].library_id
        if key not in seen:
            seen.add(key)
            uniq.append(inst)
    out["yearly"] = uniq
    return out
