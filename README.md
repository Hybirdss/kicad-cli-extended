<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

# kicad-cli-extended

KiCad's official `kicad-cli`, extended for AI agents: every kicad-cli command and option, plus
schematic editing, PCB update and routing, fab packages, previews and diffs — all with JSON output
and an agent skill.

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

> Working with Altium Designer? See [altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended), the companion tool. Both projects use GPL-3.0-or-later.

The command is **`kce`** (KiCad CLI Extended). It drives the real KiCad 10 — `kicad-cli` and
KiCad's `pcbnew` Python module — and re-loads every file it writes through kicad-cli, so KiCad
itself validates each change.

Build a circuit from a netlist intent, verify the connections with KiCad, then place, route
and produce manufacturing files. For an existing board, preview a change, gate it in a
transaction and restore an accepted checkpoint if it fails. Install the skill to give your
agent the same task-driven recipes.

## Benchmark

`kce` carries 157 always-loaded skill tokens (3,737 with the whole SKILL.md),
compared with 4,927–63,420 tool-definition tokens for the two public KiCad MCP servers
selected by stars on 2026-10-28. The full command reference costs 11,238 tokens when opened on demand.

Measured on 2026-10-30 with KiCad 10.0.7, Python 3.11.17 on a 16-CPU Linux machine,
tiktoken 0.14.0 `o200k_base`: 5 interleaved repetitions on fresh copies of KiCad's
`pic_programmer` demo. Every repetition started below the 4.00 load gate
(recorded loads: 2.02, 3.38, 3.54, 3.45, 3.36).
Tokens are median request plus response; time is median wall clock. CLI calls start a new
process; MCP servers are already running, with startup reported separately.
[Method](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md), [summary and ranges](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md),
[all recorded runs](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json).

| Side | Tools | Tokens before any work |
|---|---:|---:|
| `kce`, skill frontmatter | 1 command | **157** |
| `kce`, whole SKILL.md (includes frontmatter) | | 3,737 |
| mixelpixx/KiCAD-MCP-Server 2.8.3 (2,995★ at selection), all tools | 244 | **63,420** |
| mixelpixx, `core-toolbox` config | 37 | **6,445** |
| lamaalrajih/kicad-mcp (531★ at selection) | 16 | **4,927** |

| Task (on the demo project) | `kce` tokens / time | mixelpixx tokens / time | lamaalrajih tokens / time |
|---|---|---|---|
| ERC summary | 108 / 3.62 s | 69 / 3.09 s | no tool |
| DRC | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + drill | 2,771 / 2.15 s | 645 / 1.43 s | no tool |

All 50 supported runs succeeded and produced the expected output: BOM/Gerber/drill files on
disk, or an ERC/DRC violation count. Per-task tokens exclude context overhead; these are
measurements of the listed calls and responses on one demo, rather than a complete agent session.

## kicad-cli vs. kicad-cli-extended

Derived from the `--help` tree of KiCad 10.0.7's kicad-cli (43 commands, 582 options; stored in
[`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json)).

| Feature | kicad-cli | kicad-cli-extended |
|---|:-:|:-:|
| Exports and checks (ERC, DRC, Gerber, drill, BOM, STEP, PDF, render, …) | ✓ | ✓ all 43 commands, every option passed through |
| JSON output for every command | JSON report files from `erc`, `drc`, `export stats`, `import` only | ✓ |
| Schematic editing: place, wire, label, annotate | | ✓ |
| Update PCB from schematic | `drc --schematic-parity` only checks it | ✓ |
| Placement, routing, autoroute, zones, outline | zone refill in `drc` / plots only | ✓ |
| Net classes and design rules | read only | ✓ |
| Fab packages: JLCPCB, PCBWay | jobsets, no vendor presets | ✓ |
| Previews and revision diff | renders and plots only | ✓ |
| Undo/redo sessions | | ✓ |
| Library search, custom symbols/footprints and wizards, pin/pad/model checks | | ✓ |
| Netlist-first apply and strict verify | netlist export | ✓ intent JSON, patch/subset and optional identity checks |
| Atomic plans and named checkpoints | | ✓ acceptance predicates and restore |
| Resident JSON-RPC worker | | ✓ request deadlines, cancellation, progress |
| Placement measurement and bounded repair | | ✓ score, legalize, place near |
| Differential pairs, length/skew rules and tuning | DRC checks only | ✓ rules, coupled routing, meanders |
| Via fences/stitching, teardrops, rule areas | | ✓ |
| Panelization | | ✓ mouse bites and V-score to a separate board |
| Altium/Eagle and other board import | ✓ board conversion | ✓ target selection, read-back and source-preservation report |
| Live GUI control through KiCad's IPC API | | ✓ |
| SPICE simulation (ngspice) | | ✓ |
| AI agent skill | | ✓ |

## Requirements and install

* **KiCad 10.x** (kicad-cli + its pcbnew Python module, both ship with KiCad).
* **Python 3.10+** for `kce` itself. pcbnew runs in KiCad's own interpreter, found automatically.

| OS | Install KiCad 10 |
|---|---|
| Windows | [installer](https://www.kicad.org/download/windows/), `choco install kicad`, or `winget install KiCad.KiCad` |
| macOS | `brew install --cask kicad` (or the .dmg) |
| Ubuntu / Debian | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| Fedora / Arch | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| Flatpak | `flatpak install flathub org.kicad.KiCad` (detected automatically) |

Install `kce` from the public repository with your tool of choice:

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # recommended
# or
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

PyPI publishing is not enabled yet; the repository installation works without it.

Check what was found:

```bash
kce --json info doctor          # kicad-cli, pcbnew interpreter, library dirs: "ok": true
kce lib init-global-tables      # first run only: KiCad's default library tables
```

## Build your first LED board

This makes a resistor-limited LED powered through a two-pin connector. Use a 3.3 V supply
for this example; the resistor is 330 Ω. Start in an empty working directory after the
installation checks above. Labels connect the pins without crossing unrelated wires.

```bash
kce --json project new led --name led
cd led
kce --json sch add Device:LED --ref D1 --at 101.6,63.5 --angle 90 --value RED --footprint LED_SMD:LED_0805_2012Metric
kce --json sch add Device:R --ref R1 --at 76.2,63.5 --value 330 --footprint Resistor_SMD:R_0805_2012Metric
kce --json sch add Connector_Generic:Conn_01x02 --ref J1 --at 50.8,63.5 --mirror y --footprint Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical
kce --json sch label-pin J1:1 VCC
kce --json sch label-pin R1:1 VCC
kce --json sch label-pin R1:2 LED
kce --json sch label-pin D1:2 LED
kce --json sch label-pin D1:1 GND
kce --json sch label-pin J1:2 GND
kce --json check erc --fail-on-error
kce --json sch nets
kce --json pcb outline rect --at 100,100 --size 30,20
kce --json --dry-run pcb update --stable-uuids
kce --json pcb update --stable-uuids
kce --json pcb score
kce --json pcb autoroute --grid auto
kce --json pcb connectivity
kce --json check drc --fail-on-error
kce --json fab package --preset jlcpcb --fail-on-drc --no-step
```

Expect three components, three two-pin nets, ERC/DRC errors of zero and no unconnected or
parity issues. Score reports no courtyard overlaps or out-of-outline parts. The final result
must have complete:true, no failed_steps and a nonempty files list under outputs/fab-jlcpcb/.
This is a bare-board example. For assembly, choose verified supplier parts and review the CPL
in the fab viewer before ordering. No manufacturer rule qualification is implied by a clean DRC.
Use `pcb render` after `info doctor --require render` if you need a 3D preview.

`kce` without arguments opens an interactive shell. `kce --help`, `kce <group> --help`,
`kce --json info commands --full` and `kce --json info describe pcb score` expose the command tree.

## Review an ECO and release a package

Manufacturing releases use `fab package --preset jlcpcb --fail-on-drc -o RELEASE`.
Exports run in staging and publish only when all requested outputs are present.
Failures return `complete:false` and `failed_steps` with exit 3, preserving the accepted
package. Use a fresh release directory on platforms without atomic directory exchange.

For a verified ECO schematic with a new C10 and an existing U2 on net VCC,
checkpoint the PCB stage, preview the sync, then scope placement and routing:

```bash
kce --project board session checkpoint before-pcb-eco
kce --project board --dry-run pcb update --keep-extra --lock-existing
kce --project board pcb update --keep-extra --lock-existing
kce --project board pcb place near C10 --to U2 --net VCC --max-dist 6 --rotation auto
kce --project board pcb legalize --refs C10 --max-move 5
kce --project board --dry-run pcb autoroute --refs C10 --grid auto
kce --project board pcb autoroute --refs C10 --grid auto
kce --project board pcb connectivity
kce --project board check drc --fail-on-error
kce --project board project compare git:HEAD --geometry --allow C2,C10,net:VCC,net:GND --fail-on-unexpected
```

Review `replaced[].pads_moved`, `unmatched_pins` and `tracks_ending_on_old_pads` before
applying a sync. An approved swap that moves pads requires omitting `--lock-existing`.
Replacements retain footprint UUIDs and existing tracks. Gate the finished
ECO with connectivity and DRC. Scoped operations keep other footprints fixed without
changing their saved lock flags; compare rejects changes outside the allowance with exit 4.
For foreign intake use `project import SOURCE --dir NEW`, or select the target project
with `--project TARGET pcb import SOURCE`; inspect `read_back` and the source-preservation assertion.

Before releasing assembly files, run `fab parts check --offline` and
`fab bom-check --require MPN,LCSC,Footprint`. A package can require LCSC IDs and a matching
revision with `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest
--name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE`. Review `cpl.review` in the fab's
placement viewer. File and ZIP hashes accompany the result; only `complete:true` is ready
for hand-off. Fab capability presets and full cross-format equivalence remain on the
[board scenario roadmap](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/BOARD-GAPS-ROADMAP.md).

## AI agent skill

`kce` ships a skill (`SKILL.md` + `COMMANDS.md`) that teaches an agent the workflows above.

```bash
kce skill install                 # every agent found on this machine + ~/.agents/skills
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # project-local
kce skill status
```

Targets: `agents` (`~/.agents/skills`), `claude` (`~/.claude/skills`), `codex` (`~/.codex/skills`),
`gemini` (`~/.gemini/skills`), or any directory with `--dir`. Files are copied, so this works on
Windows too. The same skill is in [`skills/kicad-cli-extended/`](https://github.com/Hybirdss/kicad-cli-extended/tree/main/skills/kicad-cli-extended) for
tools that install skills from GitHub.

## Workflows

Install the skill with `kce skill install`; it selects these chains from your request.
The [installed skill](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe)
has twelve trigger-driven recipes. [Recipe details](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md)
expand their commands, acceptance checks and recovery steps. Use explicit check gates:
ERC/DRC default to report-only, and a successful command can still return an unacceptable result.

### First board and fabrication

Follow the LED quickstart above, then choose verified supplier IDs for assembly. For USB-C,
inspect the connector's actual pin map and the intended power role before writing the intent.
Search/show parts, checkpoint, dry-run apply, run apply/verify/ERC, outline, dry-run sync,
place, score, route, pour, DRC, package. Place connector openings with pcb move and pcb lock;
automatic connector edge placement is not implemented. Set the real fabrication limits with
project rules/netclass commands. Fab presets select outputs, not manufacturer-qualified rules.
Accept only complete:true; review BOM, CPL orientation and Gerber layers before ordering yourself.

### Netlist-first board from a specification

Keep the approved electrical intent in JSON. Put the working intent inside the project and
capture it in the initial checkpoint; keep plans, journals and an independent oracle outside.
Every stage is a run plan with acceptance before commit, followed by an immutable checkpoint:

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

The schematic plan applies intent, annotates, verifies strictly and gates ERC. The board plan
sets layers/outline, syncs with stable UUIDs and scores. Later plans place, route and gate DRC.
A score step can use expect:[{"path":"courtyard_overlaps.count","op":"eq","value":0}].
If later review rejects a committed stage, restore its preceding checkpoint. Repeating a sync
must preserve the accepted footprint identity map.

### Engineering change on an existing project

Capture baseline nets, footprints/pads, score and ERC/DRC. Checkpoint pre-eco. Rehearse and run
the schematic plan with read-backs and strict intent verification before syncing the PCB.
The ECO example above shows scoped placement/routing. Compare checkpoint:pre-eco or git:HEAD
with --geometry and an explicit --allow list; inspect allowed-ref changes too. Pin nets,
positions, UUIDs, pad displacement and routing must match the approved change. On regression,
restore pre-eco and verify the restored bytes/nets/score. Generate new outputs into a fresh
directory; checkpoints exclude outputs, so archive --include-outputs when preserving a release.

### Continuous integration and tagged releases

Use the [PR template](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml)
and [hardware release template](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml).
Pin tools, fetch the base revision, gate ERC/DRC with explicit thresholds, compare, preview,
check BOM fields/prices and require the title-block revision before packaging. Preserve stdout
on design/3 along with stderr. jobset execute fails with design/3 for failed jobs by default;
its fab-template archive contains Gerber/drill, so use fab package for assembly outputs.
Rehearse a clean and a seeded bad design. Assert a clean Git tree and review emitted files/hashes.
The templates do not claim a completed Windows run or publish/pay for a board.

### Placement search with a warm worker

Verify intent and baseline ERC/DRC, then checkpoint baseline and best-000000 --immutable, then
best. Keep a foreground serve process; a trial plan moves parts, verifies intent and scores
with expect predicates. Require zero overlaps/outside and the objective policy before commit.
A keeper must also pass DRC before saving its immutable generation and replacing best.
Restore best on rejection. For crash/timeout/transport loss, inspect commit_state, restore and
verify bytes/nets/score before resuming. Journal infrastructure outcomes separately from design
quality. kce supplies the primitives; your controller owns seed, journal and acceptance policy.

### Part missing from the libraries

Search normalized names and exposed-pad variants; show candidates before drawing. Use lib sym
new, lib fp new or lib fp wizard in project-local libraries, with ${KIPRJMOD} table/model paths.
Run lib link-check and lib fp check, inspect their coverage/unsupported fields, and compare the
pin table and geometry to the datasheet. Apply/verify, inspect sync mismatches, then ERC/DRC.
Library model paths can travel with the project; existence does not prove body geometry or
assembly orientation. Native vendor-library conversion remains on the library roadmap.

### Foreign boards, simulation, high-speed routing and panels

Import Altium/Eagle boards into a new directory. Preserve the source, inspect read-back and
losses, explicitly carry rules and compare against an independent source inventory. Companion
ace (GPL-3.0-or-later) can inspect native Altium files. A board-only import does not reconstruct
a schematic or establish cross-format equivalence. Before any schematic sync, verify the
source-derived intent and review a dry-run pcb update --keep-extra.

Simulate filters in a scratch project first. Check SPICE node 0, AC stimulus, exact probe names,
warnings and analytical results. For high-speed blocks use the order rules, pair route,
shorter-leg tune, fence, stitch, scoped teardrops, zone fill, DRC, length report. Panelize last
to a separate board; inspect effective mode/spacing, unchanged source hash and panel DRC before
a panel package. Manufacturer V-score capability and controlled impedance need independent review.

## Command groups

| Group | What it does |
|---|---|
| `project` | new (from templates), import foreign boards, info, files, text variables, net classes, design rules, lib tables, variants, archive, compare revisions |
| `sch` | place/move/rotate symbols, fields, wires, labels, power, no-connects, sheets, annotate, nets, intent `apply`/`verify`; kicad-cli `erc`, `export *`, `upgrade` |
| `pcb` | inspect, update from schematic, score, legalize, place near, route, autoroute, diff pairs, length tuning, via stitching/fencing, teardrops, zones and keepouts, outline, layers, 3D models, panelization, barcodes, holes, fiducials, DSN/SES; kicad-cli `drc`, `render`, `export *`, `import`, `upgrade` |
| `lib` | search and inspect symbols/footprints, suggest footprints, create/wizard, pin/pad checks, portable library models; kicad-cli svg export, upgrade |
| `check` | ERC, DRC (zone refill + schematic parity), `diagnose` with fix hints |
| `fab` | manufacturing packages (generic, JLCPCB, PCBWay, OSH Park), parts/BOM checks, price and revision gates, manifests |
| `jobset` | create and run `.kicad_jobset` files |
| `preview` | SVG/PNG previews rendered by kicad-cli, revision diff bundles, live sessions |
| `gui` | drive a running KiCad GUI through its IPC API: status, selection, actions, save/reload/refill |
| `sim` | SPICE simulation with ngspice: directives, models, netlist, run |
| `session` | status, undo/redo/history, save/discard, checkpoint/list/restore/drop |
| `run` | atomic JSON plans with pre-commit acceptance predicates |
| `serve` | resident JSON-RPC over stdio, warm pcbnew worker, deadlines and cancellation |
| `errors` | stable error types, categories, exit codes and recovery commands |
| `raw` | S-expression query/edit on any KiCad file; run pcbnew Python |
| `repl` | interactive shell, the default when no subcommand is given |
| `info` / `skill` | doctor/capabilities, versions, command metadata and public schemas / install the agent skill |

### Netlist intents: `sch apply` and `sch verify`

Describe the circuit by its netlist instead of its geometry
([format](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/netlist-intent.schema.json)):

```bash
kce sch apply intent.json            # place symbols, set fields, label every net
kce sch verify --expect intent.json  # {"match": true, ...}
```

Nets are matched by pin sets, so KiCad's auto-generated names are reported as `renamed`, not
as errors. Without `--strict` a mismatch still exits 0; `--strict` exits with the `design`
category (3). `sch apply` is idempotent (applying the same file twice changes no byte),
`--power-flags` adds a PWR_FLAG where ERC needs one, `--prune` removes symbols and labels the
intent does not mention, and its output ends with the `sch verify` result. Version 1 supports
explicit no_connect and power_flags, remove lists and component attributes. For a small ECO,
use apply --patch and verify --subset --strict; optional --check-lib-id, --check-uuid and
--check-net-names add identity gates.

For a custom or vendor part, start with `lib sym search` and `lib fp search`, inspect
unregistered files with `lib sym show FILE.kicad_sym:NAME` / `lib fp show FILE.kicad_mod`,
then run `lib link-check LIB:SYM FP:NAME` and `lib fp check FP:NAME` before placement.
`lib fp model set FP:NAME body.step --copy-into 3d` makes the model travel with the project.
The generated skill includes the complete library creation and verification chain.
[Library and source-contract roadmap](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/LIBRARY-INTENT-ROADMAP.md) records conversion
coverage, named limitations and deferred scenario items.

Full reference: [`skills/kicad-cli-extended/COMMANDS.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/COMMANDS.md).
Design and KiCad behaviours: [`docs/ARCHITECTURE.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/ARCHITECTURE.md).

## Plans and resident sessions

`kce --json run plan.json` executes `{"version":1,"steps":[{"args":["sch","set-value","R1","330"]}]}`
against `--project` or the plan's optional `project` path. Steps share a session; one final save
validates the edited files with KiCad. The result contains each step's JSON. A failed step or
validation restores all project file bytes and history. `--dry-run` leaves them untouched.
The [plan schema](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/plan.schema.json) defines the format. Steps must stay within the
project; commands that switch projects, change global configuration, start sessions, run
arbitrary Python, or upgrade files directly are rejected.

`kce session checkpoint NAME` saves project files, including binary files and excluding outputs/.
`kce session restore NAME` validates the restored files and records undo history, including
file additions and deletions. Missing names are `input` errors. Both support `--dry-run`.
Checkpoints live in the project's state directory; VCS metadata, lock files, and kce state
are excluded from their contents. Save pending REPL edits before making a checkpoint.

`kce serve` accepts one JSON-RPC 2.0 request per stdin line. Method `run` takes
`{"args":["pcb","info"],"project":"blink"}` and returns the same JSON as the CLI,
including array results. Each request uses fresh project state; board requests reuse one
pcbnew worker. Errors contain `data.kce` with the CLI error object, plus `data.category`
and `data.exit_code`. `shutdown` or EOF closes the server and worker. Only responses reach
stdout; JSON-RPC notifications produce no response. A startup `--project` supplies the default.

With piped stdin, `kce --json repl` emits one JSON line per command, including help and
error objects. `quit`, `exit`, and `q` emit `{"quit":true}`. Blank lines and comments emit
nothing. Edits stay in the shared session until `session save` (or `project save`); EOF discards unsaved edits.

## JSON output contract

* On success, `--json` (before or after the subcommand) prints one JSON document on stdout. Parse failures produce one JSON error on stderr. Design and comparison gates can retain their result on stdout when they fail.
* Exit code `0` on success. On failure: one JSON object on stderr, validated by
  [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json):
  `{"error": "...", "type": "KeyError", "category": "input", "hint": "kce sch symbols"}` with an
  optional `details` object. The message says what to run next. `type` values are stable names.
* The `category` (and the exit code) separates an infrastructure failure from a design rejection:

  | category | exit | meaning | `type` values |
  |---|:-:|---|---|
  | `input` | 2 | bad arguments, unknown reference or pin, missing file, malformed input file, locked item | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked` (unlock it or pass `--force`), `RuntimeError` (no project loaded, project locked by another process, nothing to undo) |
  | `design` | 3 | refused because the result would be wrong | `NetMergeError` (an edit would join unrequested nets), `NetSplitError` (a symbol swap would lose connections), `RouteBlocked` (no clean wire route), `DesignMismatch` (strict intent verification), `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC failed) |
  | `validation` | 4 | KiCad rejected a saved file (the mutation was rolled back), or an explicit comparison gate found unexpected changes | `ValidationError` |
  | `crash` | 5 | kicad-cli (any other non-zero exit) or the pcbnew worker died or returned garbage | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` exceeded `KCE_TIMEOUT` (seconds, default 1800), or a pcbnew worker op exceeded its 3600 s budget | `KicadTimeout`; `ToolTimeout` (Freerouting, ngspice or kikit exceeded its budget) |
  | `environment` | 7 | KiCad or another tool was not found, or an OS-level I/O failure | `KicadNotFound`, `OSError`, `RuntimeError` (Freerouting missing) |
  | `internal` | 1 | a bug in kce (report it); details.traceback holds the stack | `KicadError` (worker exception), `RuntimeError` (unexpected), `InternalError`, `TypeError`, `IndexError` |
* `kce --json errors` lists every stable type with its category, exit code and recovery command.
* Passthrough exports report files in `outputs[]` with `path`, `bytes` and the `format`
  detected from the file content (Gerbers include parsed X2 attributes). `fab package` uses `files[]`.
* A failed storage restoration reports `rolled_back:false` and `commit_state:unknown`; repair
  storage and restore an accepted checkpoint before retrying a mutation.
* Commands that change files save automatically, validated by KiCad, and are undoable with
  `kce session undo`. `--dry-run` executes on isolated copies and returns `dry_run:true` and `would_write`.
* When stdout is not UTF-8 (e.g. a cp1252 Windows console), non-ASCII characters are
  `\u`-escaped so the JSON stays valid.
* Caller-assigned ids survive repeated edits: `sch add --uuid UUID` sets a symbol instance's
  uuid (a malformed uuid, or one already used in the project, is rejected), and
  `pcb update --stable-uuids` assigns every symbol-linked footprint, including existing ones,
  `uuid5(6ba7b811-9dad-11d1-80b4-00c04fd430c8, symbol_path)`. IDs converge after one run.
* `sch nets` follows [schemas/sch-nets.schema.json](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/sch-nets.schema.json).
  Each net's `code` is `null` when KiCad supplies no code. Compare pin sets and names;
  net codes are not persistent identities.

## Configuration

| Variable | Purpose |
|---|---|
| `KCE_KICAD_CLI` (alias `KICAD_CLI`) | path to `kicad-cli` |
| `KCE_PYTHON` | Python interpreter that can `import pcbnew` |
| `KCE_SHARE_DIR` | KiCad data directory (`share/kicad`) |
| `KICAD10_SYMBOL_DIR`, `KICAD10_FOOTPRINT_DIR`, `KICAD10_3DMODEL_DIR`, `KICAD10_TEMPLATE_DIR` | KiCad's own library path variables |
| `KICAD_CONFIG_HOME` | KiCad's config directory |
| `KCE_NO_VALIDATE=1` | skip re-loading saved files through kicad-cli |
| `KCE_LOCK_TIMEOUT` | seconds to wait for another `kce` editing the same project (default 120) |
| `KCE_TIMEOUT` | seconds before a single `kicad-cli` invocation is abandoned (default 1800) |
| `KCE_SESSION_DIR` | external base directory for per-project history/checkpoints; `--session-dir` overrides it |
| `KCE_NGSPICE` | explicit ngspice executable; otherwise PATH executables are tried |
| `KCE_LIBNGSPICE` | ngspice shared library fallback when no executable is found |
| `FREEROUTING_JAR`, `JAVA` | optional Freerouting autorouter |

Explicit variables always win over auto-detection. Per-project state (undo history, previews)
lives in `<project>/.kicad-cli-extended/` unless an external session directory is selected.
Test-only variables are KCE_DEMOS, KCE_FORCE_INSTALLED, KCE_KEEP_ARTIFACTS, KCE_GUI_TESTS
and KCE_COLD_CLI. The cold-evidence recorder uses KCE_AUDIT_CLI and KCE_AUDIT_LOG.
Their exact scope is documented in [Testing](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md#test-configuration).

## Development and tests

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # KiCad demo projects for the end-to-end tests
python -m pytest tests/test_core.py            # unit tests (no KiCad needed)
python scripts/gen_skill.py --check            # generated skill/reference/schema gate
KCE_FORCE_INSTALLED=1 python -m pytest tests   # full suite against real KiCad
```

After a KiCad update, `python scripts/gen_kicad_cli_spec.py` regenerates the command spec; the
coverage test fails until every kicad-cli command and option is reachable. Details:
[`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md).

## License

GPL-3.0-or-later. kicad-cli-extended is an independent open-source project, not affiliated
with or endorsed by the KiCad project. KiCad is a trademark of the KiCad project.
`src/kicad_cli_extended/core/sexpr.py` contains a port of KiCad's
`KICAD_FORMAT::Prettify` (KiCad is GPL-3.0-or-later), and the bundled command spec and jobset
catalog are derived from KiCad 10.0.7. See [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE).

## PowerShell and CI

Use argument arrays in PowerShell. Plans replace shell-specific loops:

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# Attach negative comma-separated values with '=': --rotate=-45,0,45
```

[Hardware PR workflow](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) and
[release artifact workflow](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml) use Linux and Windows with
`fetch-depth: 0`, Bash, KiCad 10.0.7 and a required reviewed kce commit pin.
Copy the templates into `.github/workflows/`. Replace PROJECT and REPLACE_WITH_REVIEWED_SHA.
Pin the Linux apt package versions for your runner image too; the version assertion rejects
an unexpected KiCad version. These are templates, not a claim of a completed Windows run.
Add your project's MPN, LCSC, assembly and cost policy before treating artifacts as order-ready.
Release artifacts are uploaded for review. Publishing a release or paying a manufacturer is a separate action.

Add these rules to `.gitignore` before CI runs:

```gitignore
out/
outputs/
*.kicad_prl
~*.lck
*-backups/
fp-info-cache
.kicad-cli-extended/
```

Use `.gitattributes` to keep native design files consistent across operating systems:

```gitattributes
*.kicad_* text eol=lf
```

Editing, netlists, ERC and DRC run without a display. On Linux, 3D rendering may require
`xvfb-run -a kce ... pcb render`. Check `info doctor --require step,render` before a 3D job.
The JSON contract table above defines exits 1 through 7; a design gate exits 3 while retaining
its stdout result. Never treat a missing result or a timeout as a clean design.
