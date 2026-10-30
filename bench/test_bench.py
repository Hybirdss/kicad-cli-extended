#!/usr/bin/env python3
"""Self-check for the benchmark's context figures; needs tiktoken, which the package does not.

The `kce` side loads one file, SKILL.md, and that file already contains its frontmatter: the
"frontmatter + SKILL.md body" figure is the token count of the whole file. An earlier version
billed the frontmatter twice, reporting 4,162 tokens where the skill costs 3,984.

    uv run --no-project --with tiktoken==0.14.0 python bench/test_bench.py
    uv run --no-project --with tiktoken==0.14.0 --with pytest python -m pytest bench/test_bench.py

Needs tiktoken, which is deliberately not a dependency of the package itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _run_bench():
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        try:
            import pytest
        except ImportError:
            raise
        pytest.importorskip("tiktoken")
    import run_bench

    return run_bench


def _context():
    rb = _run_bench()
    d = rb.ROOT / "skills" / "kicad-cli-extended"
    return rb, rb.kce_context((d / "SKILL.md").read_text(encoding="utf-8"),
                              (d / "COMMANDS.md").read_text(encoding="utf-8"))


def test_with_skill_tokens_equals_the_whole_skill_file():
    rb, ctx = _context()
    skill = (rb.ROOT / "skills" / "kicad-cli-extended" / "SKILL.md").read_text(encoding="utf-8")
    assert ctx["with_skill_tokens"] == rb.tok(skill)
    assert ctx["with_skill_tokens"] == ctx["on_trigger"]["skill_body"]
    assert 0 < ctx["always_tokens"] < ctx["with_skill_tokens"]


if __name__ == "__main__":
    rb, ctx = _context()
    skill = (rb.ROOT / "skills" / "kicad-cli-extended" / "SKILL.md").read_text(encoding="utf-8")
    test_with_skill_tokens_equals_the_whole_skill_file()
    print(f"frontmatter {ctx['always_tokens']}  with skill {ctx['with_skill_tokens']} "
          f"(tok(SKILL.md) {rb.tok(skill)})  commands {ctx['on_demand']['commands_reference']}")
    print("SELF-CHECK OK")
