#!/usr/bin/env python3
"""Stdlib-only host helper: validate backup uploads and atomically report state."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def checked_manifest(bundle):
    manifest = json.loads(bundle.with_suffix('.json').read_text(encoding='utf-8'))
    if manifest.get('verification') != 'verified':
        raise ValueError('备份尚未完成校验：' + bundle.name)
    digest = hashlib.sha256()
    with bundle.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != manifest.get('bundle_sha256'):
        raise ValueError('备份 SHA256 不一致：' + bundle.name)
    return manifest


def fresh_backups(root):
    dates = []
    for bundle in (root / 'snapshots/full_backups').glob('jx-handover-backup-*.zip'):
        manifest = checked_manifest(bundle)
        date = datetime.fromisoformat(manifest['created_at'])
        if date.tzinfo is None:
            raise ValueError('备份时间缺少时区')
        dates.append(date)
    if not dates:
        raise ValueError('没有可上传的完整备份')
    latest = max(dates)
    age = (datetime.now(timezone.utc) - latest).total_seconds()
    if age < -300 or age > 36 * 3600:
        raise ValueError('最近备份已超过 36 小时或服务器时间异常；请先生成新备份')
    return latest.isoformat()


def write_receipt(root, state, count=0, bundle=None):
    directory = root / 'snapshots'
    if not directory.is_dir():
        raise ValueError('数据目录 snapshots 不存在，请检查 JX_HOST_DATA_DIR')
    path = directory / 'oss-sync-status.json'
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    data = {'state': state, 'updated_at': now, 'synced_count': count,
            'last_success_at': previous.get('last_success_at'),
            'latest_backup_at': previous.get('latest_backup_at'),
            'run_latest_at': None if state == 'running' else previous.get('run_latest_at'),
            'target': os.environ.get('JX_OSS_URI', '')}
    if state == 'uploaded':
        created = datetime.fromisoformat(checked_manifest(bundle)['created_at'])
        if created.tzinfo is None:
            raise ValueError('备份时间缺少时区')
        earlier = previous.get('run_latest_at')
        newest = max(created, datetime.fromisoformat(earlier)) if earlier else created
        data.update(state='running', synced_count=int(previous.get('synced_count', 0)) + 1,
                    run_latest_at=newest.isoformat())
    if state == 'success':
        if count < 1 or count != previous.get('synced_count') or not previous.get('run_latest_at'):
            raise ValueError('没有备份完成上传，不能报告同步成功')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous['run_latest_at'])).total_seconds()
        if age < -300 or age > 36 * 3600:
            raise ValueError('本次上传的备份不是最近 36 小时内生成的备份')
        data.update(last_success_at=now, latest_backup_at=previous['run_latest_at'])
    fd, temporary = tempfile.mkstemp(prefix='.oss-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'running', 'uploaded', 'success', 'failed'])
    parser.add_argument('--count', type=int, default=0)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    root = Path(os.environ['JX_HOST_DATA_DIR']).resolve()
    if args.command == 'check':
        fresh_backups(root)
    else:
        write_receipt(root, args.command, args.count, args.bundle)


if __name__ == '__main__':
    main()
