"""Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0), enough for the benchmark.

Implements initialize / notifications/initialized / tools/list (with pagination) /
resources/list / resource templates / prompts/list / tools/call.  Keeps the raw JSON so the
benchmark counts exactly what a client receives.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
from pathlib import Path


class McpError(RuntimeError):
    pass


class StdioClient:
    def __init__(self, command: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None):
        self.command = command
        self.cwd = cwd
        self.env = dict(os.environ, **(env or {}))
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._q: "queue.Queue[dict]" = queue.Queue()
        self.stderr: List[str] = []
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.startup_seconds = 0.0

    # -- transport --------------------------------------------------------
    def start(self, timeout: float = 300) -> None:
        t0 = time.perf_counter()
        command = self.command
        if os.name == 'nt':
            launcher = Path(__file__).resolve().parents[1] / 'src/kicad_cli_extended/utils/windows_job.py'
            command = [sys.executable, '-I', str(launcher), json.dumps(command)]
        self.proc = subprocess.Popen(command, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        res = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "kce-bench", "version": "1"}}, timeout)
        self.server_info = res.get("serverInfo", {})
        self.capabilities = res.get("capabilities", {})
        self.notify("notifications/initialized", {})
        self.startup_seconds = time.perf_counter() - t0

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except ValueError:
                self.stderr.append("[non-json stdout] " + line[:500])

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def _send(self, msg: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Optional[dict] = None, timeout: float = 600) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise McpError(f"timeout waiting for {method}")
            try:
                msg = self._q.get(timeout=min(left, 1.0))
            except queue.Empty:
                if self.proc and self.proc.poll() is not None:
                    raise McpError(f"server exited ({self.proc.returncode}) during {method}: "
                                   + "\n".join(self.stderr[-20:]))
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise McpError(f"{method}: {msg['error']}")
                return msg["result"]
            if "method" in msg and "id" in msg:      # server -> client request (e.g. roots/list)
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {} if msg["method"] != "roots/list"
                            else {"roots": []}})

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
                self.proc.wait()

    # -- MCP methods ------------------------------------------------------
    def _paged(self, method: str, key: str) -> List[dict]:
        items, cursor = [], None
        while True:
            res = self.request(method, {"cursor": cursor} if cursor else {})
            items += res.get(key, [])
            cursor = res.get("nextCursor")
            if not cursor:
                return items

    def list_tools(self) -> List[dict]:
        return self._paged("tools/list", "tools")

    def list_resources(self) -> List[dict]:
        return self._paged("resources/list", "resources") if "resources" in self.capabilities else []

    def list_resource_templates(self) -> List[dict]:
        if "resources" not in self.capabilities:
            return []
        try:
            return self._paged("resources/templates/list", "resourceTemplates")
        except McpError:
            return []

    def list_prompts(self) -> List[dict]:
        return self._paged("prompts/list", "prompts") if "prompts" in self.capabilities else []

    def call_tool(self, name: str, arguments: dict, timeout: float = 1800) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
