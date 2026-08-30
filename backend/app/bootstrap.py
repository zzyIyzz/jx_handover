"""Idempotent application bootstrap used by both source and packaged builds."""
from __future__ import annotations

import json

from app.db import SessionLocal
from app.migrations import initialize_database
from app.models import Staff, Station, now_iso


SEED_STATIONS = [
    ("XS_MMS", "修水眉毛山风电场", ["修水", "眉毛山", "修水眉毛山"]),
    ("WA_SK", "万安韶口光伏电站", ["万安", "韶口", "万安韶口"]),
    ("ND_ZJT", "宁都真君堂光储电站", ["宁都", "真君堂", "宁都真君堂"]),
]

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


def initialize_application_data() -> dict:
    migration = initialize_database()
    created_stations = 0
    created_staff = 0
    db = SessionLocal()
    try:
        for code, name, aliases in SEED_STATIONS:
            exists = db.query(Station).filter(Station.code == code).first()
            if exists is None:
                db.add(Station(
                    code=code,
                    name=name,
                    aliases_json=json.dumps(aliases, ensure_ascii=False),
                    created_at=now_iso(),
                    updated_at=now_iso(),
                ))
                created_stations += 1
        for station_code, name, role, note in SEED_STAFF:
            exists = (db.query(Staff)
                      .filter(Staff.station_code == station_code, Staff.name == name)
                      .first())
            if exists is None:
                db.add(Staff(
                    station_code=station_code,
                    name=name,
                    role=role,
                    note=note,
                    created_at=now_iso(),
                ))
                created_staff += 1
        db.commit()
    finally:
        db.close()
    return {
        "migration": migration,
        "created_stations": created_stations,
        "created_staff": created_staff,
    }

