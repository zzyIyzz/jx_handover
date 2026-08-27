# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.periodic import library_summary, select_for_window  # noqa: E402

OUT = os.path.join(os.environ.get("TEMP", "."), "periodic_check.txt")
lines = []
lines.append("模板库规模: " + str(library_summary()))
sel = select_for_window("2026-08-14", "2026-08-23", "2026-08-23")
for cat in ("monthly", "quarterly", "yearly"):
    rows = sel[cat]
    lines.append(f"\n=== {cat} {len(rows)} 项 ===")
    for inst in rows:
        it = inst["item"]
        lines.append(f"  {it.library_id} {it.name} | {it.schedule} -> "
                     f"[{inst['plan_start']} ~ {inst['plan_end']}] "
                     f"责任人={it.owner}")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("OK ->", OUT)
