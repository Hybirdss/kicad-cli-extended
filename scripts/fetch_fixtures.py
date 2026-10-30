#!/usr/bin/env python3
"""Fetch the KiCad demo projects used by the end-to-end tests.

Sparse, blob-filtered checkout of github.com/KiCad/kicad-source-mirror at a
release tag into ``.fixtures/kicad-src`` (gitignored).  Line endings are kept
as committed (LF), which the byte-identical round-trip test relies on.

    python scripts/fetch_fixtures.py                 # demos + the Altium boards the tests use (~45 MB)
    python scripts/fetch_fixtures.py --all-demos     # every demo (~340 MB)
    python scripts/fetch_fixtures.py --with-sources  # + common/jobs etc. for scripts/gen_jobset_catalog.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/KiCad/kicad-source-mirror.git"
TEST_DEMOS = ["pic_programmer", "video", "complex_hierarchy", "interf_u", "vme-wren"]
# Real Altium boards from KiCad's importer tests.
TEST_QA = [
    "qa/data/pcbnew/plugins/altium/eDP_adapter_dvt1_source/",
    "qa/data/pcbnew/plugins/altium/issue24654/",
]
SOURCES = ["common/jobs/", "common/io/kicad/", "kicad/cli/"]


def git(*args: str, cwd: Path | None = None) -> None:
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", *args]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="10.0.7", help="KiCad release tag (default: %(default)s)")
    ap.add_argument("--dest", default=str(Path(__file__).resolve().parents[1] / ".fixtures" / "kicad-src"))
    ap.add_argument("--all-demos", action="store_true")
    ap.add_argument("--with-sources", action="store_true")
    a = ap.parse_args()
    if shutil.which("git") is None:
        print("git is required", file=sys.stderr)
        return 1
    dest = Path(a.dest)
    paths = ["/demos/"] if a.all_demos else [f"/demos/{d}/" for d in TEST_DEMOS]
    paths += [f"/{q}" for q in TEST_QA]
    if a.with_sources:
        paths += [f"/{s}" for s in SOURCES]
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--filter=blob:none", "--no-checkout", "--depth", "1", "--branch", a.tag, REPO_URL, str(dest))
        git("config", "core.autocrlf", "false", cwd=dest)
    git("sparse-checkout", "init", "--no-cone", cwd=dest)
    git("sparse-checkout", "set", *paths, cwd=dest)
    git("checkout", cwd=dest)
    missing = [d for d in TEST_DEMOS if not (dest / "demos" / d).is_dir()]
    missing += [q for q in TEST_QA if not (dest / q).is_dir()]
    if missing:
        print(f"missing demos after checkout: {missing}", file=sys.stderr)
        return 1
    print(f"fixtures ready: {dest / 'demos'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
