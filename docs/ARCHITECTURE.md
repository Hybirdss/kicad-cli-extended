# Architecture

How `kce` drives KiCad 10, and the KiCad behaviours it relies on. Target: **KiCad 10.0.7**
(the command spec and job catalog were generated from tag `10.0.7` of
`KiCad/kicad-source-mirror`).

## Engines

| Engine | What it gives us | How `kce` uses it |
|---|---|---|
| `kicad-cli` | 43 leaf commands, 582 options: ERC, DRC, every plot/export (Gerber, drill, pos, STEP/STPZ/BREP/XAO/GLB/STL/PLY/U3D/VRML/3D-PDF, PDF/SVG/DXF/PS/HPGL, IPC-2581, ODB++, GenCAD, IPC-D-356, stats), BOM, netlist, 3D render, foreign-board import, upgrade, jobset run, symbol/footprint SVG + upgrade | Every subcommand and option is exposed verbatim (generated from `utils/kicad_cli_spec.json`), plus commands that post-process its output. It is also the **validator** for every file `kce` writes. |
| `pcbnew` (KiCad's SWIG module) | Board model: footprints, pads, tracks, vias, zones, zone filler, connectivity, Specctra DSN/SES, footprint loading, footprint wizards | All board edits and live connectivity queries, in a worker process (`utils/pcbnew_worker.py`). |
| Native S-expression files | Schematics (`.kicad_sch`) have **no Python API** in KiCad 10 | `core/sexpr.py` (lossless parser/writer) and `core/schematic.py` (Eeschema operations on the tree). |
| JSON files | `.kicad_pro` (net classes, rules, text variables, severities), `.kicad_jobset` | Edited as JSON through the session. |

### SWIG vs. IPC API

* `import pcbnew` in 10.0.7 warns that the SWIG interface is deprecated, but it ships, covers
  everything needed here and **runs headless**.
* The optional `gui` commands use the IPC API (`kicad-python`) and need a running PCB editor
  with its API server enabled. Headless editing, checks and exports use the other engines.
* Not wrapped by SWIG in 10.0.7 (worked around): `BOARD_STACKUP` (read from the board file),
  `RN_NET` ratsnest edges (the worker computes minimum-spanning connections from
  `CONNECTIVITY_DATA.GetConnectedItems`), and `GetConnectedPads()` returns an empty vector.

## Finding KiCad on each OS

`pcbnew` is a compiled extension for one interpreter: Linux — the distro `python3` KiCad was
built for; Windows — `<KiCad>\bin\python.exe`; macOS —
`KiCad.app/Contents/Frameworks/Python.framework/Versions/<v>/bin/python3`. Agents usually run
another Python, so all pcbnew work runs in a **worker subprocess** started with an interpreter
that can import it. Request and result travel through temp JSON files (pcbnew's console noise
cannot corrupt the protocol) and a SWIG crash cannot take down the CLI. One-shot operations
start a worker per call. `serve` enables `ResidentWorker` in `utils/kicad_backend.py`:
one interpreter stays alive, receives request/result filenames on stdin, and publishes results
atomically. It still loads fresh project state for each request. Native subprocess discovery
and execution share the request deadline; cancellation/timeout kills and reaps the worker.
The next board request can start a new worker. `kce` itself is pure
Python (click + prompt_toolkit) and installs into any Python ≥ 3.10.

Discovery (first hit wins; see `utils/kicad_backend.py`):

| What | Order |
|---|---|
| kicad-cli | `KCE_KICAD_CLI` (alias `KICAD_CLI`) → `PATH` → Windows `%ProgramFiles%\KiCad\<ver>\bin` and `%LOCALAPPDATA%\Programs\KiCad\<ver>\bin` (newest first); macOS `/Applications/KiCad/KiCad.app/Contents/MacOS`; Linux `/usr/bin`, `/usr/local/bin`, snap → Flatpak `org.kicad.KiCad` |
| pcbnew Python | `KCE_PYTHON` → the running interpreter → the interpreter bundled next to kicad-cli (Windows/macOS) → the system `python3` (Linux) → Flatpak. A candidate whose pcbnew major version matches kicad-cli wins. |
| Library data | `KICAD10_SYMBOL_DIR` / `KICAD10_FOOTPRINT_DIR` / `KICAD10_3DMODEL_DIR` / `KICAD10_TEMPLATE_DIR` → KiCad's `kicad_common.json` path variables → the install's data dir (`KCE_SHARE_DIR` overrides; Windows `<root>\share\kicad`, macOS `KiCad.app/Contents/SharedSupport`, Linux `/usr/share/kicad`) |
| KiCad config | `KICAD_CONFIG_HOME` → `~/.config/kicad/10.0`, `~/Library/Preferences/kicad/10.0`, `%APPDATA%\kicad\10.0` (Flatpak: `~/.var/app/org.kicad.KiCad/config/kicad/10.0`) |

An explicit environment variable always beats auto-detection. The kicad-cli command is kept
as an argv prefix so the Flatpak form works; with Flatpak, temp files handed to KiCad are put
under the user cache instead of `/tmp` (the sandbox has a private `/tmp`).

**Supported versions:** KiCad 10.x. KiCad's path variables carry the major version
(`KICAD10_*`); KiCad 9 is refused with a clear message because it cannot read the files KiCad 10
writes and its kicad-cli lacks options this tool exposes.

**Portability rules followed in the code:** explicit UTF-8 for every file and subprocess;
KiCad files written with LF on every OS (byte-identical round trip); paths compared
case-insensitively on Windows and never split on `/`; temp files via `tempfile`; no shell
invocation; file locks via `fcntl` (POSIX) or `msvcrt` (Windows); stdout falls back to
ASCII-escaped JSON and `backslashreplace` text when the console is not UTF-8 (e.g. cp1252).

## Data model

* A project is a directory with `<name>.kicad_pro` (JSON), the root `<name>.kicad_sch`
  (+ sub-sheets), `<name>.kicad_pcb`, and optionally `sym-lib-table`, `fp-lib-table`,
  `<name>.kicad_dru`, `*.kicad_jobset`.
* KiCad writes S-expressions through `KICAD_FORMAT::Prettify` (`common/io/kicad/kicad_io_utils.cpp`).
  `core/sexpr.py` ports it token for token (tab indent, `xy` runs up to column 99, token wrap at
  column 72, short-form and library-table modes) and keeps every atom's raw text. Every
  KiCad 9/10 file in the official demos (342 files) round-trips **byte-identically**; the only
  exception is one hand-edited board, which KiCad itself would reformat the same way.
* Symbol geometry: library coordinates are Y-up and parsed with Y inverted; placement applies
  the `TRANSFORM` matrices exactly as `SCH_SYMBOL::SetOrientation` (rotation, then mirror). A
  pin's connection point is its `(at)`. Verified by building circuits with rotated and
  mirrored symbols and checking KiCad's own netlist.
* KiCad 10 boards refer to nets **by name** (`(net "GND")`), with no top-level net table.
* Footprint ↔ symbol link: footprint `(path "/<sheet-uuid>/.../<symbol-uuid>")` ↔ netlist
  `sheetpath tstamps` + `tstamps`.

## Modules and execution flow

| Module | Responsibility |
|---|---|
| `cli.py` | Click registration, global options, session lifecycle and output/error framing |
| `core/contract.py` | Command effects, arguments, examples and public schema discovery |
| `core/intent.py` | Parse version-1 intent, apply symbols/nets/NC/power flags, compare KiCad netlist pin partitions and optional identity |
| `core/plan.py` | Validate plan steps/predicates, defer save, accept or roll back; checkpoint generations and heads |
| `core/serve.py` | JSON-RPC line framing, serialized command execution, request ids, deadlines, cancellation and shutdown |
| `core/runtime.py` | Request context, deadline budget, subprocess ownership, progress and failure details |
| `core/safety.py` | Effect-based read-only refusal and isolated dry-run inputs, outputs and session state |
| `core/session.py`, `core/workspace.py` | Lazy documents, snapshots/history, validated saves and mirrored unsaved project state |
| `core/pcb.py`, `utils/pcbnew_worker.py` | PCB synchronization, measurement and board edits via native KiCad |
| `core/sim.py`, `core/gui.py` | ngspice executable/shared-library simulation; optional live GUI adapter |
| `skill.py`, `skill_data/`, `schema_data/` | Skill installer and generated packaged documents/schemas |

A one-shot command resolves global options and effects before accessing the project. Read-only
refuses any writer/exporter/control operation. Dry-run runs declared operations on isolated
copies, including external library inputs and output locations. Arbitrary code and external
execution paths that cannot be isolated are refused. The session saves and validates successful
mutations, then the CLI returns the result. See the [JSON contract](../README.md#json-output-contract).

`run PLAN` parses and validates every step and predicate before executing. Steps share one
session and defer saving until all checks pass. Result predicates run before commit; missing
paths or false predicates reject the stage with AcceptanceFailed/design/3. Step failure or
validation failure restores project bytes, file additions/deletions and history. Restoration
I/O failure is explicit (rolled_back:false, commit_state:unknown). Plan paths cannot escape
the project; the plan document and controller journal can remain outside it. Session/global
configuration switching and arbitrary code are excluded from plans.

Checkpoints stage a complete generation, then atomically replace the named head. --immutable
refuses replacement. Snapshot contents exclude outputs, state, VCS, bytecode and locks; external
libraries are outside scope. Restore deletes project files created later. Keep journals and an
independent approved oracle outside restore scope, and plan input copies inside the project
before checkpointing. `--session-dir`/KCE_SESSION_DIR chooses an external per-project namespace.

`serve` runs a foreground JSON-RPC server with a reader and a serialized command executor.
The reader can accept cancellation while the executor owns the mutation. Acknowledgement waits
for rollback and native process reaping. Each request gets fresh project/session state, while
pcbnew operations reuse the interpreter. Results retain the CLI shape, including bare arrays;
errors embed the CLI contract in data.kce. Metadata exposes elapsed time and worker reuse;
unmeasured phase timings are null. Deadlines cover native discovery/execution, not a guarantee
of preempting arbitrary Python work. EOF/shutdown drains accepted requests and closes the worker.

See [Testing](TESTING.md) for module-specific suites and native recovery checks.

## KiCad GUI action → `kce` command

| KiCad GUI | `kce` |
|---|---|
| New project from template | `project new DIR --template NAME` |
| Add symbol / power symbol | `sch add LIB:SYM --at x,y`, `sch power GND --at U1:GND` |
| Wire / label / no-connect / junction | `sch connect A B`, `sch label-pin REF:PIN NET`, `sch label`, `sch no-connect`, `sch junction` |
| Symbol properties | `sch set-field/set-value/set-footprint/set-attr` |
| Annotate | `sch annotate` |
| Hierarchical sheet | `sch add-sheet`, `sch sheet-pin` |
| Update symbols from library | `sch update-symbols` |
| ERC / DRC | `check erc`, `check drc`, `check diagnose` (+ kicad-cli `sch erc`, `pcb drc`) |
| Update PCB from Schematic (F8) | `pcb update` (netlist → pcbnew) |
| Move / rotate / flip / lock footprint | `pcb move/rotate/flip/lock` |
| Route track / via | `pcb route REF:PAD REF:PAD`, `pcb track`, `pcb via` |
| Autorouter | `pcb autoroute` (built-in grid router, or Freerouting via DSN/SES) |
| Zones + fill | `pcb zone add/keepout/fill` |
| Board setup | `project rules ...`, `project netclass ...`, `pcb set-layers`, `pcb thickness` |
| Plot / fabrication outputs | `pcb export *`, `fab package --preset jlcpcb` |
| 3D viewer render | `pcb render`, `preview capture --recipe 3d` |
| Footprint wizards | `lib fp wizard qfn ...` |
| Symbol / footprint editor (simple) | `lib sym new`, `lib fp new` |
| Jobsets | `jobset new/add-job/add-output/execute`, `jobset run` |
| Scripting console | `raw pcbnew -c ...` |

## CLI design

* **Modes**: one-shot subcommands (`--json` everywhere) or an interactive shell when no
  subcommand is given; piped stdin is read line by line without banner or prompt.
* **State**: `--project` takes a `.kicad_pro`, `.kicad_sch`, `.kicad_pcb` or directory (default:
  the single `.kicad_pro` in the current directory). Documents load lazily into a session and
  edits mark them dirty. **One-shot commands save automatically** unless `--dry-run`; the shell
  saves on `project save`. Saves are atomic (temp file + rename) under an exclusive project lock,
  and every saved file is re-loaded by kicad-cli (`sch export netlist`, `pcb export stats`,
  `sym/fp upgrade` into a temp dir). If KiCad rejects a file, every file of that command is
  rolled back.
* **Unsaved state reaches every renderer**: with dirty documents, `core/workspace.py` mirrors the
  project into a temp dir and overlays them, so kicad-cli and pcbnew see exactly the session
  state (shell, `--dry-run`, multi-step commands).
* **Undo**: before each change, the affected files' previous content is stored as gzip blobs in
  `<project>/.kicad-cli-extended/blobs`; the history lives in
  `<project>/.kicad-cli-extended/session.json`; `session undo/redo` swap contents back and save
  (with validation). This works across processes.
* **kicad-cli coverage**: `core/kicad_cli_cmds.py` turns each spec entry into a command with the
  same long option names; `--show-command` prints the exact argv. The test suite walks the live
  `kicad-cli --help` tree and fails on any subcommand or option that is not reachable.
* **Errors**: one JSON object `{error, type, category, hint, details?}` on stderr, validated
  by `schemas/error.schema.json`. Categories map to exits: input 2, design 3, validation 4,
  crash 5, timeout 6, environment 7, internal 1. See the
  [JSON output contract](../README.md#json-output-contract) for types and recovery details.

## KiCad 10 behaviours this tool relies on

1. Global `sym-lib-table`/`fp-lib-table` do not exist until the GUI's first run, so ERC reports
   library errors on a fresh machine. `kce lib init-global-tables` installs KiCad's defaults (what
   the GUI does); library resolution falls back to KiCad's template tables.
2. kicad-cli writes wxWidgets debug noise ("Adding duplicate image handler") to stderr and ANSI
   colour codes in jobset output; both are stripped.
3. A new schematic re-saved by 10.0.7 has version `20260306` (demo files carry `20260101`);
   boards `20260206`.
4. `pcb render --width 1600 --height 900` yields 1568×872 px. `pcb render` uses `-h` for
   `--height`, so only long option names are mirrored.
5. Jobset `pcb_render` jobs default to 0×0 px and fail; `kce jobset add-job pcb_render` fills in
   1600×900. A failing job leaves its folder destination empty. Relative destination paths are
   resolved from the working directory, so `kce` runs jobsets from the project directory.
6. `pcb export hpgl` still exists but prints "No longer supported as of KiCad 10.0".
7. ERC/DRC default to the text `report` format; `kce` defaults them to `json`.
8. kicad-cli has no annotation, update-PCB-from-schematic, standalone zone fill or autorouter;
   `kce` implements them with the S-expression layer and pcbnew, and kicad-cli re-checks the
   result (ERC, DRC with `--schematic-parity`).
9. Without KiCad's 3D model package, STEP/render exports succeed but warn
   "Could not add 3D model ..." (board only).
10. `pcbnew.LoadBoard` loads `<same basename>.kicad_pro` from the board's directory, so the
    worker always works on a mirrored project directory and saves with
    `SaveBoard(..., aSkipSettings=True)`; only `kce` changes the project file.
11. Netlist `pinfunction` is `<pin name>_<pin number>`; `node pin` is the pin number.
12. `pcb update` on the official `pic_programmer` and hierarchical `video` demos changes nothing
    (0 added/removed/updated, 0 nets reassigned): the sync matches KiCad's own.
13. `pcb export gencad` help says "Output directory" but it needs a **file** path.
14. `pcb export pdf/svg/ps` still default to the deprecated single-file mode, which exits 2
    ("Done.") when `--output` is a directory; `kce` adds `--mode-separate` (pdf) or `--mode-multi`
    (svg/ps/dxf) for directory outputs unless a mode is given. `--mode-multipage` with a
    directory output writes nothing.
15. KiCad 10 drops `netclass_patterns` when `.kicad_pro` `net_settings.meta.version` is below 5
    (KiCad's own empty-project template ships 0); `kce` raises it to 5 when it edits net classes.
16. pcbnew's base `PCB_IO` (from `PCB_IO_MGR.FindPlugin`) has no `CreateLibrary`;
    `PCB_IO_KICAD_SEXPR()` is used to create `.pretty` libraries.

## Known gaps

* Moving a schematic symbol does not drag attached wires; prefer net labels (`sch label-pin`).
* The built-in autorouter is a 2-layer grid router for small boards. For dense boards use
  `--engine freerouting` (Java + `FREEROUTING_JAR`).
* Live GUI commands require the optional IPC client and a running editor; SPICE requires
  ngspice or its shared library. Neither is required for normal headless editing.
* Symbol creation supports box symbols with pins, not a general symbol graphics editor.
* Board import does not reconstruct a source schematic or translate all source rules.
  Intent verification establishes agreement with supplied intent, not independent source equivalence.
* Placement/clearance heuristics, model-path existence and a clean DRC do not certify impedance,
  vendor land patterns, assembly orientation or manufacturing capability. See the
  [board](BOARD-GAPS-ROADMAP.md) and [library/intent](LIBRARY-INTENT-ROADMAP.md) roadmaps.
