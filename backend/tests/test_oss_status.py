"""Host receipt contract: real hashes, stale/empty failures, admin-only display."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import config
from app.services.oss_status import oss_sync_status

spec = importlib.util.spec_from_file_location('oss_receipt', ROOT / 'deploy/cloud/scripts/oss-sync-receipt.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class OssStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshots = self.root / 'snapshots'
        self.bundles = self.snapshots / 'full_backups'
        self.bundles.mkdir(parents=True)
        self.patch = patch.object(config, 'SNAPSHOT_DIR', self.snapshots)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def backup(self, hours=0):
        path = self.bundles / 'jx-handover-backup-test.zip'
        path.write_bytes(b'test-backup-content')
        path.with_suffix('.json').write_text(json.dumps({
            'verification': 'verified',
            'created_at': (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(),
            'bundle_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }), encoding='utf-8')
        return path

    def test_no_receipt_and_invalid_receipt_are_never_success(self):
        self.assertEqual(oss_sync_status()['state'], 'unknown')
        (self.snapshots / 'oss-sync-status.json').write_text('{invalid', encoding='utf-8')
        self.assertEqual(oss_sync_status()['state'], 'invalid')

    def test_success_then_failure_preserves_last_success(self):
        bundle = self.backup()
        helper.write_receipt(self.root, 'running')
        self.assertEqual(oss_sync_status()['state'], 'running')
        helper.write_receipt(self.root, 'uploaded', bundle=bundle)
        helper.write_receipt(self.root, 'success', 1)
        first = oss_sync_status()
        self.assertEqual(first['state'], 'success')
        self.assertEqual(first['synced_count'], 1)
        helper.write_receipt(self.root, 'failed')
        failed = oss_sync_status()
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['last_success_at'], first['last_success_at'])

    def test_new_unuploaded_backup_cannot_be_claimed_by_receipt(self):
        bundle = self.backup(hours=1)
        helper.write_receipt(self.root, 'running')
        helper.write_receipt(self.root, 'uploaded', bundle=bundle)
        uploaded_at = json.loads(bundle.with_suffix('.json').read_text())['created_at']
        self.backup(hours=0)
        helper.write_receipt(self.root, 'success', 1)
        self.assertEqual(oss_sync_status()['latest_backup_at'], uploaded_at)

    def test_empty_stale_and_corrupt_backups_cannot_report_success(self):
        with self.assertRaises(ValueError):
            helper.write_receipt(self.root, 'success', 0)
        with self.assertRaises(ValueError):
            helper.fresh_backups(self.root)
        path = self.backup(hours=40)
        with self.assertRaises(ValueError):
            helper.fresh_backups(self.root)
        self.backup()
        path.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            helper.fresh_backups(self.root)

    def test_old_receipt_is_marked_stale_and_errors_are_not_exposed(self):
        path = self.snapshots / 'oss-sync-status.json'
        path.write_text(json.dumps({'state': 'success',
            'updated_at': (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
            'message': 'secret-key-should-not-be-shown', 'synced_count': 2}), encoding='utf-8')
        result = oss_sync_status()
        self.assertTrue(result['stale'])
        self.assertNotIn('secret-key', json.dumps(result))
