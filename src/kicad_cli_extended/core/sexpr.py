"""Lossless S-expression parser / writer for KiCad native files.

KiCad stores schematics (.kicad_sch), boards (.kicad_pcb), symbol libraries
(.kicad_sym), footprints (.kicad_mod), library tables (sym-lib-table /
fp-lib-table), drawing sheets (.kicad_wks) and design rules (.kicad_dru) as
S-expressions.

Design goals
------------
* **Lossless**: every atom keeps its exact source text.  Numbers are stored as
  bare symbols (``"1.27"`` stays ``"1.27"``) and quoted strings remember their
  raw escaped form, so ``dumps(loads(text)) == text`` byte-for-byte for files
  written by KiCad 10 (verified in the test-suite against the official demos).
* **KiCad-identical formatting**: :func:`dumps` is a faithful port of
  ``KICAD_FORMAT::Prettify`` (common/io/kicad/kicad_io_utils.cpp, tag 10.0.7),
  so edited files diff minimally against what KiCad itself would save.
* **Convenient tree API**: :class:`SList` is a ``list`` subclass with
  ``tag``, ``find``, ``find_all``, ``value``, ``set`` helpers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Union

__all__ = [
    "SList", "QStr", "Sym", "Atom", "Node",
    "loads", "load", "dumps", "dump", "quote", "fmt_num", "S", "Q",
    "SexprError", "walk", "parse_path_query",
]


class SexprError(ValueError):
    """Raised for malformed S-expression input."""


class Sym(str):
    """A bare (unquoted) atom: keywords, numbers, yes/no, layer names..."""

    __slots__ = ()

    @property
    def raw(self) -> str:
        return str(self)


class QStr(str):
    """A quoted string atom.

    ``str(self)`` is the *unescaped* value; ``raw`` is the exact quoted source
    text (including the surrounding double quotes).
    """

    __slots__ = ("_raw",)

    def __new__(cls, value: str, raw: Optional[str] = None):
        obj = super().__new__(cls, value)
        obj._raw = raw
        return obj

    @property
    def raw(self) -> str:
        if self._raw is None:
            self._raw = quote(str(self))
        return self._raw


Atom = Union[Sym, QStr]


class SList(list):
    """An S-expression list. ``self[0]`` is normally the tag symbol."""

    __slots__ = ()

    # -- identity -----------------------------------------------------------
    @property
    def tag(self) -> Optional[str]:
        if self and not isinstance(self[0], SList):
            return str(self[0])
        return None

    # -- search -------------------------------------------------------------
    def find(self, tag: str) -> Optional["SList"]:
        """First direct child list with the given tag."""
        for c in self:
            if isinstance(c, SList) and c.tag == tag:
                return c
        return None

    def find_all(self, tag: str) -> List["SList"]:
        return [c for c in self if isinstance(c, SList) and c.tag == tag]

    def children(self) -> List["SList"]:
        return [c for c in self if isinstance(c, SList)]

    def atoms(self) -> List[str]:
        """Atoms after the tag (positional values)."""
        return [c for c in self[1:] if not isinstance(c, SList)]

    def value(self, tag: Optional[str] = None, default=None, index: int = 0):
        """``(tag v0 v1 ...)`` -> ``v{index}`` as ``str``.

        Without ``tag``, returns this list's own positional atom.
        """
        node = self if tag is None else self.find(tag)
        if node is None:
            return default
        a = node.atoms()
        if len(a) <= index:
            return default
        return str(a[index])

    def fvalue(self, tag: Optional[str] = None, default=None, index: int = 0):
        v = self.value(tag, None, index)
        if v is None:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def deep_find_all(self, tag: str) -> List["SList"]:
        return [n for n in walk(self) if n.tag == tag]

    def property(self, name: str) -> Optional["SList"]:
        """Find ``(property "name" "value" ...)``."""
        for c in self.find_all("property"):
            if len(c) > 1 and str(c[1]) == name:
                return c
        return None

    def get_property(self, name: str, default=None):
        p = self.property(name)
        if p is None or len(p) < 3:
            return default
        return str(p[2])

    # -- mutation -----------------------------------------------------------
    def set(self, tag: str, *values, after: Optional[str] = None) -> "SList":
        """Set ``(tag values...)`` replacing an existing child or appending."""
        node = S(tag, *values)
        for i, c in enumerate(self):
            if isinstance(c, SList) and c.tag == tag:
                self[i] = node
                return node
        if after is not None:
            for i, c in enumerate(self):
                if isinstance(c, SList) and c.tag == after:
                    self.insert(i + 1, node)
                    return node
        self.append(node)
        return node

    def remove_all(self, tag: str) -> int:
        before = len(self)
        self[:] = [c for c in self if not (isinstance(c, SList) and c.tag == tag)]
        return before - len(self)

    def remove_child(self, child: "SList") -> bool:
        for i, c in enumerate(self):
            if c is child:
                del self[i]
                return True
        return False

    # -- conversion ---------------------------------------------------------
    def to_py(self):
        """Plain Python representation (lists and strings) for JSON output."""
        return [c.to_py() if isinstance(c, SList) else str(c) for c in self]

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        txt = compact(self)
        return f"SList({txt[:120]}{'...' if len(txt) > 120 else ''})"


Node = Union[SList, Sym, QStr]

# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def fmt_num(v: float, max_decimals: int = 6) -> str:
    """Format a number the way KiCad does (no exponent, trailing zeros trimmed)."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if abs(v) < 0.5 * 10 ** (-max_decimals):
        return "0"
    s = f"{v:.{max_decimals}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def quote(value: str) -> str:
    """Quote a string exactly like OUTPUTFORMATTER::Quotes."""
    out = ['"']
    for ch in value:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def Q(value) -> QStr:
    """Make a quoted-string atom."""
    return QStr(str(value))


def _atomize(v) -> Node:
    if isinstance(v, (SList, Sym, QStr)):
        return v
    if isinstance(v, bool):
        return Sym("yes" if v else "no")
    if isinstance(v, (int, float)):
        return Sym(fmt_num(v))
    if isinstance(v, (list, tuple)):
        return S(*v)
    if isinstance(v, str):
        # Plain python strings become bare symbols when safe, quoted otherwise.
        if v and re.fullmatch(r"[A-Za-z0-9_.+\-*/#:$&%@!?~^]+", v):
            return Sym(v)
        return QStr(v)
    raise TypeError(f"Cannot convert {type(v).__name__} to S-expression atom")


def S(tag, *items) -> SList:
    """Build a list: ``S("at", 1.0, 2.0, 0)`` -> ``(at 1 2 0)``."""
    out = SList()
    out.append(_atomize(tag) if not isinstance(tag, SList) else tag)
    for it in items:
        if it is None:
            continue
        out.append(_atomize(it))
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+|"', re.S)
_ESC_RE = re.compile(r"\\(.)", re.S)
_ESC_MAP = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}


def _unescape(raw: str) -> str:
    body = raw[1:-1]
    if "\\" not in body:
        return body
    return _ESC_RE.sub(lambda m: _ESC_MAP.get(m.group(1), m.group(1)), body)


def loads(text: str) -> SList:
    """Parse KiCad S-expression text. Returns the single top-level list."""
    stack: List[SList] = []
    root: Optional[SList] = None
    append_stack = stack.append
    pop_stack = stack.pop
    cur: Optional[SList] = None
    for tok in _TOKEN_RE.findall(text):
        c0 = tok[0]
        if c0 == "(" and len(tok) == 1:
            node = SList()
            if cur is not None:
                cur.append(node)
            elif root is not None:
                raise SexprError("Multiple top-level expressions in document")
            else:
                root = node
            append_stack(node)
            cur = node
        elif c0 == ")" and len(tok) == 1:
            if not stack:
                raise SexprError("Unbalanced ')'")
            pop_stack()
            cur = stack[-1] if stack else None
        elif c0 == '"':
            if len(tok) == 1:
                raise SexprError("Unterminated string")
            if cur is None:
                raise SexprError(f"Atom outside of list: {tok[:40]}")
            cur.append(QStr(_unescape(tok), tok))
        else:
            if cur is None:
                raise SexprError(f"Atom outside of list: {tok[:40]}")
            cur.append(tok)  # bare symbol (plain str for speed)
    if stack:
        raise SexprError(f"Unbalanced '(' - {len(stack)} list(s) not closed")
    if root is None:
        raise SexprError("Empty S-expression document")
    return root


def loads_many(text: str, comments: bool = True) -> List[SList]:
    """Parse a document holding several top-level lists (e.g. .kicad_dru).

    ``#`` line comments are stripped when ``comments`` is true.
    """
    if comments:
        text = "\n".join(_strip_comment(line) for line in text.splitlines())
    wrapped = loads("(__doc__ " + text + "\n)")
    return [c for c in wrapped[1:] if isinstance(c, SList)]


def _strip_comment(line: str) -> str:
    in_q = False
    esc = False
    for i, ch in enumerate(line):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
        elif ch == '"':
            in_q = not in_q
        elif ch == "#" and not in_q:
            return line[:i]
    return line


def load(path: str) -> SList:
    with open(path, "r", encoding="utf-8") as f:
        return loads(f.read())


# ---------------------------------------------------------------------------
# Writer: port of KICAD_FORMAT::Prettify (kicad 10.0.7)
# ---------------------------------------------------------------------------

_SHORT_FORM = {"font", "stroke", "fill", "teardrop", "offset", "rotate", "scale"}


def _alpha_prefix(s: str) -> str:
    m = re.match(r"[A-Za-z]*", s)
    return m.group(0) if m else ""


def compact(node: Node) -> str:
    """Single-line serialisation (the formatter's raw input form)."""
    if not isinstance(node, SList):
        return node.raw if hasattr(node, "raw") else str(node)
    return "(" + " ".join(compact(c) for c in node) + ")"


def dumps(node: SList, mode: str = "normal") -> str:
    """Serialise with KiCad's exact Prettify rules.

    ``mode``: ``"normal"`` (sch/pcb/sym/mod), ``"compact_text"``
    (COMPACT_TEXT_PROPERTIES) or ``"library_table"`` (sym/fp-lib-table).

    The algorithm is a token-level port of ``KICAD_FORMAT::Prettify``: the
    compact form KiCad's formatter emits has exactly one space between
    tokens, and every whitespace/paren decision below mirrors the C++ state
    machine (same thresholds: xy run column 99, token wrap column 72).
    """
    text_special = mode == "compact_text"
    lib_special = mode == "library_table"
    out: List[str] = []
    w = out.append
    depth = 0
    lib_depth = 0
    last = ""
    multi = False
    in_xy = False
    short = False
    lib_row = False
    short_depth = 0
    col = 0
    first_emitted = False
    tabs = ["\t" * i for i in range(64)]

    def tab(n):
        return tabs[n] if n < 64 else "\t" * n

    stack: List[list] = []
    cur = node
    idx = -1  # -1 => list not yet opened
    while True:
        if idx == -1:
            # ---- open list "cur"
            first = cur[0] if cur else None
            if first is None or isinstance(first, SList):
                ftxt = ""
            else:
                ftxt = first.raw if type(first) is QStr else first
            cur_xy = ftxt == "xy" and len(cur) > 1
            if text_special or lib_special:
                ap = _alpha_prefix(ftxt)
                cur_short = text_special and ap in _SHORT_FORM
                cur_lib = lib_special and ap == "lib"
            else:
                cur_short = cur_lib = False
            if not first_emitted:
                w("(")
                col += 1
                first_emitted = True
            elif (in_xy and cur_xy and col < 99) or short or lib_row:
                w(" (")
                col += 2
            else:
                w("\n" + tab(depth) + "(")
                col = depth + 1
            in_xy = cur_xy
            if cur_short:
                short = True
                short_depth = depth
            elif cur_lib:
                lib_row = True
                lib_depth = depth
            depth += 1
            last = "("
            idx = 0
        if idx >= len(cur):
            # ---- close list
            if depth > 0:
                depth -= 1
            if short:
                w(")")
                col += 1
            elif lib_row and depth == lib_depth:
                w(")")
                lib_row = False
            elif last == ")" or multi:
                w("\n" + tab(depth) + ")")
                col = depth + 1
                multi = False
            else:
                w(")")
                col += 1
            if short_depth == depth:
                short = False
                short_depth = 0
            last = ")"
            if not stack:
                break
            cur, idx = stack.pop()
            continue
        child = cur[idx]
        idx += 1
        is_list = isinstance(child, SList)
        if is_list:
            raw = None
        else:
            raw = child.raw if type(child) is QStr else child
        if idx > 1:
            # whitespace between tokens
            if depth > 0 and last != "(" and not is_list and raw[:1] != ")":
                if in_xy or col < 72:
                    w(" ")
                    col += 1
                elif short or lib_row:
                    w(" ")
                else:
                    w("\n" + tab(depth))
                    col = depth
                    multi = True
        if is_list:
            stack.append((cur, idx))
            cur = child
            idx = -1
            continue
        w(raw)
        col += len(raw) if raw.isascii() else len(raw.encode("utf-8"))
        if raw:
            last = raw[-1]
    w("\n")
    return "".join(out)


def dump(node: SList, path: str, mode: str = "normal") -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(node, mode))


def mode_for_path(path: str) -> str:
    base = Path(path).name
    if base in ("sym-lib-table", "fp-lib-table", "design-block-lib-table"):
        return "library_table"
    return "normal"


# ---------------------------------------------------------------------------
# Tree walking + a tiny path query language for the raw escape hatch
# ---------------------------------------------------------------------------


def walk(node: SList) -> Iterator[SList]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed([c for c in n if isinstance(c, SList)]))


def parse_path_query(query: str) -> List[tuple]:
    """Parse ``a/b[2]/property[Reference]/at`` style queries.

    Each step is ``tag``, ``tag[N]`` (Nth match, 0-based), ``tag[=value]`` /
    ``tag[value]`` (first atom equals value) or ``*`` (any child list).
    A leading ``//`` searches recursively for the first step.
    """
    steps = []
    recursive_first = query.startswith("//")
    q = query.strip("/")
    if not q:
        return steps
    for i, part in enumerate(re.findall(r"(?:[^/\[]|\[[^\]]*\])+", q)):
        m = re.fullmatch(r"([^\[]+)(?:\[([^\]]*)\])?", part)
        if not m:
            raise ValueError(f"Bad query step: {part!r}")
        tag, sel = m.group(1), m.group(2)
        kind, arg = "all", None
        if sel is not None:
            if re.fullmatch(r"-?\d+", sel):
                kind, arg = "index", int(sel)
            else:
                kind, arg = "value", sel[1:] if sel.startswith("=") else sel
        steps.append((tag, kind, arg, recursive_first and i == 0))
    return steps


def query(root: SList, q: str) -> List[SList]:
    """Evaluate a path query, returning matching lists."""
    current = [root]
    steps = parse_path_query(q)
    if steps and steps[0][0] == root.tag and not steps[0][3]:
        tag, kind, arg, _ = steps[0]
        steps = steps[1:]
    for tag, kind, arg, recursive in steps:
        nxt: List[SList] = []
        for n in current:
            if recursive:
                cands = [x for x in walk(n) if x is not n and (tag == "*" or x.tag == tag)]
            else:
                cands = [c for c in n if isinstance(c, SList) and (tag == "*" or c.tag == tag)]
            if kind == "index":
                if -len(cands) <= arg < len(cands):
                    nxt.append(cands[arg])
            elif kind == "value":
                nxt.extend(c for c in cands if len(c) > 1 and not isinstance(c[1], SList)
                           and str(c[1]) == arg)
            else:
                nxt.extend(cands)
        current = nxt
    return current


def iter_atoms(node: SList) -> Iterable[str]:
    for c in node:
        if not isinstance(c, SList):
            yield str(c)
