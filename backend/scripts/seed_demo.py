"""种子数据：把修水眉毛山 2026.8.14~8.23 班次的真实内容造入数据库，
保证没有真实班会 XLSX 时也能端到端验收。

覆盖验收要点：
- #1SVG 跨三日记录（8.20/8.22/8.23）必须合并为一个事项
- F08 与 F13 变频器网侧接地文字相似但设备不同，必须保持两个事项
- 月计划含"8.28 截止未完成"项（8.23 交接不得判超期，白色）
- 月计划含"8.22 截止未完成"项（8.23 交接必须判超期，红色）
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

# ---- 月度定期工作计划（2026-08，修水）----
# (标题, 开始, 结束, 完成人, 状态, 备注, 类别)
PLAN_ITEMS = [
    ("OMS月报及预测电量提交", None, "2026-08-01", "熊思奇", "已完成", "", "monthly"),
    ("登机次数统计", None, "2026-08-04", "", "已完成",
     "统计专责督察人员自行填写登机次数并审核", "monthly"),
    ("避雷器月度检查表（纸质版）", None, "2026-08-06", "", "已完成", "", "monthly"),
    ("电缆沟月度检查表（纸质版）", None, "2026-08-06", "", "已完成", "", "monthly"),
    ("SF6压力月度检查表（纸质版）", None, "2026-08-06", "", "已完成", "", "monthly"),
    ("设备测温月度检查表（纸质版）", None, "2026-08-06", "", "已完成", "", "monthly"),
    ("消防设施月度检查表（纸质版）", None, "2026-08-06", "", "已完成",
     "包含正压式呼吸器", "monthly"),
    ("直流系统切换月度试验表（纸质版）", None, "2026-08-10", "", "已完成",
     "", "monthly"),
    ("UPS切换月度试验表（纸质版）", None, "2026-08-10", "", "已完成", "", "monthly"),
    ("提交当月采购计划", None, "2026-08-10", "熊思奇", "已完成", "", "monthly"),
    ("安全工器具月度检查表（纸质版）", None, "2026-08-14", "熊思奇",
     "已完成", "", "monthly"),
    ("应急物资检查（纸电版）", None, "2026-08-14", "熊思奇", "已完成",
     "包括应急药品、防爆物资、消防物资、防汛物资", "monthly"),
    ("专项清洁：空调滤网/气象装置/生产区地面/SVG/蓄电池月度清扫",
     None, "2026-08-18", "熊思奇", "已完成", "", "monthly"),
    ("事故预想", None, "2026-08-18", "熊思奇", "已完成", "生产管理系统",
     "monthly"),
    ("考问讲解", None, "2026-08-18", "熊思奇", "已完成", "生产管理系统",
     "monthly"),
    ("箱变月度巡检表（纸质版）", "2026-08-01", "2026-08-31", "", "未完成",
     "", "monthly"),
    ("风机月度巡检表（纸电版）", "2026-08-01", "2026-08-31", "", "未完成",
     "", "monthly"),
    ("叶片月度巡检表（纸电版）", "2026-08-01", "2026-08-31", "", "未完成",
     "", "monthly"),
    # 验收 F13：8.28 截止未完成，在 8.23 交接时不得判超期（白色）
    ("升压站接地引下线改造", "2026-08-20", "2026-08-28", "盛林", "未完成",
     "演示：截止8.28，交接日8.23不应判超期", "monthly"),
    # 验收 F14：8.22 截止未完成，8.23 交接必须判超期（红色）
    ("消防沙池更换", "2026-08-10", "2026-08-22", "", "未完成",
     "演示：截止8.22，交接日8.23应判超期", "monthly"),
    # 区间不相交：不应进入本班
    ("9月安全日活动策划", "2026-09-01", "2026-09-15", "", "未完成",
     "不应出现在8.14~8.23班次", "monthly"),
    # 季度定期工作
    ("输电线路巡视（纸质版）", "2026-07-01", "2026-09-30", "钟宇", "未完成",
     "钟宇完成I线，熊思奇完成II线III线巡视", "quarterly"),
    ("风机数据备份季度记录表（纸质版）", "2026-07-01", "2026-09-30", "",
     "未完成", "", "quarterly"),
    ("工控备份台账（电子版）", "2026-07-01", "2026-09-30", "", "未完成",
     "", "quarterly"),
    ("测风塔季度检查表（纸质版）", "2026-07-01", "2026-09-30", "", "未完成",
     "", "quarterly"),
    ("设备密码季度检查表（纸质版）", "2026-04-01", "2026-06-30", "", "已完成",
     "上季度已完成，不应进入本班", "quarterly"),
]

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

        for title, p_start, p_end, owner, status, notes, category in PLAN_ITEMS:
            db.add(MonthlyPlanItem(
                plan_month="2026-08",
                scope_type="station",
                station_id=station.id,
                title=title,
                category=category,
                plan_start=p_start,
                plan_end=p_end,
                owner=owner,
                status="completed" if status == "已完成" else "pending",
                notes=notes,
            ))

        db.commit()
        print("种子数据写入完成：")
        print(f"  班会记录 {len(MEETING_RECORDS)} 条（窗口内）"
              f" + {len(OUT_OF_WINDOW_RECORDS)} 条（窗口外，验收用）")
        print(f"  月度/季度计划 {len(PLAN_ITEMS)} 条")
        print("  设备变更请在新建交接班后通过界面或接口添加：")
        for d in DEVICE_CHANGES:
            print(f"    - {d}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
