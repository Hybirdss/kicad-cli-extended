"""Session: project state, in-memory documents, undo/redo, persistence.

State model
-----------
The KiCad project files themselves are the source of truth.  A session holds
the documents a command has loaded (parsed S-expression trees or raw text),
tracks which are dirty, and on save writes them back atomically and validates
each one by re-loading it through the real ``kicad-cli``.

Undo/redo works across one-shot invocations: before every mutation the prior
content of each affected file is stored (gzip, content-addressed) under
``<project>/<STATE_DIR>/blobs`` and the history lives in
``<project>/<STATE_DIR>/session.json`` (saved with
``_locked_save_json``).  Mutations take an exclusive ``flock`` on
``<state>/lock`` so concurrent agents cannot interleave edits.
"""

from __future__ import annotations

import copy
import contextlib
import errno
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import PurePath
from typing import Any, Callable, Dict, List, Optional, Union

from .. import APP_NAME, STATE_DIR, env
from ..utils.paths import rel_inside
from . import sexpr

STATE_SUBDIR = STATE_DIR
MAX_UNDO = 100


def state_directory(project_dir, override=None):
    base = override or env("SESSION_DIR", "")
    if base:
        if rel_inside(os.path.realpath(base), os.path.realpath(project_dir)) is not None:
            raise ValueError("A custom session directory must be outside the project; use --session-dir DIR")
        # Separate projects even when a controller uses one state root for many trials.
        key = hashlib.sha256(os.path.normcase(os.path.realpath(project_dir)).encode("utf-8")).hexdigest()[:20]
        return os.path.join(os.path.abspath(base), key)
    return os.path.join(project_dir, STATE_SUBDIR)


class ValidationError(RuntimeError):
    """KiCad rejected the saved files; every write of that command was rolled back."""


def _lock_file(f, exclusive: bool = True, blocking: bool = True) -> bool:
    """Cross-platform advisory lock (fcntl on POSIX, msvcrt on Windows).

    Returns True if locked, False if the platform/filesystem has no locking.
    Raises BlockingIOError when ``blocking`` is False and the lock is held.
    """
    try:
        import fcntl
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(f.fileno(), flags)
        return True
    except ImportError:
        pass
    except OSError as e:
        if isinstance(e, BlockingIOError):
            raise
        return False
    try:
        import msvcrt
        pos = f.tell()
        f.seek(0)
        try:
            mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
            while True:
                try:
                    msvcrt.locking(f.fileno(), mode, 1)
                    return True
                except OSError as e:
                    if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if not blocking:
                        raise BlockingIOError("file is locked")
                    time.sleep(0.05)
        finally:
            f.seek(pos)
    except ImportError:
        return False


def _unlock_file(f) -> None:
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    except OSError:
        return
    try:
        import msvcrt
        pos = f.tell()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.seek(pos)
    except ImportError:
        pass


def _locked_save_json(path: str, data: Any, **dump_kwargs: Any) -> None:
    """Persist JSON with exclusive locking (open r+, lock, truncate inside lock)."""
    try:
        f = open(path, "r+", encoding="utf-8", newline="\n")
    except FileNotFoundError:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        f = open(path, "w", encoding="utf-8", newline="\n")
    with f:
        _locked = _lock_file(f, exclusive=True)
        try:
            f.seek(0)
            f.truncate()
            json.dump(data, f, **dump_kwargs)
            f.flush()
        finally:
            if _locked:
                _unlock_file(f)


def _locked_load_json(path: str) -> Optional[Dict]:
    try:
        f = open(path, "r", encoding="utf-8")
    except FileNotFoundError:
        return None
    with f:
        locked = _lock_file(f, exclusive=False)
        try:
            txt = f.read()
        finally:
            if locked:
                _unlock_file(f)
    if not txt.strip():
        return None
    return json.loads(txt)


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: str, data: bytes) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{APP_NAME}-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
        if os.name != 'nt':
            directory_fd = os.open(d, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Document:
    """A loaded KiCad file (S-expression tree or raw text/JSON)."""

    def __init__(self, path: str, kind: str):
        self.path = os.path.abspath(path)
        self.kind = kind  # "sexpr" | "text" | "json"
        self._tree: Optional[sexpr.SList] = None
        self._text: Optional[str] = None
        self._bytes: Optional[bytes] = None
        self._json: Optional[Any] = None
        self.dirty = False
        self.exists_on_disk = os.path.exists(self.path)
        self.disk_hash = _file_hash(self.path) if self.exists_on_disk else None

    # lazy loaders
    def bytes(self) -> bytes:
        if self._bytes is not None:
            return self._bytes
        if self.dirty:
            return self.text().encode("utf-8")
        with open(self.path, "rb") as f:
            return f.read()

    def set_bytes(self, data: bytes) -> None:
        self.set_text("")
        self._bytes = data

    def text(self) -> str:
        if self._bytes is not None:
            return self._bytes.decode("utf-8")
        if self._tree is not None and self.kind == "sexpr" and self.dirty:
            return sexpr.dumps(self._tree, sexpr.mode_for_path(self.path))
        if self._json is not None and self.kind == "json" and self.dirty:
            return json.dumps(self._json, indent=2, ensure_ascii=False) + "\n"
        if self._text is None:
            if not os.path.exists(self.path):
                raise FileNotFoundError(f"File not found: {self.path}")
            with open(self.path, encoding="utf-8") as f:
                self._text = f.read()
        return self._text

    def tree(self) -> sexpr.SList:
        if self._tree is None:
            self._tree = sexpr.loads(self.text())
        return self._tree

    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.text())
        return self._json

    def set_text(self, text: str) -> None:
        self._bytes = None
        self._deleted = False
        self._text = text
        self._tree = None
        self._json = None
        self.dirty = True

    def set_tree(self, tree: sexpr.SList) -> None:
        self._bytes = None
        self._deleted = False
        self._tree = tree
        self._text = None
        self.dirty = True

    def mark_dirty(self) -> None:
        # the tree/json is the authority now
        if self._tree is not None or self._json is not None:
            self._bytes = None
            self._text = None
        self.dirty = True


def _file_hash(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


class Session:
    """Holds the open project, loaded documents and undo/redo history."""

    def __init__(self) -> None:
        self.session_dir = None
        self.project = None  # core.project.KicadProject
        self.docs: Dict[str, Document] = {}
        self._undo: List[Dict] = []
        self._redo: List[Dict] = []
        self._modified = False
        self._lock_fh = None
        self.validate = env("NO_VALIDATE", "") != "1"
        self.last_validation: List[Dict] = []
        self._defer_save = False
        self._hold_lock = False

    # ------------------------------------------------------------------ project
    @property
    def project_path(self) -> Optional[str]:
        return self.project.pro_path if self.project else None

    def has_project(self) -> bool:
        return self.project is not None

    def set_project(self, project) -> None:
        if self._defer_save and self.project is not None and self.project.pro_path != project.pro_path:
            raise ValueError("Cannot switch project inside a transaction; use kce run's project option")
        if self.project is not None and self.project.pro_path != project.pro_path:
            self.release_lock()
        self.project = project
        self.project.session_state_dir = self.state_dir
        self.docs.clear()
        self._modified = False
        st = _locked_load_json(self.session_file) if self.state_dir else None
        self._undo = (st or {}).get("undo", [])
        self._redo = (st or {}).get("redo", [])

    def require_project(self):
        if self.project is None:
            raise RuntimeError(
                "No KiCad project loaded. Pass --project <file.kicad_pro|dir>, run inside a "
                "project directory, or create one with `kce project new <dir> --name NAME`.")
        return self.project

    @property
    def state_dir(self) -> Optional[str]:
        if self.project is None:
            return None
        return state_directory(self.project.dir, self.session_dir)

    @property
    def session_file(self) -> Optional[str]:
        sd = self.state_dir
        return os.path.join(sd, "session.json") if sd else None

    # ---------------------------------------------------------------- documents
    @staticmethod
    def key(path: str) -> str:
        """Dictionary key for a path (case-insensitive on Windows)."""
        return os.path.normcase(os.path.abspath(path))

    def has_doc(self, path: str) -> bool:
        return self.key(path) in self.docs

    def doc(self, path: str, kind: Optional[str] = None) -> Document:
        ap = os.path.abspath(path)
        if self._defer_save:
            from .plan import transaction_path
            if not transaction_path(ap, self.project.dir):
                raise ValueError(f"Plan files must stay inside the project and outside VCS/session metadata: {ap}; use `kce run --help`.")
        k = self.key(ap)
        if k not in self.docs:
            if kind is None:
                suffix = os.path.splitext(ap)[1].lower()
                kind = "json" if suffix in (".kicad_pro", ".kicad_jobset", ".json") else (
                    "text" if suffix == ".kicad_dru" else "sexpr")
            self.docs[k] = Document(ap, kind)
        return self.docs[k]

    def tree(self, path: str) -> sexpr.SList:
        return self.doc(path).tree()

    def current_text(self, path: str) -> Optional[str]:
        ap = os.path.abspath(path)
        if self.key(ap) in self.docs:
            d = self.docs[self.key(ap)]
            if d.dirty or d._text is not None or d._tree is not None or d._json is not None:
                try:
                    return d.text()
                except FileNotFoundError:
                    return None
        if os.path.exists(ap):
            with open(ap, encoding="utf-8") as f:
                return f.read()
        return None

    def dirty_docs(self) -> List[Document]:
        return [d for d in self.docs.values() if d.dirty]

    # ---------------------------------------------------------------- locking
    def _acquire_lock(self) -> None:
        if self._lock_fh is not None or self.state_dir is None:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        ignore = os.path.join(self.state_dir, ".gitignore")
        if not os.path.exists(ignore):
            atomic_write_text(ignore, "*\n")
        fh = open(os.path.join(self.state_dir, "lock"), "a+", encoding="utf-8")
        deadline = time.time() + float(env("LOCK_TIMEOUT", "120"))
        while True:
            try:
                _lock_file(fh, exclusive=True, blocking=False)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    fh.close()
                    raise RuntimeError(
                        f"Project is locked by another {APP_NAME} process "
                        f"({os.path.join(self.state_dir, 'lock')}). Retry when it finishes.")
                time.sleep(0.1)
        self._lock_fh = fh
        # re-sync history (another process may have written it while we waited)
        st = _locked_load_json(self.session_file)
        if st:
            self._undo = st.get("undo", [])
            self._redo = st.get("redo", [])
        for d in self.docs.values():
            if not d.dirty and d.exists_on_disk and _file_hash(d.path) != d.disk_hash:
                raise RuntimeError(
                    f"{d.path} changed on disk while this command was running; re-run the command.")

    def release_lock(self) -> None:
        if self._hold_lock:
            return
        if self._lock_fh is not None:
            _unlock_file(self._lock_fh)
            self._lock_fh.close()
            self._lock_fh = None

    @contextlib.contextmanager
    def transaction(self, validator=None, *, dry_run=False):
        """Hold the project lock until the final save or a byte-for-byte rollback."""
        from .plan import snapshot_files, restore_files, transaction_path

        if self._defer_save:
            raise ValueError("Nested transactions are not supported; use `kce run --help`.")
        project = self.require_project()
        if any(not transaction_path(d.path, project.dir) for d in self.dirty_docs()):
            raise ValueError("Save or discard edits outside the project or to metadata before a transaction; use `kce session save`.")
        self._acquire_lock()
        self._hold_lock = True
        try:
            before = snapshot_files(self.project.dir, include_state=True)
            history_path = self.session_file
            external_history = history_path if rel_inside(history_path, self.project.dir) is None else None
            history_before = self.current_bytes(history_path) if external_history else None
            def restore_snapshot():
                restore_files(self.project.dir, before, include_state=True)
                if external_history:
                    if history_before is None:
                        if os.path.exists(external_history):
                            os.unlink(external_history)
                    else:
                        atomic_write_bytes(external_history, history_before)
            state = copy.deepcopy((self.docs, self._undo, self._redo, self._modified,
                                   getattr(self, "_tx_undo_len", None), self.last_validation))
            self._defer_save = True
            result = {"saved": [], "validation": []}
            try:
                yield result
                self._defer_save = False
                if not dry_run:
                    after = snapshot_files(self.project.dir, include_state=True)
                    changed = sorted(rel for rel in before.keys() | after.keys()
                                     if not rel.startswith(STATE_DIR + "/") and before.get(rel) != after.get(rel))
                    paths = [os.path.join(self.project.dir, rel.replace("/", os.sep)) for rel in changed]
                    if paths:
                        self.snapshot("transaction file writes", paths)
                        self._undo[-1]["files"] = {rel: self._put_blob(before.get(rel)) for rel in changed}
                    result.update(self.save_session(validator, validation_paths=paths))
                    result["saved"] = list(dict.fromkeys([*result["saved"], *paths]))
                    for path in paths:
                        self.docs.pop(self.key(path), None)
                    return
            except BaseException as original:
                try:
                    restore_snapshot()
                except BaseException as rollback_error:
                    rollback_error.rollback_failed = True
                    rollback_error.rollback_cause = original
                    raise rollback_error from original
                finally:
                    (self.docs, self._undo, self._redo, self._modified,
                     self._tx_undo_len, self.last_validation) = state
                raise
            restore_snapshot()
            (self.docs, self._undo, self._redo, self._modified,
             self._tx_undo_len, self.last_validation) = state
        finally:
            self._defer_save = False
            self._hold_lock = False
            self.release_lock()

    def current_bytes(self, path: str) -> Optional[bytes]:
        if self.has_doc(path):
            doc = self.doc(path)
            if getattr(doc, "_deleted", False):
                return None
            if doc.dirty:
                return doc.bytes()
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # ---------------------------------------------------------------- blobs
    def _blob_dir(self) -> str:
        d = os.path.join(self.state_dir, "blobs")
        os.makedirs(d, exist_ok=True)
        return d

    def _put_blob(self, text: Optional[Union[str, bytes]]) -> Optional[str]:
        if text is None:
            return None
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        h = hashlib.sha256(data).hexdigest()
        p = os.path.join(self._blob_dir(), h + ".gz")
        if not os.path.exists(p):
            with gzip.open(p, "wb") as f:
                f.write(data)
        return h

    def _get_blob(self, h: Optional[str]) -> Optional[str]:
        data = self._get_blob_bytes(h)
        return data.decode("utf-8") if data is not None else None

    def _get_blob_bytes(self, h: Optional[str]) -> Optional[bytes]:
        if h is None:
            return None
        p = os.path.join(self._blob_dir(), h + ".gz")
        with gzip.open(p, "rb") as f:
            return f.read()

    # ---------------------------------------------------------------- undo/redo
    def snapshot(self, description: str, paths: List[str]) -> None:
        """Record the current content of ``paths`` before mutating them."""
        self.require_project()
        self._acquire_lock()
        files = {}
        for p in paths:
            ap = os.path.abspath(p)
            files[self._rel(ap)] = self._put_blob(self.current_bytes(ap))
        before = {"tx": getattr(self, "_tx_undo_len", None), "redo": list(self._redo),
                  "modified": self._modified, "dropped": None}
        if before["tx"] is None:
            self._tx_undo_len = len(self._undo)
        entry = {"id": uuid.uuid4().hex[:12], "description": description,
                 "timestamp": time.time(), "files": files}
        self._undo.append(entry)
        if len(self._undo) > MAX_UNDO:
            before["dropped"] = self._undo.pop(0)
        self._redo.clear()
        self._modified = True
        self._before_snapshot = before

    def _rel(self, ap: str) -> str:
        rel = rel_inside(ap, self.project.dir)
        return ap if rel is None else PurePath(rel).as_posix()  # portable history entries

    def _abs(self, rel: str) -> str:
        return rel if os.path.isabs(rel) else os.path.join(self.project.dir, rel)

    def _restore(self, entry: Dict) -> Dict:
        """Swap file contents with the entry's blobs; return the inverse entry."""
        inverse_files = {}
        for rel, h in entry["files"].items():
            ap = self._abs(rel)
            inverse_files[rel] = self._put_blob(self.current_bytes(ap))
            content = self._get_blob_bytes(h)
            d = self.doc(ap)
            if content is None:
                d.set_text("")
                d._deleted = True  # type: ignore[attr-defined]
            else:
                d.set_bytes(content)
        return {"id": entry["id"], "description": entry["description"],
                "timestamp": time.time(), "files": inverse_files}

    def undo(self) -> Optional[Dict]:
        self.require_project()
        self._acquire_lock()
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(self._restore(entry))
        self._modified = True
        return entry

    def redo(self) -> Optional[Dict]:
        self.require_project()
        self._acquire_lock()
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(self._restore(entry))
        self._modified = True
        return entry

    def rollback_last_snapshot(self) -> bool:
        """Drop the most recent snapshot: the edit it guarded never reached the documents."""
        before = getattr(self, "_before_snapshot", None)
        if not self._undo or before is None:
            return False
        self._undo.pop()
        if before["dropped"] is not None:
            self._undo.insert(0, before["dropped"])
        self._redo[:] = before["redo"]
        self._tx_undo_len = before["tx"]
        self._modified = before["modified"]
        self._before_snapshot = None
        return True

    def list_history(self) -> List[Dict]:
        out = []
        for i, e in reversed(list(enumerate(self._undo))):
            out.append({"index": i, "id": e["id"], "description": e["description"],
                        "timestamp": e["timestamp"], "files": sorted(e["files"])})
        return out

    def redo_list(self) -> List[Dict]:
        return [{"id": e["id"], "description": e["description"], "files": sorted(e["files"])}
                for e in reversed(self._redo)]

    # ---------------------------------------------------------------- saving
    def save_session(self, validator: Optional[Callable[[List[str]], List[Dict]]] = None,
                     *, validation_paths: Optional[List[str]] = None) -> Dict:
        """Write dirty documents atomically, validate through kicad-cli, persist history.

        If validation fails every written file is restored to its previous
        content and a RuntimeError describing KiCad's complaint is raised.
        """
        self.require_project()
        if self._defer_save:
            return {"saved": [], "validation": []}
        dirty = self.dirty_docs()
        written: List[str] = []
        previous: Dict[str, Optional[bytes]] = {}
        if dirty:
            self._acquire_lock()
        try:
            for d in dirty:
                previous[d.path] = None
                if os.path.exists(d.path):
                    with open(d.path, "rb") as f:
                        previous[d.path] = f.read()
                if getattr(d, "_deleted", False):
                    if os.path.exists(d.path):
                        os.unlink(d.path)
                else:
                    atomic_write_bytes(d.path, d.bytes())
                written.append(d.path)
        except BaseException:
            try:
                for p in reversed(written):
                    if previous[p] is None:
                        os.unlink(p)
                    else:
                        atomic_write_bytes(p, previous[p])
            finally:
                self.release_lock()
            raise
        self.last_validation = []
        to_validate = list(dict.fromkeys([*written, *(validation_paths or [])]))
        if to_validate and self.validate and validator is not None:
            try:
                self.last_validation = validator(to_validate)
                from .runtime import remaining
                remaining(10**9)
            except Exception as e:
                for p, txt in previous.items():
                    if txt is None:
                        if os.path.exists(p):
                            os.unlink(p)
                    else:
                        atomic_write_bytes(p, txt)
                tx = getattr(self, "_tx_undo_len", None)
                if tx is not None:
                    del self._undo[tx:]
                self._tx_undo_len = None
                for d in dirty:
                    self.docs.pop(self.key(d.path), None)
                self._persist_history()
                self.release_lock()
                from ..utils.kicad_backend import KicadError
                if isinstance(e, KicadError) and 'timed out' in str(e).lower():
                    raise
                raise ValidationError(
                    f"KiCad rejected the edited file(s); changes were rolled back.\n{e}") from e
        for d in dirty:
            d.dirty = False
            d.exists_on_disk = os.path.exists(d.path)
            d.disk_hash = _file_hash(d.path)
            d._text = None if d.kind != "text" else d._text
        self._persist_history()
        self._modified = False
        self._tx_undo_len = None
        self.release_lock()
        return {"saved": written, "validation": self.last_validation}

    def _persist_history(self) -> None:
        if self.session_file is None:
            return
        _locked_save_json(self.session_file, {
            "version": 1,
            "project": self.project.pro_path if self.project else None,
            "updated": time.time(),
            "undo": self._undo,
            "redo": self._redo,
        }, indent=1)

    def discard(self) -> None:
        for d in list(self.docs.values()):
            if d.dirty:
                del self.docs[self.key(d.path)]
        self._modified = False
        self.release_lock()

    def status(self) -> Dict:
        return {
            "has_project": self.project is not None,
            "project_path": self.project_path,
            "project_dir": self.project.dir if self.project else None,
            "modified": self._modified or bool(self.dirty_docs()),
            "dirty_files": [d.path for d in self.dirty_docs()],
            "undo_depth": len(self._undo),
            "redo_depth": len(self._redo),
            "state_dir": self.state_dir,
            "validate_on_save": self.validate,
        }
