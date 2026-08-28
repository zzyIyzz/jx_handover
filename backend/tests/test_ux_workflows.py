"""V0.2 高频操作回归测试：导入幂等、日期异常、快捷复核。"""
from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.db import Base
from app.models import (
    HandoverBatch,
    HandoverItem,
    HandoverStationMeta,
    Station,
    WorkItem,
)
from app.services import handover_service
from app.services.importer import xlsx


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.station = Station(
            code="TEST", name="测试场站", aliases_json='["测试"]')
        self.db.add(self.station)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _make_batch_items(self, count: int = 2):
        batch = HandoverBatch(
            start_date="2026-08-20", end_date="2026-08-28",
            handover_date="2026-08-28", status="review")
        self.db.add(batch)
        self.db.flush()
        meta = HandoverStationMeta(
            batch_id=batch.id, station_id=self.station.id,
            duty_leader="甲", temp_leader="无", operators_json='["乙"]')
        self.db.add(meta)
        self.db.flush()
        items = []
        for index in range(count):
            work = WorkItem(
                station_id=self.station.id,
                canonical_title=f"事项 {index + 1}",
                canonical_key=f"T{index + 1}", status="in_progress",
                priority="normal", first_seen_date="2026-08-20",
                last_seen_date="2026-08-28")
            self.db.add(work)
            self.db.flush()
            item = HandoverItem(
                batch_id=batch.id, station_meta_id=meta.id,
                work_item_id=work.id, title_snapshot=work.canonical_title,
                status="in_progress", priority="normal",
                start_date="2026-08-20")
            self.db.add(item)
            items.append(item)
        self.db.commit()
        return batch, meta, items

    def test_review_item_saves_and_approves_in_one_revision(self):
        _, _, items = self._make_batch_items(1)
        result = handover_service.review_item(
            self.db, items[0].id, items[0].revision,
            {"title_snapshot": "修改后的事项", "next_owner": "丙"})
        self.assertEqual(result["review_status"], "approved")
        self.assertEqual(result["revision"], 2)
        self.assertTrue(result["human_edited"])
        self.db.refresh(items[0])
        self.assertEqual(items[0].title_snapshot, "修改后的事项")
        self.assertEqual(items[0].next_owner, "丙")

    def test_approve_all_is_single_station_operation(self):
        batch, meta, items = self._make_batch_items(3)
        items[2].review_status = "approved"
        self.db.commit()
        result = handover_service.approve_all_items(
            self.db, batch.id, meta.id)
        self.assertEqual(result["approved"], 2)
        self.assertTrue(all(item.review_status == "approved" for item in items))

    def test_meeting_import_is_idempotent_and_reports_bad_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook_path = root / "班会记录.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "测试场站"
            sheet.append(["日期", "工作内容"])
            sheet.append(["8.20", "正常记录"])
            sheet.append(["8.40", "日期异常记录"])
            book.save(workbook_path)

            old_import_dir = config.IMPORT_DIR
            config.IMPORT_DIR = root
            try:
                first = xlsx.import_meeting_xlsx(
                    self.db, workbook_path, default_year=2026,
                    force_station_code="TEST")
                second = xlsx.import_meeting_xlsx(
                    self.db, workbook_path, default_year=2026,
                    force_station_code="TEST")
            finally:
                config.IMPORT_DIR = old_import_dir

            self.assertEqual(first["inserted"], 2)
            self.assertEqual(len(first["date_unresolved"]), 1)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["skipped_duplicate"], 2)

    def test_plan_import_skips_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook_path = root / "月度计划.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "月度计划"
            sheet.append(["工作内容", "开始日期", "截止日期", "负责人"])
            sheet.append(["检查消防器材", "8.1", "8.28", "甲"])
            book.save(workbook_path)

            old_import_dir = config.IMPORT_DIR
            config.IMPORT_DIR = root
            try:
                first = xlsx.import_monthly_plan_xlsx(
                    self.db, workbook_path, plan_month="2026-08",
                    default_year=2026)
                second = xlsx.import_monthly_plan_xlsx(
                    self.db, workbook_path, plan_month="2026-08",
                    default_year=2026)
            finally:
                config.IMPORT_DIR = old_import_dir

            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["skipped_duplicate"], 1)


if __name__ == "__main__":
    unittest.main()
