"""数据模型。日期字段统一 ISO 8601 字符串存储（2026-08-23），
"8.23"、"8月23日" 等中文格式只允许出现在展示层。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Station(Base):
    __tablename__ = "stations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    aliases_json = Column(Text, nullable=False, default="[]")
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id = Column(Text, primary_key=True, default=lambda: new_id("imp"))
    # tencent_xlsx | monthly_plan_xlsx | manual
    source_type = Column(Text, nullable=False)
    file_name = Column(Text)
    file_sha256 = Column(Text)
    status = Column(Text, nullable=False, default="running")  # running|success|failed
    row_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    started_at = Column(Text, nullable=False, default=now_iso)
    finished_at = Column(Text)


class SourceRecord(Base):
    """原始班会记录，RAW 层，永不删除，AI 结果必须能追溯到这一层。"""

    __tablename__ = "source_records"
    __table_args__ = (
        Index("idx_source_records_date_station", "source_date", "station_id"),
        Index("idx_source_records_hash", "content_hash"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("src"))
    import_job_id = Column(Text, ForeignKey("import_jobs.id"))
    source_type = Column(Text, nullable=False, default="tencent_xlsx")
    source_date = Column(Text)  # ISO 8601
    station_id = Column(Integer, ForeignKey("stations.id"))
    sheet_name = Column(Text)
    row_no = Column(Integer)
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=False)
    raw_json = Column(Text, nullable=False, default="{}")
    content_hash = Column(Text, nullable=False)
    captured_at = Column(Text, nullable=False, default=now_iso)


class HandoverBatch(Base):
    __tablename__ = "handover_batches"
    __table_args__ = (Index("idx_handover_batches_dates", "start_date", "end_date"),)

    id = Column(Text, primary_key=True, default=lambda: new_id("hb"))
    start_date = Column(Text, nullable=False)
    end_date = Column(Text, nullable=False)
    handover_date = Column(Text, nullable=False)
    # draft|analyzing|review|ready|published|archived
    status = Column(Text, nullable=False, default="draft")
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class HandoverStationMeta(Base):
    __tablename__ = "handover_station_meta"
    __table_args__ = (UniqueConstraint("batch_id", "station_id"),)

    id = Column(Text, primary_key=True, default=lambda: new_id("meta"))
    batch_id = Column(
        Text, ForeignKey("handover_batches.id", ondelete="CASCADE"), nullable=False
    )
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    duty_leader = Column(Text, nullable=False, default="")
    temp_leader = Column(Text, nullable=False, default="无")
    operators_json = Column(Text, nullable=False, default="[]")
    rotation_note = Column(Text, nullable=False, default="")
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class WorkItem(Base):
    """FACT 层：跨班次持续存在的事项主体。"""

    __tablename__ = "work_items"
    __table_args__ = (
        Index("idx_work_items_station_status", "station_id", "status"),
        Index("idx_work_items_key", "station_id", "canonical_key"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("wi"))
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    canonical_title = Column(Text, nullable=False)
    domain = Column(Text, nullable=False, default="其他")
    # 设备编号等合并键，如 #1SVG / F08
    canonical_key = Column(Text, nullable=False, default="")
    # pending|in_progress|blocked|completed|unknown
    status = Column(Text, nullable=False, default="unknown")
    # urgent|important|normal
    priority = Column(Text, nullable=False, default="normal")
    first_seen_date = Column(Text, nullable=False)
    last_seen_date = Column(Text, nullable=False)
    is_closed = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class WorkItemUpdate(Base):
    __tablename__ = "work_item_updates"
    __table_args__ = (
        UniqueConstraint("work_item_id", "source_record_id"),
        Index("idx_work_updates_date", "work_item_id", "update_date"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("wiu"))
    work_item_id = Column(
        Text, ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False
    )
    source_record_id = Column(Text, ForeignKey("source_records.id"))
    update_date = Column(Text, nullable=False)
    action_text = Column(Text, nullable=False, default="")
    progress_text = Column(Text, nullable=False, default="")
    status_hint = Column(Text, nullable=False, default="unknown")
    next_action_text = Column(Text, nullable=False, default="")
    ai_confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(Text, nullable=False, default=now_iso)


class HandoverItem(Base):
    """HANDOVER 本班快照层：专业事项在本班的最终版本。"""

    __tablename__ = "handover_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "work_item_id"),
        Index("idx_handover_items_batch_review", "batch_id", "review_status"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("hi"))
    batch_id = Column(
        Text, ForeignKey("handover_batches.id", ondelete="CASCADE"), nullable=False
    )
    station_meta_id = Column(
        Text, ForeignKey("handover_station_meta.id", ondelete="CASCADE"), nullable=False
    )
    work_item_id = Column(Text, ForeignKey("work_items.id"), nullable=False)

    title_snapshot = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)
    summary = Column(Text, nullable=False, default="")
    latest_progress = Column(Text, nullable=False, default="")
    blocker = Column(Text, nullable=False, default="")
    next_action = Column(Text, nullable=False, default="")

    previous_owner = Column(Text, nullable=False, default="")
    next_owner = Column(Text, nullable=False, default="")
    start_date = Column(Text)
    end_date = Column(Text)

    source_ids_json = Column(Text, nullable=False, default="[]")
    ai_confidence = Column(Float, nullable=False, default=0.0)

    # pending|approved|rejected|edited
    review_status = Column(Text, nullable=False, default="pending")
    human_edited = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class MonthlyPlanItem(Base):
    """月度/季度定期工作计划，通用工作的唯一来源。"""

    __tablename__ = "monthly_plan_items"
    __table_args__ = (
        Index("idx_monthly_plan_month_station", "plan_month", "station_id"),
        Index("idx_monthly_plan_dates", "plan_start", "plan_end"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("mp"))
    plan_month = Column(Text, nullable=False)  # 2026-08
    # region|station|multi_station
    scope_type = Column(Text, nullable=False, default="station")
    station_id = Column(Integer, ForeignKey("stations.id"))
    title = Column(Text, nullable=False)
    # monthly|quarterly
    category = Column(Text, nullable=False, default="monthly")
    plan_start = Column(Text)
    plan_end = Column(Text)
    owner = Column(Text, nullable=False, default="")
    status = Column(Text, nullable=False, default="pending")
    notes = Column(Text, nullable=False, default="")
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class HandoverGeneralItem(Base):
    __tablename__ = "handover_general_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "monthly_plan_item_id", "station_meta_id"),
    )

    id = Column(Text, primary_key=True, default=lambda: new_id("hg"))
    batch_id = Column(
        Text, ForeignKey("handover_batches.id", ondelete="CASCADE"), nullable=False
    )
    station_meta_id = Column(Text, ForeignKey("handover_station_meta.id"))
    monthly_plan_item_id = Column(
        Text, ForeignKey("monthly_plan_items.id"), nullable=False
    )
    status = Column(Text, nullable=False)
    owner = Column(Text, nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    review_status = Column(Text, nullable=False, default="approved")
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class DeviceChange(Base):
    __tablename__ = "device_changes"

    id = Column(Text, primary_key=True, default=lambda: new_id("dc"))
    batch_id = Column(
        Text, ForeignKey("handover_batches.id", ondelete="CASCADE"), nullable=False
    )
    station_meta_id = Column(
        Text, ForeignKey("handover_station_meta.id", ondelete="CASCADE"), nullable=False
    )
    content = Column(Text, nullable=False)
    source_record_id = Column(Text)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False, default=now_iso)
    updated_at = Column(Text, nullable=False, default=now_iso)


class DocumentSnapshot(Base):
    __tablename__ = "document_snapshots"
    __table_args__ = (UniqueConstraint("station_meta_id", "version"),)

    id = Column(Text, primary_key=True, default=lambda: new_id("snap"))
    batch_id = Column(Text, ForeignKey("handover_batches.id"), nullable=False)
    station_meta_id = Column(
        Text, ForeignKey("handover_station_meta.id"), nullable=False
    )
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False, default="draft")  # draft|published
    data_json = Column(Text, nullable=False)
    docx_path = Column(Text, nullable=False)
    sha256 = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False, default=now_iso)
