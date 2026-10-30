"""Install the bundled agent skill (SKILL.md + COMMANDS.md) into agent skill directories.

Files are copied (never symlinked) so this works the same on Windows, macOS
and Linux.  Known targets live under the user's home directory; any other
location can be given with ``--dir``.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import APP_NAME

SKILL_NAME = APP_NAME
SKILL_FILES = ("SKILL.md", "COMMANDS.md")
PACKAGED_DIR = Path(__file__).resolve().parent / "skill_data"

#: target -> candidate skills directories relative to $HOME (first existing wins, else the first)
TARGETS: Dict[str, Sequence[str]] = {
    "agents": (".agents/skills",),                       # shared location read by several agent CLIs
    "claude": (".claude/skills",),                       # Claude Code (personal skills)
    "codex": (".codex/skills",),                         # OpenAI Codex CLI
    "gemini": (".gemini/skills", ".gemini/config/skills"),  # Gemini CLI
}


def packaged_files() -> List[Path]:
    files = [PACKAGED_DIR / f for f in SKILL_FILES]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise FileNotFoundError(f"Packaged skill files missing: {missing} (broken installation?)")
    return files


def frontmatter_name(skill_md: Path) -> Optional[str]:
    try:
        head = skill_md.read_text(encoding="utf-8")[:2000]
    except OSError:
        return None
    m = re.search(r"^name:\s*(?:>-?\s*\n\s*)?['\"]?([\w.-]+)", head, re.M)
    return m.group(1) if m else None


def target_dir(target: str, home: Optional[Path] = None) -> Path:
    home = home or Path.home()
    rels = TARGETS[target]
    for rel in rels:
        if (home / rel).is_dir():
            return home / rel
    return home / rels[0]


def detected_targets(home: Optional[Path] = None) -> List[str]:
    """Targets whose agent is present (its home config dir exists); ``agents`` is always included."""
    home = home or Path.home()
    out = ["agents"]
    for t, rels in TARGETS.items():
        if t != "agents" and any((home / Path(rel).parts[0]).is_dir() for rel in rels):
            out.append(t)
    return out


def _resolve(targets: Sequence[str], dirs: Sequence[str]) -> List[Dict[str, str]]:
    names = list(targets) or (["auto"] if not dirs else [])
    if "all" in names:
        names = list(TARGETS)
    if "auto" in names:
        names = [n for n in names if n != "auto"] + detected_targets()
    plan, seen = [], set()
    for t in names:
        if t not in TARGETS:
            raise ValueError(f"Unknown target {t!r}; choose from {sorted(TARGETS)}, auto, all (or use --dir)")
        base = target_dir(t)
        key = os.path.normcase(str(base))
        if key not in seen:
            seen.add(key)
            plan.append({"target": t, "skills_dir": str(base), "dest": str(base / SKILL_NAME)})
    for d in dirs:
        base = Path(d).expanduser()
        key = os.path.normcase(str(base))
        if key not in seen:
            seen.add(key)
            plan.append({"target": "dir", "skills_dir": str(base), "dest": str(base / SKILL_NAME)})
    return plan


def _state(dest: Path) -> str:
    if dest.is_symlink():
        return "symlink"
    if not dest.exists():
        return "absent"
    if all((dest / f).is_file() and filecmp.cmp(dest / f, src, shallow=False)
           for f, src in zip(SKILL_FILES, packaged_files())):
        return "current"
    return "outdated"


def install(targets: Sequence[str] = (), dirs: Sequence[str] = (), dry_run: bool = False,
            force: bool = False) -> Dict[str, object]:
    files = packaged_files()
    results = []
    for item in _resolve(targets, dirs):
        dest = Path(item["dest"])
        state = _state(dest)
        name = frontmatter_name(dest / "SKILL.md") if state not in ("absent", "symlink") else None
        if state == "outdated" and name not in (None, SKILL_NAME) and not force:
            results.append(dict(item, action="skipped", reason=f"{dest} holds a different skill ({name}); use --force"))
            continue
        action = {"absent": "create", "current": "unchanged", "outdated": "update",
                  "symlink": "replace-symlink"}[state]
        if not dry_run and action != "unchanged":
            if state == "symlink":
                dest.unlink()
            dest.mkdir(parents=True, exist_ok=True)
            for src in files:
                shutil.copyfile(src, dest / src.name)
        results.append(dict(item, action=action))
    return {"skill": SKILL_NAME, "source": str(PACKAGED_DIR), "dry_run": dry_run, "results": results}


def uninstall(targets: Sequence[str] = (), dirs: Sequence[str] = (), dry_run: bool = False) -> Dict[str, object]:
    results = []
    for item in _resolve(targets or ["all"], dirs):
        dest = Path(item["dest"])
        state = _state(dest)
        if state == "absent":
            results.append(dict(item, action="not-installed"))
            continue
        name = frontmatter_name(dest / "SKILL.md")
        if state != "symlink" and name != SKILL_NAME:
            results.append(dict(item, action="skipped", reason=f"{dest} is not this skill ({name})"))
            continue
        if not dry_run:
            if state == "symlink":
                dest.unlink()
            else:
                shutil.rmtree(dest)
        results.append(dict(item, action="remove"))
    return {"skill": SKILL_NAME, "dry_run": dry_run, "results": results}


def status() -> Dict[str, object]:
    rows = []
    detected = set(detected_targets())
    for t in TARGETS:
        dest = target_dir(t) / SKILL_NAME
        rows.append({"target": t, "dest": str(dest), "agent_detected": t in detected, "state": _state(dest)})
    return {"skill": SKILL_NAME, "source": str(PACKAGED_DIR), "targets": rows}
