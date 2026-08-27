"""种子数据：把修水眉毛山 2026.8.14~8.23 班次的真实内容造入数据库，
保证没有真实班会 XLSX 时也能端到端验收。

覆盖验收要点：
- #1SVG 跨三日记录（8.20/8.22/8.23）必须合并为一个事项
- F08 与 F13 变频器网侧接地文字相似但设备不同，必须保持两个事项
- 定期工作严格按内置模板库生成，种子只预置“实际完成状态”：
  截止日≤交接日(8.23)的月度项默认已完成（绿）；
  m21 上网电费结算保持未完成→超期（红）；
  截止日晚于 8.23 的保持未完成→期限内（白）。
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ImportJob,
    MonthlyPlanItem,
    SourceRecord,
    Station,
    new_id,
    now_iso,
)
from app.services import periodic  # noqa: E402

HANDOVER_DATE = "2026-08-23"
# 演示：已完成但未填超期红的额外项（library_id -> 备注）
EXTRA_COMPLETED = {
    "q1": "钟宇完成I线，熊思奇完成II线III线巡视",
}
# 演示：截止日已过但保持未完成 -> 超期红（上网电费结算，截止 8.16）
KEEP_OVERDUE = {"m21"}

# ---- 修水班会记录（节选自真实交接班材料）----
# (日期, 内容)
MEETING_RECORDS = [
    ("2026-08-15", "F10风机变桨润滑系统油泵堵塞，清理后试运正常，已完成"),
    ("2026-08-15", "修水功率预测文件上传集控失败，重新配置上传通道后恢复正常，已完成"),
    ("2026-08-16", "水泵房目视化整改，已完成"),
    ("2026-08-16", "修水中控室AGC问题，联系厂家远程排查，继续跟踪处理"),
    ("2026-08-18", "视频监控掉线、风机消防掉线问题，联系维护单位处理中，继续跟踪"),
    ("2026-08-19", "F05风机机舱罩维修，厂家完成更换，已完成"),
    ("2026-08-20", "#1SVG故障处理，更换功率单元，故障仍未消除"),
    ("2026-08-20", "F05风机齿轮箱内窥镜检查及取油样，已完成"),
    ("2026-08-20", "主变取油样送检，已完成"),
    ("2026-08-20", "F13风机变频器网侧接地，测量箱变到变频器绝缘电阻无异常，"
                   "按厂家建议退出箱变低压侧断路器接地保护，试送后设备恢复正常"),
    ("2026-08-21", "集电Ⅲ线路杆塔树障清理，继续联系巡检中心激光清障仪邮寄事项"),
    ("2026-08-22", "#1SVG继续处理，更换交换功率单元板后仍存在故障"),
    ("2026-08-22", "F08风机变频器网侧接地，测量箱变到变频器绝缘电阻无异常，"
                   "按厂家建议退出箱变低压侧断路器接地保护，试送后设备恢复正常"),
    ("2026-08-23", "#1SVG故障处理过程已发送行云群，要求钟宇进行台账记录，设备仍未恢复"),
    ("2026-08-23", "排水沟三峡能源环保隐患问题整改，制定整改方案，未完成，截止9月30日"),
    ("2026-08-23", "F02、F06、F11、F12、F13、F15共6台风机齿轮箱油温及温控阀"
                   "异常预警工单处理，远景计划月度安排处理，未完成"),
    ("2026-08-23", "升压站马路牙子刷漆，黄黑油漆在危废间、稀释剂在大厅，未完成"),
    ("2026-08-23", "修水五防系统处理，已与厂家沟通，安徽集控五防系统升级未携带"
                   "江西片区，监控后台设备状态无法与五防系统保持一致，未完成"),
    ("2026-08-23", "实训平台比赛，剩余刘嘉华未完成接线，继续组织"),
]

# 窗口外记录：验收 F11——不得进入 8.14~8.23 本班
OUT_OF_WINDOW_RECORDS = [
    ("2026-08-13", "F03风机半年检预安排，讨论工器具准备"),
    ("2026-08-25", "（未来记录）#1SVG厂家计划更换PWM板"),
]

# ---- 定期工作：种子只预置“实际完成状态”，项目清单由内置模板库生成 ----

def seed_periodic_status(db):
    """按 8.14~8.23 窗口筛选模板项，预置带 library_id 的执行记录；
    create_batch 会幂等复用这些记录（同 library_id + plan_month）。"""
    selected = periodic.select_for_window("2026-08-14", "2026-08-23",
                                          HANDOVER_DATE)
    total = done = 0
    for cat in ("monthly", "quarterly", "yearly"):
        for inst in selected[cat]:
            tpl = inst["item"]
            plan_end = inst["plan_end"]
            completed = False
            if tpl.library_id in EXTRA_COMPLETED:
                completed = True
            elif tpl.library_id not in KEEP_OVERDUE and plan_end \
                    and plan_end <= HANDOVER_DATE:
                completed = True
            db.add(MonthlyPlanItem(
                plan_month=HANDOVER_DATE[:7],
                scope_type="region",
                station_id=None,
                title=tpl.name,
                category=cat,
                library_id=tpl.library_id,
                plan_start=inst["plan_start"],
                plan_end=plan_end,
                owner=tpl.owner,
                status="completed" if completed else "pending",
                notes=EXTRA_COMPLETED.get(tpl.library_id, ""),
            ))
            total += 1
            done += completed
    return total, done


DEVICE_CHANGES = [
    "#1SVG为检修状态",
    "F08风机、F13风机箱变接地保护已退出",
]


def _hash(source_date, station_id, normalized):
    return hashlib.sha256(
        f"{source_date}|{station_id}|{normalized}".encode("utf-8")).hexdigest()


def main():
    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.code == "XS_MMS").first()
        if station is None:
            print("请先运行 init_db.py")
            return

        existing = (db.query(SourceRecord)
                    .filter(SourceRecord.station_id == station.id).count())
        if existing:
            print(f"修水已有 {existing} 条原始记录，跳过种子数据。")
            return

        job = ImportJob(id=new_id("imp"), source_type="manual",
                        file_name="seed_demo", status="success",
                        finished_at=now_iso())
        db.add(job)
        db.flush()

        rows = MEETING_RECORDS + OUT_OF_WINDOW_RECORDS
        for i, (d, text) in enumerate(rows, start=2):
            normalized = text.replace(" ", "")
            db.add(SourceRecord(
                import_job_id=job.id,
                source_type="manual",
                source_date=d,
                station_id=station.id,
                sheet_name="种子数据",
                row_no=i,
                raw_text=text,
                normalized_text=normalized,
                raw_json=json.dumps({"seed": True}, ensure_ascii=False),
                content_hash=_hash(d, station.id, normalized),
            ))

        total, done = seed_periodic_status(db)

        db.commit()
        print("种子数据写入完成：")
        print(f"  班会记录 {len(MEETING_RECORDS)} 条（窗口内）"
              f" + {len(OUT_OF_WINDOW_RECORDS)} 条（窗口外，验收用）")
        print(f"  定期工作执行记录 {total} 条（模板库筛选），"
              f"其中已完成 {done} 条")
        print("  设备变更请在新建交接班后通过界面或接口添加：")
        for d in DEVICE_CHANGES:
            print(f"    - {d}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
