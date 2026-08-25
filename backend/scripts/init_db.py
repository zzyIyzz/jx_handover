"""初始化数据库：建表 + 插入三个种子场站（幂等）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Station, now_iso  # noqa: E402
import app.models  # noqa: E402,F401  确保所有模型注册

SEED_STATIONS = [
    ("XS_MMS", "修水眉毛山风电场", ["修水", "眉毛山", "修水眉毛山"]),
    ("WA_SK", "万安韶口光伏电站", ["万安", "韶口", "万安韶口"]),
    ("ND_ZJT", "宁都真君堂光储电站", ["宁都", "真君堂", "宁都真君堂"]),
]


def main():
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
        db.commit()
        print("数据库初始化完成。")
        for s in db.query(Station).all():
            print(f"  {s.code}  {s.name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
