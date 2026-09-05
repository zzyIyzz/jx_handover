"""Idempotent application bootstrap used by both source and packaged builds."""
from __future__ import annotations

import json

from app.db import SessionLocal
from app.migrations import initialize_database
from app.models import Staff, Station, now_iso
from app.security import initialize_missing_staff_passwords, validate_account_directory


SEED_STATIONS = [
    ("XS_MMS", "修水眉毛山风电场", ["修水", "眉毛山", "修水眉毛山"]),
    ("WA_SK", "万安韶口光伏电站", ["万安", "韶口", "万安韶口"]),
    ("ND_ZJT", "宁都真君堂光储电站", ["宁都", "真君堂", "宁都真君堂"]),
]

# 人员名单以工作群成员为准（不含两个电站账号）；不再维护岗位角色。
SEED_STAFF = [
    ("REGION", "刘学森", "", ""),
    ("REGION", "敖资溪", "", ""),
    ("REGION", "曹浩", "", ""),
    ("REGION", "郭桓君", "", ""),
    ("REGION", "金宇鑫", "", ""),
    ("REGION", "连喆", "", ""),
    ("REGION", "刘嘉华", "", ""),
    ("REGION", "倪阳峰", "", ""),
    ("REGION", "潘和雨", "", ""),
    ("REGION", "盛林", "", ""),
    ("REGION", "熊思奇", "", ""),
    ("REGION", "徐诚浩", "", ""),
    ("REGION", "易子安", "", ""),
    ("REGION", "张日君", "", ""),
    ("REGION", "钟宇", "", ""),
    ("REGION", "周智源", "", ""),
    ("REGION", "朱正昊炎", "", ""),
]


def initialize_application_data() -> dict:
    migration = initialize_database()
    created_stations = 0
    created_staff = 0
    updated_staff = 0
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
            elif exists.role != role or exists.note != note:
                exists.role = role
                exists.note = note
                updated_staff += 1
        db.commit()
        validate_account_directory(db)
        initialized_accounts = initialize_missing_staff_passwords(db)
    finally:
        db.close()
    return {
        "migration": migration,
        "created_stations": created_stations,
        "created_staff": created_staff,
        "updated_staff": updated_staff,
        "initialized_accounts": initialized_accounts,
    }

