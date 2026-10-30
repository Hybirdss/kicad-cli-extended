"""Interactive shell (``kce`` with no subcommand).

Interactive terminals get line editing, history and completion through
prompt_toolkit; piped stdin (scripts, agents) is read line by line with no
banner or prompt, so the output stream contains only command results.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import COMMAND, __version__

BUILTINS = {"help": "list command groups", "quit": "leave (also: exit, q, Ctrl-D)"}


class Shell:
    def __init__(self, execute: Callable[[List[str]], None], state: Callable[[], Tuple[str, int]],
                 groups: Dict[str, str], history_file: Optional[str] = None, banner_extra: str = "",
                 json_mode: bool = False, on_error: Optional[Callable[[ValueError], None]] = None):
        self.execute = execute          # runs one argv list
        self.state = state              # -> (project name, number of unsaved files)
        self.groups = groups
        self.history_file = history_file
        self.banner_extra = banner_extra
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty()
        self.json_mode = json_mode and not self.interactive
        self.on_error = on_error

    # -- input ---------------------------------------------------------------
    def _prompt(self) -> str:
        name, dirty = self.state()
        tag = f" [{name}{'*' if dirty else ''}]" if name else ""
        return f"{COMMAND}{tag}> "

    def _lines(self) -> Iterator[str]:
        if not self.interactive:
            for line in sys.stdin:
                yield line.rstrip("\r\n")
            return
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory, InMemoryHistory
        except ImportError:  # pragma: no cover - prompt_toolkit is a dependency
            while True:
                try:
                    yield input(self._prompt())
                except EOFError:
                    return
            return
        history = InMemoryHistory()
        if self.history_file:
            try:
                os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
                history = FileHistory(self.history_file)
            except OSError:
                pass
        words = sorted(set(self.groups) | set(BUILTINS) | {"exit", "--json", "--project", "--dry-run"})
        session = PromptSession(history=history, completer=WordCompleter(words, sentence=True))
        while True:
            try:
                yield session.prompt(self._prompt())
            except KeyboardInterrupt:
                continue
            except EOFError:
                return

    # -- loop ----------------------------------------------------------------
    def _say(self, text: str) -> None:
        print(text, file=sys.stderr if not self.interactive else sys.stdout)

    def help(self) -> None:
        if self.json_mode:
            self.execute(["--help"])
            return
        width = max(len(k) for k in list(self.groups) + list(BUILTINS))
        for k in sorted(self.groups):
            print(f"  {k.ljust(width)}  {self.groups[k]}")
        for k, v in BUILTINS.items():
            print(f"  {k.ljust(width)}  {v}")
        print(f"\n  Any `{COMMAND}` command works here without the `{COMMAND}` prefix, e.g. `--json sch symbols`.")

    def run(self) -> None:
        if self.interactive:
            print(f"{COMMAND} {__version__} interactive shell. Type 'help' or 'quit'.")
            if self.banner_extra:
                print(self.banner_extra)
        warned = False
        for raw in self._lines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low in ("quit", "exit", "q"):
                _, dirty = self.state()
                if dirty and self.interactive and not warned:
                    print(f"{dirty} unsaved file(s). Run `kce project save`, or type quit again to discard.")
                    warned = True
                    continue
                if self.json_mode:
                    print(json.dumps({"quit": True}, separators=(",", ":")))
                break
            if low in ("help", "?"):
                self.help()
                continue
            try:
                if os.name == "nt":
                    lexer = shlex.shlex(line, posix=True)
                    lexer.whitespace_split = True
                    lexer.commenters = ""
                    lexer.escape = ""  # Windows paths contain literal backslashes
                    argv = list(lexer)
                else:
                    argv = shlex.split(line)
            except ValueError as e:
                if self.on_error is not None:
                    self.on_error(e)
                else:
                    self._say(f"Error: {e}")
                continue
            if argv and argv[0] == COMMAND:
                argv = argv[1:]
            warned = False
            self.execute(argv)
        _, dirty = self.state()
        if dirty:
            self._say(f"{dirty} unsaved file(s) discarded (use `kce project save` before quitting to keep them).")
