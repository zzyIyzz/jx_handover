"""初始化数据库：建表 + 轻量迁移 + 插入种子场站与人员字典（幂等）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Staff, Station, now_iso  # noqa: E402
import app.models  # noqa: E402,F401  确保所有模型注册

SEED_STATIONS = [
    ("XS_MMS", "修水眉毛山风电场", ["修水", "眉毛山", "修水眉毛山"]),
    ("WA_SK", "万安韶口光伏电站", ["万安", "韶口", "万安韶口"]),
    ("ND_ZJT", "宁都真君堂光储电站", ["宁都", "真君堂", "宁都真君堂"]),
]

# 内置值班人员字典（station_code="REGION" 为片区通用，新增场站直接复用）
# 依据：检修片区定期工作计划人员分工 + 交接班记录署名人员。
SEED_STAFF = [
    ("REGION", "钟宇", "科技专责", "备件/台账记录"),
    ("REGION", "连喆", "综合管理人员", "综合专责"),
    ("REGION", "刘学森", "一次/营销专责", ""),
    ("REGION", "张日君", "二次专责", ""),
    ("REGION", "盛林", "风机光伏专责", "现场值守"),
    ("REGION", "敖资溪", "通讯专责", ""),
    ("REGION", "熊思奇", "带班负责人", "值班负责人"),
    ("REGION", "郭桓君", "现场值守", ""),
    ("REGION", "金宇鑫", "现场值守", ""),
    ("REGION", "刘嘉华", "现场值守", ""),
    ("REGION", "倪阳峰", "现场值守", ""),
    ("REGION", "徐诚浩", "现场值守", ""),
    ("REGION", "朱正昊炎", "现场值守", ""),
    ("REGION", "周智源", "现场值守", ""),
]


def _migrate(db):
    """轻量迁移：为旧库补新增列（SQLite ALTER TABLE ADD COLUMN）。全新库自动跳过。"""
    tables = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'"))}
    if "monthly_plan_items" not in tables:
        return  # 全新库，建表时已包含新列，无需迁移
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(monthly_plan_items)"))}
    if "library_id" not in cols:
        db.execute(text(
            "ALTER TABLE monthly_plan_items "
            "ADD COLUMN library_id TEXT NOT NULL DEFAULT ''"))
        print("已迁移：monthly_plan_items.library_id")


def main():
    db = SessionLocal()
    try:
        _migrate(db)          # 先迁移旧库（首次建库时表不存在，自动跳过）
        db.commit()
    finally:
        db.close()
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        for code, name, aliases in SEED_STATIONS:
            exists = db.query(Station).filter(Station.code == code).first()
            if not exists:
                db.add(Station(code=code, name=name,
                               aliases_json=json.dumps(aliases,
                                                       ensure_ascii=False),
                               created_at=now_iso(), updated_at=now_iso()))
        for sc, name, role, note in SEED_STAFF:
            exists = (db.query(Staff)
                      .filter(Staff.station_code == sc, Staff.name == name)
                      .first())
            if not exists:
                db.add(Staff(station_code=sc, name=name, role=role,
                             note=note, created_at=now_iso()))
        db.commit()
        print("数据库初始化完成。")
        for s in db.query(Station).all():
            print(f"  {s.code}  {s.name}")
        print(f"人员字典 {db.query(Staff).count()} 人")
    finally:
        db.close()


if __name__ == "__main__":
    main()
