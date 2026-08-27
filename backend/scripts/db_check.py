# -*- coding: utf-8 -*-
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.models import MonthlyPlanItem, HandoverBatch, HandoverGeneralItem

OUT = os.path.join(os.environ.get("TEMP", "."), "db_check.txt")
db = SessionLocal()
lines = []
plans = db.query(MonthlyPlanItem).all()
lines.append(f"MonthlyPlanItem 总数 {len(plans)}")
from collections import Counter
lines.append("status 分布: " + str(Counter(p.status for p in plans)))
lines.append("library_id 为空数量: "
             + str(sum(1 for p in plans if not p.library_id)))
for p in plans[:8]:
    lines.append(f"  {p.library_id!r} | {p.plan_month} | {p.status} | "
                 f"{p.title} | end={p.plan_end}")
batches = db.query(HandoverBatch).all()
lines.append(f"\n班次 {len(batches)} 个:")
for b in batches:
    n = db.query(HandoverGeneralItem).filter(
        HandoverGeneralItem.batch_id == b.id).count()
    lines.append(f"  {b.id} {b.start_date}~{b.end_date} general={n}")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("OK ->", OUT)
