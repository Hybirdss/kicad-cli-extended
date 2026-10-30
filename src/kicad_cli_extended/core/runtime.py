"""Per-request deadline, cancellation and measured backend state."""
from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..utils.windows_job import command as process_command


@dataclass
class Request:
    request_id: Any
    timeout_ms: int
    started: float = field(default_factory=time.monotonic)
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    process: Any = None
    worker: Any = None
    meta: dict = field(default_factory=lambda: {'worker_started': False, 'worker_reused': None,
        'worker_start_count': 0, 'worker_start_ms': None, 'load_ms': None, 'netlist_ms': None,
        'validation_ms': None, 'checkpoint_ms': None})

    def stop(self):
        self.cancelled.set()
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                if os.name != 'nt':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass
        if self.worker is not None:
            self.worker.close(force=True)


current = contextvars.ContextVar('kce_request', default=None)


@contextlib.contextmanager
def scope(request):
    token = current.set(request)
    try:
        yield request
    finally:
        current.reset(token)
        request.done.set()


def remaining(timeout):
    request = current.get()
    if request is None:
        return timeout
    left = request.timeout_ms / 1000 - (time.monotonic() - request.started)
    if request.cancelled.is_set() or left <= 0:
        from ..utils.kicad_backend import KicadError
        raise KicadError('Request timed out or was cancelled', {'request_id': request.request_id,
            'timeout_ms': request.timeout_ms, 'cancelled': request.cancelled.is_set(),
            'commit_state': request.meta.get('commit_state', 'unknown'), 'retryable': True})
    return min(timeout, left)


def execute(cmd, *, timeout, **kwargs):
    """Communicate with a killable process group, polling only when cancellation is possible."""
    request = current.get()
    if request is None:
        return subprocess.run(process_command(cmd), timeout=timeout, **kwargs)
    timeout = remaining(timeout)
    kwargs.pop('capture_output', None)
    proc = subprocess.Popen(process_command(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=os.name != 'nt', **kwargs)
    request.process = proc
    started = time.monotonic()
    end = started + timeout
    error = None
    try:
        while True:
            left = min(remaining(timeout), end - time.monotonic())
            if left <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                stdout, stderr = proc.communicate(timeout=min(left, 0.05))
                remaining(timeout)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= end:
                    raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        if proc.poll() is None:
            try:
                if os.name != 'nt':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
        proc.communicate()
        request.process = None
        if error is not None:
            error.runtime_details = details(os.path.basename(str(cmd[0])), ' '.join(map(str, cmd[:2])),
                                            pid=proc.pid, returncode=proc.returncode, started=started)
            error.runtime_details['retryable'] = isinstance(error, subprocess.TimeoutExpired) or request.cancelled.is_set() or 'timed out' in str(error).lower()


def details(backend, operation, *, pid=None, returncode=None, timeout=None, started=None):
    request = current.get()
    return {'backend': backend, 'operation': operation,
            'request_id': request.request_id if request else None, 'worker_pid': pid,
            'returncode': returncode, 'signal': -returncode if returncode is not None and returncode < 0 else None,
            'timeout_ms': request.timeout_ms if request else timeout * 1000 if timeout else None,
            'elapsed_ms': round((time.monotonic() - (started or time.monotonic())) * 1000, 3),
            'commit_state': request.meta.get('commit_state', 'unknown') if request else 'unknown', 'retryable': False}
