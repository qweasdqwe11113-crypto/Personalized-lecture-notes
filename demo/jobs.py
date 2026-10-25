"""One process, one worker, bounded queue; saved input freezes at submission."""
from collections import deque
from threading import Condition, Thread
from uuid import uuid4

from prompts import PreparedRequest
from qwen import ModelError
from runs import create_run, fail_run, run_detail


class JobError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class JobQueue:
    def __init__(self, db, execute, capacity=4):
        self.db, self.execute, self.capacity = db, execute, capacity
        self.pending, self.active = deque(), False
        self.active_id = None
        self.condition = Condition()
        self.thread = None
        self.stopping = False

    def recover(self):
        """Called once before starting a new server; never replay old requests."""
        with self.db() as conn:
            rows = conn.execute("SELECT id,run_id FROM generation_jobs WHERE status IN ('queued','running') FOR UPDATE").fetchall()
            for row in rows:
                run = run_detail(conn, row['run_id'])
                status = 'succeeded' if run['status'] == 'succeeded' else 'interrupted'
                if run['status'] == 'running':
                    fail_run(conn, run['id'], ModelError('process_interrupted', '本地服务进程已中断，请核查输入后重试。', 'process'), 0)
                conn.execute('UPDATE generation_jobs SET status=%s,finished_at=now() WHERE id=%s', (status, row['id']))

    def submit(self, sid, topic, goal, context, prepared, parent=None):
        with self.condition:
            if self.stopping:
                raise JobError('服务正在停止，请稍后重试。', 503)
            if len(self.pending) + int(self.active) >= self.capacity:
                raise JobError('本地队列已满（最多 4 个任务），请等待后再提交。', 429)
            job_id = uuid4().hex
            with self.db() as conn:
                run_id = create_run(conn, sid, topic, goal, context, prepared)
                conn.execute("INSERT INTO generation_jobs (id,run_id,parent_run_id,status) VALUES (%s,%s,%s,'queued')", (job_id, run_id, parent))
            self.pending.append((job_id, run_id, sid, topic, goal, context, prepared))
            if self.thread is None:
                self.thread = Thread(target=self._work, daemon=True, name='lecture-worker')
                self.thread.start()
            self.condition.notify_all()
        return self.detail(job_id)

    def retry(self, run_id):
        with self.db() as conn:
            run = run_detail(conn, run_id)
        if not run:
            raise JobError('生成记录不存在。', 404)
        if run['status'] != 'failed':
            raise JobError('只能重试已失败记录。', 409)
        if not run['integrity_valid'] or not run['request_payload']:
            raise JobError('原输入校验不一致，不能重试。', 409)
        prepared = PreparedRequest(run['request_body'], run['request_sha256'], run['prompt_version'], run['input_version'])
        return self.submit(run['student_id'], run['topic_key'], run['goal'], run['context'], prepared, parent=run_id)

    def cancel(self, job_id):
        with self.condition:
            with self.db() as conn:
                row = conn.execute('SELECT * FROM generation_jobs WHERE id=%s FOR UPDATE', (job_id,)).fetchone()
                if not row:
                    raise JobError('任务不存在。',404)
                if row['status'] == 'cancelled':
                    return {'id':job_id,'status':'cancelled'}
                if row['status'] != 'queued':
                    raise JobError('仅可取消尚未开始的排队任务；已发送的模型调用仍需等待完成。',409)
                fail_run(conn,row['run_id'],ModelError('cancelled','教师已取消排队任务，未调用模型。','queue'),0)
                conn.execute("UPDATE generation_jobs SET status='cancelled',finished_at=now() WHERE id=%s", (job_id,))
            self.pending = deque(item for item in self.pending if item[0] != job_id)
            self.condition.notify_all()
        return self.detail(job_id)

    def detail(self, job_id):
        with self.db() as conn:
            row = conn.execute('''SELECT j.*,r.lecture_id,r.error_message,r.error_code,r.usage,
                r.elapsed_seconds,r.topic,r.student_id,r.context ? 'section_edit' AS section_rewrite,
                r.response_metadata->'saved_revision' AS saved_revision FROM generation_jobs j
                JOIN generation_runs r ON r.id=j.run_id WHERE j.id=%s''', (job_id,)).fetchone()
            if not row:
                raise JobError('任务不存在。', 404)
            row['queue_position'] = conn.execute("SELECT count(*) AS n FROM generation_jobs WHERE status='queued' AND created_at<=%s", (row['created_at'],)).fetchone()['n'] if row['status'] == 'queued' else 0
        with self.condition:
            owned = job_id == self.active_id or any(item[0] == job_id for item in self.pending)
        row['confirmation_pending'] = row['status'] in ('queued', 'running') and not owned
        return row

    def recent(self):
        with self.db() as conn:
            return conn.execute('''SELECT j.*,r.lecture_id,r.error_message,r.topic,s.name,
                r.context ? 'section_edit' AS section_rewrite
                FROM generation_jobs j JOIN generation_runs r ON r.id=j.run_id
                JOIN students s ON s.id=r.student_id ORDER BY j.created_at DESC LIMIT 12''').fetchall()

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending or self.stopping)
                if self.stopping and not self.pending:
                    return
                item = self.pending.popleft()
                self.active = True
                self.active_id = item[0]
            try:
                self.execute(*item)
            except Exception:
                # Attempt a safe terminal state without logging raw credentials.
                try:
                    with self.db() as conn:
                        run = run_detail(conn, item[1])
                        status = 'succeeded' if run['status'] == 'succeeded' else 'failed'
                        if run['status'] == 'running':
                            fail_run(conn, run['id'], ModelError('worker_error', '任务处理异常，请检查本地服务后重试。', 'process'), 0)
                        conn.execute('UPDATE generation_jobs SET status=%s,finished_at=now() WHERE id=%s', (status, item[0]))
                except Exception:
                    pass  # Missing final confirmation remains visible, recovered on restart.
            finally:
                with self.condition:
                    self.active = False
                    self.active_id = None
                    self.condition.notify_all()

    def wait_idle(self, timeout=5):
        with self.condition:
            return self.condition.wait_for(lambda: not self.active and not self.pending, timeout)

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=5)
