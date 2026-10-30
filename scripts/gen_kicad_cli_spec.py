#!/usr/bin/env python3
"""Regenerate src/kicad_cli_extended/utils/kicad_cli_spec.json by walking `kicad-cli --help`.

Run after a KiCad update; the E2E coverage test fails until the spec matches
the installed kicad-cli.  Usage: python scripts/gen_kicad_cli_spec.py [out.json]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kicad_cli_extended.utils.kicad_cli_help import kicad_cli_version, walk_kicad_cli  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src" / "kicad_cli_extended" / "utils" / "kicad_cli_spec.json"
tree = walk_kicad_cli()
leaves = {k: v for k, v in tree.items() if not v["subcommands"]}
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump({"kicad_cli_version": kicad_cli_version(), "commands": leaves}, f, indent=1, sort_keys=True)
    f.write("\n")
print(f"{len(leaves)} leaf commands, {sum(len(v['options']) for v in leaves.values())} options -> {out}")
