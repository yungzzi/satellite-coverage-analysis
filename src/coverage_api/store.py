"""SQLite 持久化任务状态。有界入队和领取任务均使用事务。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


class QueueFull(Exception):
    pass


class JobStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / 'jobs.sqlite3'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, status TEXT NOT NULL, operation TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                payload TEXT NOT NULL, error TEXT)''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, payload, capacity):
        job_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= capacity:
                raise QueueFull()
            db.execute('INSERT INTO jobs(job_id,status,operation,created_at,payload) VALUES (?,?,?,?,?)',
                       (job_id, 'queued', payload['operation'], now(), json.dumps(payload, allow_nan=False)))
        return self.get(job_id)

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['error'] = json.loads(result['error']) if result['error'] else None
        return result

    def claim(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,job_id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE job_id=?", (now(), row['job_id']))
            return dict(row)

    def running(self):
        with self.connection() as db:
            row = db.execute("SELECT job_id,started_at FROM jobs WHERE status='running' LIMIT 1").fetchone()
            return dict(row) if row else None

    def finish(self, job_id, *, error=None):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?,finished_at=?,error=? WHERE job_id=? AND status='running'",
                       ('failed' if error else 'completed', now(), json.dumps(error) if error else None, job_id))

    def interrupt_running(self, code='interrupted'):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='failed',finished_at=?,error=? WHERE status='running'",
                       (now(), json.dumps({'code': code, 'message': '计算进程中断，请重新提交任务。'})))

    def result_path(self, job_id):
        # 即使被内部调用，也只允许 UUID 文件名，不能用 URL 拼任意文件路径。
        return self.directory / f'{uuid.UUID(str(job_id))}.json'
