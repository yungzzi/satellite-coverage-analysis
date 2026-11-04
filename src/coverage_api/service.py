"""常驻单计算进程 + 轻量监督线程。CPU 工作不会占用 HTTP 事件循环。"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import signal
import sys
import threading

import psutil
from .config import Settings
from .store import JobStore

log = logging.getLogger(__name__)


class InstanceLock:
    """跨 Windows/Linux 的持有式文件锁，禁止多个 Uvicorn worker 争同一队列。"""
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.file = open(Path(directory) / 'service.lock', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.file.close()
            raise RuntimeError('此数据目录已有服务运行；请使用 --workers 1，禁止重复实例。') from None

    def close(self):
        self.file.close()


def kill_tree(pid):
    """只清理本服务启动的计算进程树，包含 CBC 子进程。"""
    try:
        process = psutil.Process(pid)
        children = process.children(recursive=True)
        if os.name != 'nt' and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
        psutil.wait_procs(children, timeout=2)
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass


def worker_loop(settings: Settings, stop, parent_pid: int):
    if os.name != 'nt':
        os.setsid()

    def watch_parent():
        # API 被强杀也不能留下持续计算的孤儿进程和 CBC。
        while not stop.wait(1):
            if not psutil.pid_exists(parent_pid):
                kill_tree(os.getpid())
                return

    threading.Thread(target=watch_parent, daemon=True).start()
    store = JobStore(settings.state_dir)
    while not stop.is_set():
        job = store.claim()
        if job is None:
            stop.wait(0.2)
            continue
        job_id = job['job_id']
        try:
            # 延迟导入；同一常驻 worker 内可复用原引擎的机会窗口缓存。
            from siriagent_skill.schemas import normalize_request
            from siriagent_skill.facade import execute_request
            with redirect_stdout(sys.stderr):
                request = normalize_request(json.loads(job['payload']))
                result = execute_request(request, root=settings.project_root)
            # 不把服务器绝对路径暴露到浏览器。数值、窗口和几何口径保持不变。
            result['input_summary']['tle_path'] = Path(result['input_summary']['tle_path']).name
            target = store.result_path(job_id)
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temporary.replace(target)
            store.finish(job_id)
        except Exception:
            log.exception('计算任务失败 job_id=%s', job_id)
            store.finish(job_id, error={'code': 'execution_error', 'message': '计算失败，请按任务编号查询服务日志。'})


class JobService:
    def __init__(self, settings):
        self.settings = settings
        self.store = JobStore(settings.state_dir)
        self.context = mp.get_context('spawn')
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.lock = None

    def start(self):
        self.lock = InstanceLock(self.settings.state_dir)
        try:
            # 上一次运行中断的任务明确失败；已排队任务仍保留并继续领取。
            self.store.interrupt_running()
            self._spawn()
            self.thread = threading.Thread(target=self._supervise, name='job-supervisor', daemon=True)
            self.thread.start()
        except BaseException:
            self.lock.close()
            raise

    def _spawn(self):
        self.worker_stop = self.context.Event()
        self.process = self.context.Process(target=worker_loop, args=(self.settings, self.worker_stop, os.getpid()), daemon=True)
        self.process.start()

    def _terminate(self):
        if self.process:
            if self.process.is_alive():
                kill_tree(self.process.pid)
            self.process.join(timeout=3)
            self.process.close()
            self.process = None

    def _supervise(self):
        try:
            while not self.stop_event.wait(0.25):
                if not self.process.is_alive():
                    log.error('计算 worker 退出，标记在途任务并重启')
                    self._terminate()
                    self.store.interrupt_running('worker_exited')
                    if not self.stop_event.is_set():
                        self._spawn()
                    continue
                running = self.store.running()
                if self.stop_event.is_set():
                    return
                if running:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(running['started_at'])).total_seconds()
                    if age > self.settings.job_timeout_s:
                        self._terminate()
                        self.store.finish(running['job_id'], error={'code': 'timeout', 'message': '超过服务计算时限，请缩小范围或调整配置。'})
                        self.store.interrupt_running()
                        if not self.stop_event.is_set():
                            self._spawn()
        except Exception:
            log.exception('任务监督器异常，服务进入未就绪状态')
            self._terminate()

    def ready(self):
        try:
            return bool(self.thread and self.thread.is_alive() and self.process and self.process.is_alive())
        except ValueError:  # 监督线程刚关闭旧进程，正在启动替代进程。
            return False

    def close(self):
        self.stop_event.set()
        if self.thread:
            # 等监督线程退出后再释放单实例锁，不能留下仍会重建 worker 的线程。
            self.thread.join()
        self.worker_stop.set()
        if self.process:
            self.process.join(timeout=3)
        self._terminate()
        self.store.interrupt_running()
        if self.lock:
            self.lock.close()
