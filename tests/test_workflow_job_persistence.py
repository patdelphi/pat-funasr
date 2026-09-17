"""
程序说明：
WorkflowJobManager 任务队列 SQLite 持久化测试（unittest）。

目标：
- 验证任务快照写入 SQLite（WAL + 事务）。
- 验证重启后终态任务可恢复、非终态任务标记失败。
- 验证 prune 同步清理数据库记录。
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "app"))
sys.path.insert(0, str(_ROOT / "app" / "openai_api"))
from workflow_service import WorkflowJobManager  # noqa: E402


def _make_db_dir() -> str:
    """创建独立临时目录存放测试数据库。"""
    return tempfile.mkdtemp(prefix="pat-funasr-workflow-db-")


def _make_db_schema(db_path: str) -> None:
    """按生产相同的建表语句初始化测试数据库。"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workflow_jobs "
        "(job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


class TestWorkflowJobPersistence(unittest.TestCase):
    def setUp(self):
        self.db_dir = _make_db_dir()
        self.db_path = str(Path(self.db_dir) / "workflow_jobs.db")

    def tearDown(self):
        shutil.rmtree(self.db_dir, ignore_errors=True)

    def _make_manager(self, **kwargs):
        return WorkflowJobManager(max_workers=2, db_path=self.db_path, **kwargs)

    def _read_db_status(self, job_id: str) -> dict:
        """直接从 SQLite 读取某任务的 payload，绕过管理器。"""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload FROM workflow_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        return json.loads(row[0])

    def test_submit_writes_queued_snapshot_to_db(self):
        manager = self._make_manager()
        gate = threading.Event()
        job_id = manager.submit(
            config={"preset_id": "custom"},
            source_path="demo.wav",
            runner=lambda ctx: (gate.wait(5), {"ok": True})[1],
        )
        # submit 返回前快照已写入；runner 被阻塞，任务停留在未完成状态
        payload = self._read_db_status(job_id)
        self.assertEqual(payload["job_id"], job_id)
        self.assertIn(payload["status"], {"queued", "running"})
        self.assertEqual(payload["config"], {"preset_id": "custom"})
        # 不可序列化字段不入库
        self.assertNotIn("cancel_event", payload)
        self.assertNotIn("future", payload)
        gate.set()
        self.assertEqual(manager.wait_for_terminal(job_id, timeout=5)["status"], "completed")
        manager.shutdown(wait=True)

    def test_completed_job_survives_restart(self):
        manager = self._make_manager()
        job_id = manager.submit(
            config={"preset_id": "custom"},
            source_path="demo.wav",
            runner=lambda ctx: {"ok": True},
        )
        snapshot = manager.wait_for_terminal(job_id, timeout=5)
        self.assertEqual(snapshot["status"], "completed")
        manager.shutdown(wait=True)

        # 模拟服务重启：同一 db 路径新建管理器
        manager2 = self._make_manager()
        try:
            restored = manager2.get_snapshot(job_id)
            self.assertEqual(restored["status"], "completed")
            self.assertEqual(restored["result"], {"ok": True})
            self.assertGreaterEqual(len(restored["events"]), 2)
        finally:
            manager2.shutdown(wait=True)

    def test_interrupted_job_marked_failed_after_restart(self):
        # 直接写入一条 running 状态旧记录，模拟服务崩溃现场
        payload = {
            "job_id": "wf_crash",
            "trace_id": "trace_crash",
            "status": "running",
            "progress": 0.4,
            "current_stage": "transcription.primary",
            "current_model": "sensevoice",
            "created_at": "2026-09-16T00:00:00+00:00",
            "updated_at": "2026-09-16T00:00:00+00:00",
            "config": {"preset_id": "custom"},
            "source_path": "demo.wav",
            "events": [],
            "result": None,
            "error": None,
            "stage_timings": [],
        }
        _make_db_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO workflow_jobs (job_id, payload, updated_at) VALUES (?, ?, ?)",
            ("wf_crash", json.dumps(payload, ensure_ascii=False), payload["updated_at"]),
        )
        conn.commit()
        conn.close()

        manager = self._make_manager()
        try:
            snapshot = manager.get_snapshot("wf_crash")
            self.assertEqual(snapshot["status"], "failed")
            self.assertIn("服务重启", snapshot["error"])
            error_codes = [e.get("error_code") for e in snapshot["events"]]
            self.assertIn("WORKFLOW_INTERRUPTED_BY_RESTART", error_codes)
            # 失败状态已写回数据库
            db_payload = self._read_db_status("wf_crash")
            self.assertEqual(db_payload["status"], "failed")
        finally:
            manager.shutdown(wait=True)

    def test_prune_removes_db_rows(self):
        manager = self._make_manager()
        job_id = manager.submit(
            config={"preset_id": "custom"},
            source_path="demo.wav",
            runner=lambda ctx: {"ok": True},
        )
        manager.wait_for_terminal(job_id, timeout=5)
        # 强制超 TTL，触发清理
        pruned = manager.prune_terminal_jobs(now=time.time() + 10**6)
        self.assertGreaterEqual(pruned, 1)
        manager.shutdown(wait=True)

        # 重启后数据库记录已被同步清理
        manager2 = self._make_manager()
        try:
            self.assertEqual(manager2.list_snapshots(), [])
        finally:
            manager2.shutdown(wait=True)

    def test_restart_keeps_multiple_terminal_jobs(self):
        manager = self._make_manager()
        ids = []
        for _ in range(3):
            ids.append(
                manager.submit(
                    config={"preset_id": "custom"},
                    source_path="demo.wav",
                    runner=lambda ctx: {"ok": True},
                )
            )
        for job_id in ids:
            manager.wait_for_terminal(job_id, timeout=5)
        manager.shutdown(wait=True)

        manager2 = self._make_manager()
        try:
            snapshots = manager2.list_snapshots()
            self.assertEqual(len(snapshots), 3)
            self.assertEqual({s["status"] for s in snapshots}, {"completed"})
        finally:
            manager2.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
