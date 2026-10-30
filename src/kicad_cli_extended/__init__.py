"""kicad-cli-extended (command: ``kce``) — KiCad's official kicad-cli, extended for AI agents.

Every kicad-cli command and option is exposed unchanged; on top of that the
tool adds what kicad-cli lacks (schematic editing, PCB update/placement/
routing, fab packages, previews, diffs, undo) with JSON output everywhere.

The project's public names live here so that renaming touches one file.
"""

from __future__ import annotations

import os
from typing import Optional

__version__ = "0.1.0"

#: project / distribution name and agent-skill name
APP_NAME = "kicad-cli-extended"
#: console command
COMMAND = "kce"
#: prefix of every environment variable this tool reads
ENV_PREFIX = "KCE_"
#: per-project state directory (undo history, locks, previews)
STATE_DIR = ".kicad-cli-extended"


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read ``KCE_<name>`` from the environment."""
    return os.environ.get(ENV_PREFIX + name, default)
