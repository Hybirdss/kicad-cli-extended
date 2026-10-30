"""Backend: find and invoke the real KiCad 10 tools on Windows, macOS and Linux.

Two engines are used, both hard dependencies:

* ``kicad-cli`` — every export, ERC, DRC, render, upgrade and jobset run.
* ``pcbnew`` (KiCad's SWIG Python module) — board editing kicad-cli cannot do.
  ``import pcbnew`` only works in the interpreter KiCad was built for (Linux:
  the distro ``python3``; Windows: ``<KiCad>\\bin\\python.exe``; macOS: the
  Python.framework inside KiCad.app), which is usually *not* the interpreter
  running this tool.  pcbnew work therefore always runs in a worker process
  (``utils/pcbnew_worker.py``) started with an interpreter that can import it;
  this also isolates SWIG crashes from the CLI process.

Discovery (first hit wins)
--------------------------
kicad-cli: ``$KICAD_CLI_EXTENDED_KICAD_CLI`` (alias ``$KICAD_CLI``) → ``PATH`` →
standard install locations (Windows ``%ProgramFiles%\\KiCad\\<ver>\\bin``,
``%LOCALAPPDATA%\\Programs\\KiCad\\<ver>\\bin``, newest version first; macOS
``/Applications/KiCad/KiCad.app/Contents/MacOS``; Linux ``/usr/bin``,
``/usr/local/bin``, snap) → Flatpak ``org.kicad.KiCad``.

pcbnew Python: ``$KICAD_CLI_EXTENDED_PYTHON`` → the current interpreter → the
interpreter bundled next to the discovered kicad-cli (Windows/macOS) → the
system ``python3`` (Linux) → Flatpak.  A candidate whose pcbnew major version
matches kicad-cli's is preferred.
"""

from __future__ import annotations

import functools
import glob
import json
import os
import re
import shutil
import subprocess
import signal
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

from .. import APP_NAME, ENV_PREFIX, env as app_env

#: KiCad major versions this release is built and tested against.
SUPPORTED_MAJORS = (10,)

INSTALL_HINT = (
    "KiCad 10 is required (kicad-cli + its pcbnew Python module).\n"
    "  Windows: installer from https://www.kicad.org/download/windows/  (or: winget install KiCad.KiCad)\n"
    "  macOS:   brew install --cask kicad   (or the .dmg from kicad.org)\n"
    "  Ubuntu:  sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad\n"
    "  Fedora:  sudo dnf install kicad      Arch: sudo pacman -S kicad kicad-library\n"
    "  Flatpak: flatpak install flathub org.kicad.KiCad\n"
    f"Overrides: {ENV_PREFIX}KICAD_CLI=<path to kicad-cli>, {ENV_PREFIX}PYTHON=<python that can import pcbnew>"
)

_NOISE = re.compile(r"^\d\d:\d\d:\d\d(?: [AP]M)?: Debug: .*$|^\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pcbnew_worker.py")

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
FLATPAK_ID = "org.kicad.KiCad"

# text-mode subprocess settings: KiCad prints UTF-8; never crash on odd bytes
_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


from ..core import runtime


class KicadError(RuntimeError):
    """A KiCad backend invocation failed. The message says what to do next."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# Platform discovery
# ---------------------------------------------------------------------------


def _version_key(path: str) -> List[int]:
    return [int(n) for n in re.findall(r"\d+", os.path.basename(os.path.normpath(path)))]


def _windows_kicad_roots() -> List[str]:
    r"""``<base>\KiCad\<version>`` install roots, newest version first."""
    bases = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(var):
            bases.append(os.environ[var])
    if os.environ.get("LOCALAPPDATA"):
        bases.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs"))
    bases.append(r"C:\Program Files")
    roots = []
    seen = set()
    for base in bases:
        kd = os.path.join(base, "KiCad")
        if not os.path.isdir(kd):
            continue
        for v in os.listdir(kd):
            root = os.path.join(kd, v)
            key = os.path.normcase(root)
            if key not in seen and os.path.isdir(os.path.join(root, "bin")):
                seen.add(key)
                roots.append(root)
    return sorted(roots, key=_version_key, reverse=True)


def _mac_app_dirs() -> List[str]:
    home = os.path.expanduser("~")
    cands = ["/Applications/KiCad/KiCad.app", "/Applications/KiCad.app",
             os.path.join(home, "Applications", "KiCad", "KiCad.app"), os.path.join(home, "Applications", "KiCad.app")]
    return [c for c in cands if os.path.isdir(c)]


@functools.lru_cache(maxsize=1)
def _flatpak_available() -> bool:
    fp = shutil.which("flatpak")
    if not fp:
        return False
    try:
        r = runtime.execute([fp, "info", FLATPAK_ID], capture_output=True, timeout=20, stdin=subprocess.DEVNULL,
                           **_TEXT)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cli_override() -> Optional[str]:
    return app_env("KICAD_CLI") or os.environ.get("KICAD_CLI")


@functools.lru_cache(maxsize=1)
def _discover_kicad_cli() -> Dict[str, Any]:
    override = _cli_override()
    if override:
        if os.path.isfile(override):
            return {"command": [override], "source": "env"}
        raise KicadError(f"{ENV_PREFIX}KICAD_CLI / KICAD_CLI points to a missing file: {override}\n" + INSTALL_HINT)
    exe = shutil.which("kicad-cli")
    if exe:
        return {"command": [exe], "source": "PATH"}
    cands: List[str] = []
    if IS_WINDOWS:
        cands += [os.path.join(r, "bin", "kicad-cli.exe") for r in _windows_kicad_roots()]
    elif IS_MAC:
        cands += [os.path.join(a, "Contents", "MacOS", "kicad-cli") for a in _mac_app_dirs()]
    else:
        cands += ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli", "/snap/bin/kicad-cli",
                  "/snap/bin/kicad.kicad-cli"]
    for c in cands:
        if os.path.isfile(c):
            return {"command": [c], "source": "default-location"}
    if not IS_WINDOWS and not IS_MAC and _flatpak_available():
        return {"command": [shutil.which("flatpak"), "run", "--command=kicad-cli", FLATPAK_ID], "source": "flatpak"}
    raise KicadError("kicad-cli not found (checked $" + ENV_PREFIX + "KICAD_CLI, PATH and the standard install "
                     "locations).\n" + INSTALL_HINT)


def kicad_cli_command() -> List[str]:
    """argv prefix that runs kicad-cli (several items for Flatpak)."""
    return list(_discover_kicad_cli()["command"])


def find_kicad_cli() -> str:
    return " ".join(kicad_cli_command())


def is_flatpak() -> bool:
    return _discover_kicad_cli()["source"] == "flatpak"


@functools.lru_cache(maxsize=1)
def kicad_version() -> str:
    r = runtime.execute([*kicad_cli_command(), "version"], capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=120, **_TEXT)
    lines = [l for l in clean_output(r.stdout).splitlines() if l.strip()]
    return lines[-1].strip() if lines else "unknown"


def kicad_major() -> int:
    m = re.match(r"(\d+)", kicad_version())
    return int(m.group(1)) if m else 0


def version_status() -> Dict[str, Any]:
    major = kicad_major()
    return {"kicad_version": kicad_version(), "supported": major in SUPPORTED_MAJORS,
            "supported_majors": list(SUPPORTED_MAJORS)}


@functools.lru_cache(maxsize=1)
def ensure_supported() -> None:
    """Fail early (with a clear message) on KiCad versions this release cannot drive."""
    major = kicad_major()
    if major and major < min(SUPPORTED_MAJORS):
        raise KicadError(f"KiCad {kicad_version()} found, but {APP_NAME} requires KiCad "
                         f"{'/'.join(map(str, SUPPORTED_MAJORS))}.x (file format and kicad-cli options differ).\n"
                         + INSTALL_HINT)


def clean_output(text: str) -> str:
    text = _ANSI.sub("", text or "")
    return "\n".join(l for l in text.splitlines() if not _NOISE.match(l))


def default_timeout() -> int:
    """Seconds after which a kicad-cli invocation is abandoned (``KCE_TIMEOUT``)."""
    try:
        return max(1, int(app_env("TIMEOUT", "1800")))
    except ValueError:
        return 1800


def run_kicad_cli(args: Sequence[str], cwd: Optional[str] = None, timeout: Optional[int] = None,
                  check: bool = True, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Run ``kicad-cli <args>``. Returns dict with returncode/stdout/stderr/command."""
    ensure_supported()
    if timeout is None:
        timeout = default_timeout()
    cmd = [*kicad_cli_command(), *[str(a) for a in args]]
    run_env = dict(os.environ)
    run_env.setdefault("KICAD_ALLOC_CONSOLE", "0")
    if env:
        run_env.update(env)
    try:
        r = runtime.execute(cmd, cwd=cwd, capture_output=True, timeout=runtime.remaining(timeout), env=run_env,
                           stdin=subprocess.DEVNULL, **_TEXT)
    except subprocess.TimeoutExpired as e:
        raise KicadError(f"kicad-cli timed out after {timeout}s: {' '.join(cmd)}") from e
    res = {"command": cmd, "returncode": r.returncode,
           "stdout": clean_output(r.stdout), "stderr": clean_output(r.stderr)}
    if check and r.returncode != 0:
        msg = res["stderr"] or res["stdout"] or "(no output)"
        raise KicadError(f"kicad-cli exited with code {r.returncode}: {' '.join(str(a) for a in args)}\n"
                         f"{msg.strip()}", res)
    return res


# ---------------------------------------------------------------------------
# Temporary files that KiCad must be able to read
# ---------------------------------------------------------------------------


def temp_root() -> Optional[str]:
    """Base dir for temp files handed to KiCad (None = the OS default).

    A Flatpak KiCad has a private /tmp, so files there are invisible to it;
    use a directory under the user's cache instead.
    """
    try:
        flat = is_flatpak()
    except KicadError:
        flat = False
    if flat:
        d = os.path.join(cache_dir(), "tmp")
        os.makedirs(d, exist_ok=True)
        return d
    return None


def mkdtemp(tag: str) -> str:
    return tempfile.mkdtemp(prefix=f"{APP_NAME}-{tag}-", dir=temp_root())


def mkstemp(tag: str, suffix: str = "") -> str:
    fd, path = tempfile.mkstemp(prefix=f"{APP_NAME}-{tag}-", suffix=suffix, dir=temp_root())
    os.close(fd)
    return path


def temporary_directory(tag: str) -> "tempfile.TemporaryDirectory[str]":
    return tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-{tag}-", dir=temp_root())


# ---------------------------------------------------------------------------
# pcbnew interpreter + worker
# ---------------------------------------------------------------------------


def _can_import_pcbnew(python) -> Optional[str]:
    cmd = list(python) if isinstance(python, (list, tuple)) else [python]
    try:
        run_env = dict(os.environ)
        run_env.pop("PYTHONPATH", None)
        run_env.pop("PYTHONHOME", None)
        r = runtime.execute([*cmd, "-I", "-W", "ignore", "-c", "import pcbnew; print(pcbnew.Version())"],
                           capture_output=True, timeout=120, stdin=subprocess.DEVNULL, env=run_env, **_TEXT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode == 0:
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return lines[-1].strip() if lines else "unknown"
    return None


def _pcbnew_python_candidates() -> List[str]:
    cands: List[str] = []
    override = app_env("PYTHON")
    if override:
        cands.append(override)
    cands.append(sys.executable)
    cli = None
    try:
        if not is_flatpak():
            cli = os.path.realpath(kicad_cli_command()[0])
    except KicadError:
        pass
    if IS_WINDOWS:
        if cli:
            cands.append(os.path.join(os.path.dirname(cli), "python.exe"))
        cands += [os.path.join(r, "bin", "python.exe") for r in _windows_kicad_roots()]
    elif IS_MAC:
        apps = _mac_app_dirs()
        if cli and ".app" in cli:
            apps.insert(0, cli[: cli.index(".app") + 4])
        for app in apps:
            fw = os.path.join(app, "Contents", "Frameworks", "Python.framework", "Versions")
            cands += sorted(glob.glob(os.path.join(fw, "*", "bin", "python3")), key=_version_key, reverse=True)
    else:
        cands.append("/usr/bin/python3")
        w = shutil.which("python3")
        if w:
            cands.append(w)
        cands.extend(sorted(glob.glob("/usr/bin/python3.[0-9]*"), key=_version_key, reverse=True))
    return cands


@functools.lru_cache(maxsize=1)
def find_pcbnew_python() -> Dict[str, Any]:
    """Locate an interpreter that can ``import pcbnew`` (env override, then per-OS search)."""
    tried: List[str] = []
    fallback = None
    try:
        want = kicad_major()
    except KicadError:
        want = 0
    for c in _pcbnew_python_candidates():
        if not c or c in tried or not os.path.isfile(c):
            continue
        tried.append(c)
        ver = _can_import_pcbnew(c)
        if not ver:
            continue
        found = {"python": c, "pcbnew_version": ver, "command": [c]}
        if not want or ver.split(".")[0] == str(want):
            return found
        fallback = fallback or found
    if not IS_WINDOWS and not IS_MAC and _flatpak_available():
        cmd = [shutil.which("flatpak"), "run", "--command=python3", FLATPAK_ID]
        ver = _can_import_pcbnew(cmd)
        tried.append(" ".join(cmd))
        if ver:
            return {"python": " ".join(cmd), "pcbnew_version": ver, "command": cmd}
    if fallback:
        fallback["warning"] = f"pcbnew {fallback['pcbnew_version']} does not match kicad-cli {kicad_version()}"
        return fallback
    raise KicadError(
        "No Python interpreter can `import pcbnew` (tried: " + (", ".join(tried) or "none") + ").\n"
        "pcbnew ships with KiCad: Linux = the distro python3 KiCad was built for; Windows = "
        "<KiCad>\\bin\\python.exe; macOS = KiCad.app/Contents/Frameworks/Python.framework/Versions/<v>/bin/python3.\n"
        f"Set {ENV_PREFIX}PYTHON to that interpreter to override.\n" + INSTALL_HINT)


def _worker_flags() -> List[str]:
    flags = ["-W", "ignore"]
    if os.environ.get("PYTHONWARNDEFAULTENCODING"):
        # propagate the encoding audit into the worker (its own code only)
        flags = ["-X", "warn_default_encoding", "-W", "ignore", "-W", "error::EncodingWarning:__main__"]
    return flags


def run_pcbnew(op: str, params: Optional[Dict[str, Any]] = None, timeout: int = 3600) -> Dict[str, Any]:
    """Run one pcbnew worker operation in the pcbnew-capable interpreter.

    Request and result travel through temp JSON files so pcbnew's console noise
    can never corrupt the protocol.  With a resident worker enabled (``serve``)
    the request goes to the long-lived process instead, and only the first call
    pays the startup.
    """
    ensure_supported()
    if _resident is not None:
        return _resident.run(op, params or {}, timeout)
    py_cmd = find_pcbnew_python()["command"]
    with temporary_directory("pcbnew") as td:
        req = os.path.join(td, "request.json")
        out = os.path.join(td, "result.json")
        with open(req, "w", encoding="utf-8") as f:
            json.dump({"op": op, "params": params or {}}, f)
        cmd = [*py_cmd, *_worker_flags(), "-I", WORKER_PATH, req, out]
        run_env = _worker_env()
        try:
            r = runtime.execute(cmd, capture_output=True, timeout=runtime.remaining(timeout), env=run_env,
                               stdin=subprocess.DEVNULL, **_TEXT)
        except subprocess.TimeoutExpired as e:
            raise KicadError(f"pcbnew worker '{op}' timed out after {timeout}s") from e
        if not os.path.exists(out):
            raise KicadError(f"pcbnew worker '{op}' crashed (exit {r.returncode}).\n" + clean_output(r.stderr)[-4000:],
                             {"stdout": clean_output(r.stdout)[-4000:], "returncode": r.returncode})
        with open(out, encoding="utf-8") as f:
            res = json.load(f)
    if not res.get("ok"):
        raise KicadError(f"pcbnew {op}: {res.get('error')}", {"traceback": res.get("traceback"), "worker_type": res.get("worker_type"), **(res.get("details") or {})})
    return res["result"]


def _worker_env() -> Dict[str, str]:
    run_env = dict(os.environ)
    run_env.pop("PYTHONPATH", None)
    run_env.pop("PYTHONHOME", None)
    return run_env


class ResidentWorker:
    """Keep one pcbnew interpreter alive; files isolate native console noise."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.starts = 0

    def start(self) -> None:
        started = time.monotonic()
        py_cmd = find_pcbnew_python()["command"]
        self.proc = subprocess.Popen(runtime.process_command([*py_cmd, *_worker_flags(), "-I", WORKER_PATH, "--serve"]),
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=_worker_env(), start_new_session=os.name != "nt", **_TEXT)
        self.starts += 1
        request = runtime.current.get()
        if request:
            request.meta.update(worker_started=True, worker_start_ms=round((time.monotonic() - started) * 1000, 3))

    def run(self, op: str, params: Dict[str, Any], timeout: int = 3600) -> Dict[str, Any]:
        request = runtime.current.get()
        if request:
            request.meta['worker_reused'] = self.proc is not None and self.proc.poll() is None
        if self.proc is None or self.proc.poll() is not None:
            self.close()
            self.start()
        if request:
            request.worker = self
            request.meta['worker_start_count'] = self.starts
            request.meta['worker_pid'] = self.proc.pid
        proc = self.proc
        assert proc is not None and proc.stdin is not None
        with temporary_directory("pcbnew") as td:
            req, out = os.path.join(td, "request.json"), os.path.join(td, "result.json")
            with open(req, "w", encoding="utf-8") as f:
                json.dump({"op": op, "params": params}, f)
            try:
                proc.stdin.write(json.dumps({"request": req, "result": out}) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.close(force=True)
                raise KicadError(f"pcbnew worker '{op}' crashed before the request was sent.",
                                 {"returncode": proc.poll()}) from e
            deadline = time.monotonic() + runtime.remaining(timeout)
            while not os.path.exists(out):
                try:
                    runtime.remaining(timeout)
                except KicadError:
                    self.close(force=True)
                    raise
                code = proc.poll()
                if code is not None:
                    self.close()
                    raise KicadError(f"pcbnew worker '{op}' crashed (exit {code}).", {"returncode": code})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close(force=True)
                    raise KicadError(f"pcbnew worker '{op}' timed out after {timeout}s")
                time.sleep(min(0.01, remaining))
            try:
                with open(out, encoding="utf-8") as f:
                    res = json.load(f)
                if not isinstance(res, dict) or not isinstance(res.get("ok"), bool) or (res["ok"] and "result" not in res):
                    raise ValueError("missing ok/result")
            except (ValueError, OSError) as e:
                self.close(force=True)
                raise KicadError(f"pcbnew worker '{op}' returned garbage: {e}") from e
        if not res["ok"]:
            raise KicadError(f"pcbnew {op}: {res.get('error')}", {"traceback": res.get("traceback"), "worker_type": res.get("worker_type"), **(res.get("details") or {})})
        return res["result"]

    def close(self, force: bool = False) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if force:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


_resident: Optional[ResidentWorker] = None


def enable_resident_worker(on: bool = True) -> None:
    """Route pcbnew operations through one long-lived worker (used by ``kce serve``)."""
    global _resident
    if on and _resident is None:
        _resident = ResidentWorker()
    elif not on and _resident is not None:
        _resident.close()
        _resident = None


def close_resident_worker() -> None:
    enable_resident_worker(False)


# ---------------------------------------------------------------------------
# KiCad data directories and path variables
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def kicad_share_dir() -> Optional[str]:
    """KiCad's shared data dir (symbols/, footprints/, template/...) for this OS, or None."""
    cands = [app_env("SHARE_DIR")]
    try:
        disc = _discover_kicad_cli()
    except KicadError:
        disc = {"command": [], "source": None}
    cli = os.path.realpath(disc["command"][0]) if disc["command"] and disc["source"] != "flatpak" else None
    if IS_WINDOWS:
        if cli:
            cands.append(os.path.join(os.path.dirname(os.path.dirname(cli)), "share", "kicad"))
        cands += [os.path.join(r, "share", "kicad") for r in _windows_kicad_roots()]
    elif IS_MAC:
        if cli and ".app" in cli:
            cands.append(os.path.join(cli[: cli.index(".app") + 4], "Contents", "SharedSupport"))
        cands += [os.path.join(a, "Contents", "SharedSupport") for a in _mac_app_dirs()]
        cands.append("/Library/Application Support/kicad")
    else:
        if cli:
            cands.append(os.path.join(os.path.dirname(os.path.dirname(cli)), "share", "kicad"))
        cands += ["/usr/share/kicad", "/usr/local/share/kicad",
                  "/var/lib/flatpak/app/org.kicad.KiCad/current/active/files/share/kicad",
                  os.path.join(os.path.expanduser("~"), ".local", "share", "flatpak", "app", FLATPAK_ID, "current",
                               "active", "files", "share", "kicad"),
                  "/snap/kicad/current/usr/share/kicad"]
    for p in cands:
        if p and os.path.isdir(p) and (os.path.isdir(os.path.join(p, "template"))
                                       or os.path.isdir(os.path.join(p, "symbols"))):
            return p
    return None


def kicad_config_dir() -> str:
    """KiCad's per-user config dir (``<root>/<major>.0``); ``$KICAD_CONFIG_HOME`` replaces the root."""
    try:
        major = kicad_major() or SUPPORTED_MAJORS[-1]
    except KicadError:
        major = SUPPORTED_MAJORS[-1]
    base = os.environ.get("KICAD_CONFIG_HOME")
    home = os.path.expanduser("~")
    if base:  # KiCad appends <major>.<minor> to it as well (common/paths.cpp, GetUserSettingsPath)
        root = base
    elif IS_MAC:
        root = os.path.join(home, "Library", "Preferences", "kicad")
    elif IS_WINDOWS:
        root = os.path.join(os.environ.get("APPDATA", home), "kicad")
    else:
        root = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), "kicad")
        flat = os.path.join(home, ".var", "app", FLATPAK_ID, "config", "kicad")
        if not os.path.isdir(os.path.join(root, f"{major}.0")) and os.path.isdir(os.path.join(flat, f"{major}.0")):
            root = flat
    return os.path.join(root, f"{major}.0")


def _user_env_vars() -> Dict[str, str]:
    """Path variables set in KiCad's kicad_common.json (Preferences > Configure Paths)."""
    p = os.path.join(kicad_config_dir(), "kicad_common.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in ((data.get("environment") or {}).get("vars") or {}).items() if v}
    except (OSError, ValueError):
        return {}


_DIR_KINDS = {"SYMBOL": "symbols", "FOOTPRINT": "footprints", "3DMODEL": "3dmodels", "TEMPLATE": "template"}


def kicad_env_vars(project_dir: Optional[str] = None) -> Dict[str, str]:
    """Path variables KiCad expands in lib tables and 3D model paths (``KICAD10_SYMBOL_DIR`` ...).

    Names carry the KiCad major version.  Precedence: process environment >
    kicad_common.json > the install's defaults.
    """
    try:
        major = kicad_major() or SUPPORTED_MAJORS[-1]
    except KicadError:
        major = SUPPORTED_MAJORS[-1]
    share = kicad_share_dir()
    out: Dict[str, str] = {}
    if share:
        for kind, sub in _DIR_KINDS.items():
            out[f"KICAD{major}_{kind}_DIR"] = os.path.join(share, sub)
    out["KICAD_USER_TEMPLATE_DIR"] = os.path.join(kicad_config_dir(), "template")
    out.update(_user_env_vars())
    for k, v in os.environ.items():
        if re.fullmatch(r"KICAD\d*_\w+_DIR", k):
            out[k] = v
    if project_dir:
        out["KIPRJMOD"] = os.path.abspath(project_dir)
    return out


def kicad_dir(kind: str) -> str:
    """Resolved data dir for kind SYMBOL | FOOTPRINT | 3DMODEL | TEMPLATE."""
    try:
        major = kicad_major() or SUPPORTED_MAJORS[-1]
    except KicadError:
        major = SUPPORTED_MAJORS[-1]
    v = kicad_env_vars().get(f"KICAD{major}_{kind}_DIR")
    if v and os.path.isdir(v):
        return v
    raise KicadError(f"KiCad {kind.lower()} directory not found. Set KICAD{major}_{kind}_DIR (or "
                     f"{ENV_PREFIX}SHARE_DIR to KiCad's share/kicad data directory).\n" + INSTALL_HINT)


def template_dir() -> str:
    return kicad_dir("TEMPLATE")


def symbol_dir() -> str:
    return kicad_dir("SYMBOL")


def footprint_dir() -> str:
    return kicad_dir("FOOTPRINT")


def expand_vars(path: str, variables: Dict[str, str]) -> str:
    def rep(m):
        name = m.group(1) or m.group(2)
        return variables.get(name, os.environ.get(name, m.group(0)))
    return re.sub(r"\$\{(\w+)\}|\$\((\w+)\)", rep, path)


# ---------------------------------------------------------------------------
# Per-user directories of this tool
# ---------------------------------------------------------------------------


def data_dir() -> str:
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA", home)
    elif IS_MAC:
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    return os.path.join(base, APP_NAME)


def cache_dir() -> str:
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        base = os.path.join(os.environ.get("LOCALAPPDATA", home), "cache")
    elif IS_MAC:
        base = os.path.join(home, "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache"))
    return os.path.join(base, APP_NAME)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def find_freerouting() -> Optional[Dict[str, str]]:
    """Locate Freerouting (optional external autorouter, needs Java)."""
    jar = os.environ.get("FREEROUTING_JAR")
    exe = shutil.which("freerouting")
    java = os.environ.get("JAVA") or shutil.which("java")
    if exe:
        return {"command": exe}
    if jar and os.path.isfile(jar) and java:
        return {"java": java, "jar": jar}
    local = os.path.join(data_dir(), "freerouting")
    jars = sorted(glob.glob(os.path.join(local, "*.jar")))
    javas = sorted(glob.glob(os.path.join(local, "jre*", "bin", "java.exe" if IS_WINDOWS else "java")))
    if jars and (java or javas):
        return {"java": java or javas[-1], "jar": jars[-1]}
    return None


def doctor() -> Dict[str, Any]:
    """Report what was found on this machine (``kce info doctor``)."""
    import platform
    from .. import __version__
    res: Dict[str, Any] = {"tool": APP_NAME, "version": __version__, "os": platform.system(),
                           "platform": sys.platform, "python": sys.executable, "python_version": platform.python_version()}
    try:
        disc = _discover_kicad_cli()
        res["kicad_cli"] = " ".join(disc["command"])
        res["kicad_cli_source"] = disc["source"]
        res.update(version_status())
    except KicadError as e:
        res["kicad_cli_error"] = str(e)
    try:
        f = find_pcbnew_python()
        res["pcbnew_python"] = f["python"]
        res["pcbnew_version"] = f["pcbnew_version"]
        if f.get("warning"):
            res.setdefault("warnings", []).append(f["warning"])
    except KicadError as e:
        res["pcbnew_error"] = str(e)
    res["share_dir"] = kicad_share_dir()
    res["path_variables"] = dict(sorted(kicad_env_vars().items()))
    for kind in _DIR_KINDS:
        try:
            res[kind.lower() + "_dir"] = kicad_dir(kind)
        except KicadError as e:
            res[kind.lower() + "_dir"] = None
            if kind != "3DMODEL":
                res.setdefault("warnings", []).append(str(e).splitlines()[0])
    try:
        res["symbol_libraries"] = len(glob.glob(os.path.join(symbol_dir(), "*.kicad_sym")))
        res["footprint_libraries"] = len(glob.glob(os.path.join(footprint_dir(), "*.pretty")))
        tdir = template_dir()
        res["templates"] = sorted(d for d in os.listdir(tdir) if os.path.isdir(os.path.join(tdir, d)))
    except (KicadError, OSError):
        pass
    cfg = kicad_config_dir()
    res["config_dir"] = cfg
    res["global_sym_lib_table"] = os.path.exists(os.path.join(cfg, "sym-lib-table"))
    res["global_fp_lib_table"] = os.path.exists(os.path.join(cfg, "fp-lib-table"))
    # KiCad 10's GUI shows its setup wizard until this third table exists too
    res["global_design_block_lib_table"] = os.path.exists(os.path.join(cfg, "design-block-lib-table"))
    res["freerouting"] = find_freerouting()
    from ..core import gui as gui_mod, sim as sim_mod  # optional engines (lazy: they import this module)
    res["ngspice"] = sim_mod.find_ngspice()
    res["kicad_ipc_api"] = gui_mod.availability()
    models = bool(res.get("3dmodel_dir"))
    display = bool(IS_WINDOWS or IS_MAC or os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    res['capabilities'] = {'step': bool(res.get('kicad_cli') and models),
                           'render': bool(res.get('kicad_cli') and display and models),
                           'freerouting': bool(res.get('freerouting')), 'ngspice': bool(res.get('ngspice')),
                           'display': display}
    warnings = [{'id': 'backend_warning', 'effect': str(w), 'fix': 'kce info doctor'}
                if not isinstance(w, dict) else w for w in res.get('warnings', [])]
    if not models:
        fix = ('KiCad installer: enable the 3D model library' if IS_WINDOWS else
               'Install KiCad 3D packages (Linux: kicad-packages3d) and set KICAD10_3DMODEL_DIR')
        warnings.insert(0, {'id': '3dmodels_missing', 'effect': 'STEP/VRML/render can show a bare PCB', 'fix': fix})
    if not display:
        warnings.append({'id': 'display_missing', 'effect': '3D rendering may require a display',
                         'fix': 'Use xvfb-run -a kce pcb render on Linux; editing and ERC/DRC are headless'})
    res['warnings'] = warnings
    res["ok"] = bool(res.get("kicad_cli") and res.get("pcbnew_python") and res.get("supported")
                     and res.get("symbol_dir"))
    return res


def _diagnosed_call(function, backend):
    @functools.wraps(function)
    def diagnosed(*args, **kwargs):
        started = time.monotonic()
        operation = args[0] if args else kwargs.get('op', 'unknown')
        if not isinstance(operation, str):
            operation = ' '.join(map(str, operation[:3]))
        try:
            return function(*args, **kwargs)
        except KicadError as exc:
            request = runtime.current.get()
            pid = request.meta.get('worker_pid') if request else None
            extra = runtime.details(backend, operation, pid=pid, returncode=exc.details.get('returncode'),
                                    timeout=kwargs.get('timeout'), started=started)
            extra.update(exc.details)
            extra['retryable'] = ('timed out' in str(exc).lower() or 'crashed' in str(exc).lower())
            exc.details = extra
            raise
    return diagnosed


run_kicad_cli = _diagnosed_call(run_kicad_cli, 'kicad-cli')
run_pcbnew = _diagnosed_call(run_pcbnew, 'pcbnew')
