"""Word 发布：临时文件 -> 校验 -> 快照 -> 输出目录。

硬约束：只有 review_status in (approved, edited) 的数据可参与渲染，
AI 与待复核数据永远不能直接生成正式 Word。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from app import config
from app.models import (
    DocumentSnapshot,
    HandoverBatch,
    HandoverItem,
    HandoverStationMeta,
    Station,
    now_iso,
)
from app.services.document import mapper, renderer, validator


def render_and_snapshot(db, batch_id: str, station_meta_id: str) -> dict:
    batch = db.get(HandoverBatch, batch_id)
    meta = db.get(HandoverStationMeta, station_meta_id)
    if batch is None or meta is None or meta.batch_id != batch.id:
        raise HTTPException(404, "交接班或场站信息不存在")

    # 硬校验：存在未复核事项则禁止生成
    pending = (db.query(HandoverItem)
               .filter(HandoverItem.station_meta_id == meta.id,
                       HandoverItem.review_status == "pending")
               .count())
    if pending:
        raise HTTPException(
            422, f"仍有 {pending} 条事项待人工复核，不能生成正式 Word。"
                 "请先在编辑器中逐条确认。")

    station = db.get(Station, meta.station_id)
    data = mapper.build_context(db, batch, meta)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = (f"{station.name}交接班记录_"
                 f"{batch.start_date.replace('-', '')}-"
                 f"{batch.end_date.replace('-', '')}_V.tmp.docx")
    tmp_path = config.GENERATED_DIR / file_name
    renderer.render_word(config.WORD_TEMPLATE, data["ctx"], data["colors"],
                         tmp_path)

    # 校验 DOCX 结构，损坏禁止发布
    try:
        validator.validate_docx(tmp_path)
    except RuntimeError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"DOCX 校验失败，禁止发布：{exc}") from exc

    sha = hashlib.sha256(tmp_path.read_bytes()).hexdigest()

    # 版本号 = 该场站班次已有快照 + 1
    last = (db.query(DocumentSnapshot)
            .filter(DocumentSnapshot.station_meta_id == meta.id)
            .order_by(DocumentSnapshot.version.desc()).first())
    version = (last.version + 1) if last else 1

    final_name = (f"{station.name}交接班记录_"
                  f"{batch.start_date.replace('-', '')}-"
                  f"{batch.end_date.replace('-', '')}_V{version:03d}.docx")
    # 输出到 generated/{场站码}/{年月}/发布历史 + 当前版
    period_dir = (config.GENERATED_DIR / station.code
                  / batch.end_date[:7].replace("-", ""))
    history_dir = period_dir / "发布历史"
    current_dir = period_dir / "当前版"
    history_dir.mkdir(parents=True, exist_ok=True)
    current_dir.mkdir(parents=True, exist_ok=True)
    final_path = history_dir / final_name
    shutil.move(str(tmp_path), str(final_path))
    current_path = current_dir / (
        f"{station.name}交接班记录_"
        f"{batch.start_date.replace('-', '')}-"
        f"{batch.end_date.replace('-', '')}.docx")
    shutil.copyfile(final_path, current_path)

    # 云盘发布目录（可选配置）
    cloud_path = None
    if config.CLOUD_PUBLISH_DIR:
        cloud_dir = Path(config.CLOUD_PUBLISH_DIR) / station.code
        cloud_dir.mkdir(parents=True, exist_ok=True)
        cloud_path = cloud_dir / final_name
        shutil.copyfile(final_path, cloud_path)

    snapshot = DocumentSnapshot(
        batch_id=batch.id,
        station_meta_id=meta.id,
        version=version,
        status="published",
        data_json=json.dumps(data["ctx"], ensure_ascii=False),
        docx_path=str(final_path),
        sha256=sha,
        created_at=now_iso(),
    )
    db.add(snapshot)
    batch.status = "published"
    db.commit()

    return {
        "snapshot_id": snapshot.id,
        "version": version,
        "sha256": sha,
        "docx_path": str(final_path),
        "current_path": str(current_path),
        "cloud_path": str(cloud_path) if cloud_path else None,
        "download_url": f"/api/documents/{snapshot.id}/download",
    }
