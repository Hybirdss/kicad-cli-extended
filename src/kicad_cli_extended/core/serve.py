"""A resident JSON-RPC 2.0 server with one request per stdin line."""

from __future__ import annotations

import contextlib
import json
import sys
import time
import threading
import queue
from typing import Any, Callable, Dict, List, Optional


def _invalid_constant(value: str):
    raise ValueError(f"Non-JSON number: {value}")


def _error(rid: Any, code: int, message: str, data=None) -> Dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": error}


def _valid_run_params(params):
    return (isinstance(params, dict) and isinstance(params.get('args'), list) and bool(params['args'])
            and all(isinstance(arg, str) for arg in params['args'])
            and ('project' not in params or isinstance(params['project'], str) and bool(params['project'])))


class Server:
    def __init__(self, dispatch: Callable[[List[str], Optional[str]], Any], out=None, err=None, request_timeout_ms=30000, progress=False):
        self.dispatch = dispatch
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self.stopped = False
        self.request_timeout_ms = request_timeout_ms
        self.progress = progress
        self.requests = {}
        self.write_lock = threading.Lock()

    def handle(self, line: str) -> Optional[Dict[str, Any]]:
        from ..cli import EXIT_CODES, error_payload

        try:
            req = json.loads(line, parse_constant=_invalid_constant)
        except ValueError as e:
            return _error(None, -32700, f"Invalid JSON: {e}")
        if (not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str)
                or ("id" in req and (isinstance(req["id"], bool) or not isinstance(req["id"], (str, int, float, type(None)))))):
            return _error(None, -32600, "Expected a JSON-RPC 2.0 request with method and a string, number or null id")
        rid, notification = req.get("id"), "id" not in req
        method, params = req["method"], req.get("params", {})
        if not isinstance(params, dict):
            response = _error(rid, -32602, "params must be an object")
        elif method == "shutdown":
            self.stopped = True
            response = {"jsonrpc": "2.0", "id": rid, "result": {"shutdown": True}}
        elif method == "cancel":
            if set(params) != {"request_id"} or not isinstance(params["request_id"], (str, int, float)) or isinstance(params["request_id"], bool):
                response = _error(rid, -32602, "cancel requires request_id (string or number)")
            else:
                target = self.requests.get(params["request_id"])
                if target is not None and not target.done.is_set():
                    target.stop()
                    target.done.wait()
                    response = {"jsonrpc": "2.0", "id": rid, "result": {"cancelled": True, "request_id": params["request_id"]}}
                else:
                    response = {"jsonrpc": "2.0", "id": rid, "result": {"cancelled": False, "request_id": params["request_id"]}}
        elif method != "run":
            response = _error(rid, -32601, "Unknown method; supported: run, cancel, shutdown")
        elif not _valid_run_params(params):
            response = _error(rid, -32602, 'run params must contain non-empty string args and an optional project path')
        else:
            from . import runtime
            timeout = params.get('timeout_ms', self.request_timeout_ms)
            if type(timeout) is not int or timeout <= 0:
                return None if notification else _error(rid, -32602, "timeout_ms must be a positive integer")
            state = self.requests.get(rid) or runtime.Request(rid, timeout)
            state.started = time.monotonic()
            if self.progress:
                self.err.write(json.dumps({'progress': 'request_started', 'request_id': rid}) + '\n')
                self.err.flush()
            try:
                with runtime.scope(state), contextlib.redirect_stdout(self.err):
                    runtime.remaining(timeout / 1000)
                    result = self.dispatch(params["args"], params.get("project"))
                    runtime.remaining(timeout / 1000)
                meta = dict(state.meta, elapsed_ms=round((time.monotonic() - state.started) * 1000, 3))
                response = {"jsonrpc": "2.0", "id": rid, "result": result, "meta": meta}
            except Exception as e:
                payload = error_payload(e)
                details = runtime.details("kce", " ".join(params["args"][:2]), started=state.started)
                details.update(request_id=rid, timeout_ms=timeout,
                               commit_state=state.meta.get('commit_state', 'unknown'))
                details.update(payload.get('details') or {})
                details['exit_code'] = EXIT_CODES[payload['category']]
                response = _error(rid, -32000, payload["error"],
                                  {"kce": payload, "category": payload["category"],
                                   "exit_code": EXIT_CODES[payload["category"]], "details": details})
            finally:
                state.done.set()
                if self.requests.get(rid) is state:
                    self.requests.pop(rid, None)
                if self.progress:
                    self.err.write(json.dumps({'progress': 'request_finished', 'request_id': rid,
                        'elapsed_ms': round((time.monotonic() - state.started) * 1000, 3)}) + '\n')
                    self.err.flush()
        return None if notification else response

    def _write(self, response):
        if response is not None:
            with self.write_lock:
                self.out.write(json.dumps(response, default=str, ensure_ascii=True) + "\n")
                self.out.flush()

    def serve_forever(self, stream=None) -> None:
        """One executor owns CLI state; the reader can cancel its active request.

        EOF and shutdown drain accepted requests. Cancellation acknowledges only after
        the executor has completed rollback and every native process has been reaped.
        """
        from .runtime import Request
        stream = stream if stream is not None else sys.stdin
        pending = queue.Queue()
        def execute():
            while True:
                line = pending.get()
                try:
                    if line is None:
                        return
                    self._write(self.handle(line))
                finally:
                    pending.task_done()
        executor = threading.Thread(target=execute, name='kce-executor')
        executor.start()
        try:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line, parse_constant=_invalid_constant)
                except ValueError:
                    request = {}
                if isinstance(request, dict) and request.get('method') == 'cancel':
                    self._write(self.handle(line))
                    continue
                if isinstance(request, dict) and request.get('method') == 'run':
                    rid, params = request.get('id'), request.get('params', {})
                    if (request.get('jsonrpc') == '2.0' and isinstance(rid, (str, int, float))
                            and not isinstance(rid, bool) and _valid_run_params(params)):
                        timeout = params.get('timeout_ms', self.request_timeout_ms)
                        if type(timeout) is int and timeout > 0:
                            old = self.requests.get(rid)
                            if old is not None and not old.done.is_set():
                                self._write(_error(rid, -32600, 'Duplicate active request id'))
                                continue
                            self.requests[rid] = Request(rid, timeout)
                pending.put(line)
                if (isinstance(request, dict) and request.get('jsonrpc') == '2.0' and request.get('method') == 'shutdown'
                        and isinstance(request.get('params', {}), dict)
                        and not isinstance(request.get('id'), bool)
                        and isinstance(request.get('id'), (str, int, float, type(None)))):
                    break
        finally:
            pending.put(None)
            pending.join()
            executor.join()


def serve_stdio(default_project: Optional[str] = None, request_timeout_ms=30000, *, progress=False, default_session_dir=None) -> None:
    from ..cli import run_in_process
    from ..utils import kicad_backend as kb

    def dispatch(args, project):
        defaults = (["--session-dir", default_session_dir] if default_session_dir else [])
        if progress:
            defaults.append("--progress")
        return run_in_process([*defaults, *args], project or default_project)
    kb.enable_resident_worker()
    try:
        Server(dispatch, request_timeout_ms=request_timeout_ms, progress=progress).serve_forever()
    finally:
        kb.close_resident_worker()
