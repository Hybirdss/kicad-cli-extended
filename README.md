<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**KiCad's official `kicad-cli`, extended for AI agents and hardware automation.**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

**English** | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[Benchmark](#benchmark) · [Comparison](#kicad-cli-vs-kicad-cli-extended) · [Install](#requirements-and-install) · [Quickstart](#build-your-first-led-board) · [Agent Skill](#ai-agent-skill) · [Workflows](#workflows) · [Commands](#command-groups) · [JSON Contract](#json-output-contract) · [CI & PowerShell](#powershell-and-ci)

</div>

> **Working with Altium Designer?** See [altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended), the companion tool. Both projects use GPL-3.0-or-later.

**`kce`** (KiCad CLI Extended) wraps every official `kicad-cli` command and option, and extends it with schematic editing, netlist-first circuit assembly, PCB sync, placement scoring, routing, fabrication packaging, revision diffs, transactional plans, and an AI agent skill — all speaking a strict JSON contract.

It drives the real **KiCad 10** (`kicad-cli` and KiCad's `pcbnew` Python module) and re-loads every file it writes through `kicad-cli`, so KiCad itself validates each mutation before commit.

---

## Benchmark

`kce` carries **157 always-loaded skill tokens** (3,737 with the whole `SKILL.md`), compared with **4,927–63,420 tool-definition tokens** for the two public KiCad MCP servers selected by stars on 2026-10-28. The full command reference costs 11,238 tokens when opened on demand.

Measured on 2026-10-30 with KiCad 10.0.7, Python 3.11.17 on a 16-CPU Linux machine, `tiktoken` 0.14.0 (`o200k_base`): 5 interleaved repetitions on fresh copies of KiCad's `pic_programmer` demo. Every repetition started below the 4.00 load gate (recorded loads: 2.02, 3.38, 3.54, 3.45, 3.36). Tokens are median request plus response; time is median wall clock. CLI calls start a new process; MCP servers are already running, with startup reported separately. See [Method](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md), [summary and ranges](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md), and [all recorded runs](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json).

| Approach | Exposed Tools | Context Tokens Before Any Work |
|---|---:|---:|
| **`kce` (skill frontmatter)** | 1 command | **157** |
| `kce` (full `SKILL.md`, includes frontmatter) | 1 command | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3 (2,995★ at selection), all tools | 244 | **63,420** |
| `mixelpixx`, `core-toolbox` config | 37 | **6,445** |
| `lamaalrajih/kicad-mcp` (531★ at selection) | 16 | **4,927** |

| Task (on `pic_programmer` demo) | `kce` (tokens / time) | `mixelpixx` (tokens / time) | `lamaalrajih` (tokens / time) |
|---|---|---|---|
| ERC summary | 108 / 3.62 s | 69 / 3.09 s | no tool |
| DRC | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + drill | 2,771 / 2.15 s | 645 / 1.43 s | no tool |

All 50 supported runs succeeded and produced the expected output on disk (BOM, Gerber, drill files) or an ERC/DRC violation count. Per-task tokens exclude context overhead; these are measurements of the listed calls and responses on one demo rather than a full agent session.

---

## `kicad-cli` vs. `kicad-cli-extended`

Derived from the `--help` tree of KiCad 10.0.7's `kicad-cli` (43 commands, 582 options; stored in [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json)).

| Capability | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| Exports & checks (ERC, DRC, Gerber, drill, BOM, STEP, PDF, render, …) | ✓ | ✓ All 43 commands, every option passed through |
| Structured JSON output for every command | Reports for `erc`, `drc`, `export stats`, `import` only | ✓ Uniform JSON contract & error categories |
| Schematic editing: place, wire, label, annotate | | ✓ |
| Netlist-first circuit assembly (`sch apply` / `sch verify`) | Netlist export only | ✓ Intent JSON, patch/subset & identity checks |
| Update PCB from schematic (`pcb update`) | `drc --schematic-parity` check only | ✓ Stable UUIDs, pad-move & track inspection |
| Placement scoring & bounded repair | | ✓ `score`, `legalize`, `place near` |
| Routing, autoroute, zones, board outline | Zone refill in `drc` / plots only | ✓ |
| Differential pairs, length/skew rules & tuning | DRC checks only | ✓ Rules, coupled routing, meanders |
| Via fences/stitching, teardrops, rule areas | | ✓ |
| Net classes & design rules | Read only | ✓ Read & write |
| Fab packages (JLCPCB, PCBWay, OSH Park) | Jobsets, no vendor presets | ✓ Staged atomic release packages & BOM/CPL gates |
| Previews & revision diff (`project compare`) | Renders & plots only | ✓ Geometry diffs, allow-lists, visual bundles |
| Atomic plans (`run`) & named checkpoints (`session`) | | ✓ Pre-commit acceptance predicates & restore |
| Resident JSON-RPC worker (`serve`) | | ✓ Warm `pcbnew` worker, deadlines, cancellation |
| Library search, custom symbols/footprints & wizards | | ✓ Pin/pad/model verification |
| Panelization | | ✓ Mouse bites & V-score to a separate board |
| Altium / Eagle & foreign board import | ✓ Board conversion | ✓ Target selection, read-back & preservation checks |
| Live GUI control via KiCad IPC API | | ✓ |
| SPICE simulation (`ngspice`) | | ✓ |
| AI agent skill (`kce skill install`) | | ✓ |

---

## Requirements and install

* **KiCad 10.x** (`kicad-cli` + its bundled `pcbnew` Python module).
* **Python 3.10+** for `kce` itself. `pcbnew` runs inside KiCad's own interpreter, detected automatically.
* *Note:* PLY 3D export requires KiCad built with OCCT 7.7 or newer; older builds still export STEP.

| OS | Install KiCad 10 |
|---|---|
| **Windows** | [Official installer](https://www.kicad.org/download/windows/), `choco install kicad`, or `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad` (or the `.dmg`) |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad` (detected automatically) |

Install `kce` from the public repository:

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # recommended
# or
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

Verify your environment and initialize default library tables (first run only):

```bash
kce --json info doctor          # kicad-cli, pcbnew interpreter, library dirs: "ok": true
kce lib init-global-tables      # first run only: KiCad's default library tables
```

---

## Build your first LED board

This builds a resistor-limited LED board powered through a 2-pin connector (3.3 V supply, 330 Ω resistor). Start in an empty directory after the installation checks above. Net labels connect pins cleanly without crossing unrelated wires:

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

**What to verify:**
* **3 components, 3 two-pin nets**, zero ERC/DRC errors, and zero unconnected or parity issues.
* `pcb score` reports zero courtyard overlaps and zero out-of-outline parts.
* `fab package` returns `complete: true`, an empty `failed_steps`, and a non-empty `files` list under `outputs/fab-jlcpcb/`.
* *Manufacturing note:* This is a bare-board example. For assembly, select verified supplier parts and review the CPL in the fabricator's placement viewer before ordering. A clean DRC does not imply manufacturer rule qualification. Use `pcb render` after `info doctor --require render` if you need a 3D preview.

> **Interactive & discovery:** Running `kce` without arguments opens an interactive REPL shell. Use `kce --help`, `kce <group> --help`, `kce --json info commands --full`, and `kce --json info describe pcb score` to inspect the command tree.

---

## Review an ECO and release a package

For a verified schematic ECO adding `C10` near `U2` on net `VCC`, checkpoint the PCB, preview the sync, then scope placement and routing to the affected ref:

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

* **Sync safety:** Inspect `replaced[].pads_moved`, `unmatched_pins`, and `tracks_ending_on_old_pads` in the dry run before applying. An approved footprint swap that moves pads requires omitting `--lock-existing`. Replacements preserve footprint UUIDs and existing tracks.
* **Scoped gates:** Scoped operations keep other footprints fixed without altering their saved lock flags. `project compare --fail-on-unexpected` rejects changes outside the `--allow` list with exit code `4`.
* **Foreign board intake:** Use `project import SOURCE --dir NEW` or `--project TARGET pcb import SOURCE`, then inspect `read_back` and the source-preservation report.
* **Release packaging:** Run `fab parts check --offline` and `fab bom-check --require MPN,LCSC,Footprint`, then build the release with `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE`. Exports run in staging and publish only when every requested output succeeds (`complete: true`); failures return `complete: false` and `failed_steps` with exit code `3`, preserving any previously accepted package. Review `cpl.review` in the fabricator's viewer. See the [board scenario roadmap](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/BOARD-GAPS-ROADMAP.md) for planned fab capability presets.

---

## AI agent skill

`kce` ships a built-in agent skill (`SKILL.md` + `COMMANDS.md`) that teaches coding agents the workflows and verification gates in this guide.

```bash
kce skill install                                 # every agent found on this machine + ~/.agents/skills
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # project-local
kce skill status
```

Supported targets: `agents` (`~/.agents/skills`), `claude` (`~/.claude/skills`), `codex` (`~/.codex/skills`), `gemini` (`~/.gemini/skills`), or any custom path via `--dir`. Files are copied directly so installation works identically on Windows, macOS, and Linux. The skill also lives in [`skills/kicad-cli-extended/`](https://github.com/Hybirdss/kicad-cli-extended/tree/main/skills/kicad-cli-extended) for tools that ingest skills from GitHub.

---

## Workflows

The [installed skill](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe) provides twelve trigger-driven recipes, documented in full in [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md). Always pass explicit check gates (`--fail-on-error`, `--fail-on-drc`, `--strict`): ERC and DRC default to report-only.

| Goal | Key Commands | Section |
|---|---|---|
| Build a new board & export fab files | `project new` → `sch apply` → `pcb update` → `fab package` | [First board and fabrication](#first-board-and-fabrication) |
| Drive a multi-stage design from netlist JSON | `session checkpoint --immutable` → `run stage.json` | [Netlist-first board from a specification](#netlist-first-board-from-a-specification) |
| Apply & verify a scoped ECO | `pcb update --keep-extra --lock-existing` → `project compare` | [Engineering change on an existing project](#engineering-change-on-an-existing-project) |
| Gate PRs and tag hardware releases in CI | `check erc` → `check drc` → `fab bom-check` → `fab package` | [Continuous integration and tagged releases](#continuous-integration-and-tagged-releases) |
| Search placements with a warm `pcbnew` worker | `serve` → `run trial.json` → `session restore best` | [Placement search with a warm worker](#placement-search-with-a-warm-worker) |
| Create & verify missing symbols/footprints | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [Part missing from the libraries](#part-missing-from-the-libraries) |
| Import boards, simulate SPICE, tune high-speed, panelize | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [Foreign boards, simulation, high-speed routing and panels](#foreign-boards-simulation-high-speed-routing-and-panels) |

### First board and fabrication

Follow the LED quickstart above, then assign verified supplier IDs for assembly. For USB-C designs, inspect the connector's actual pin map and intended power role before writing the intent:
1. Search and inspect parts (`lib sym search`, `lib fp show`), create a checkpoint, and dry-run `sch apply`.
2. Run `sch apply`, `sch verify --strict`, and `check erc --fail-on-error`.
3. Define the board outline, dry-run `pcb update`, place connector openings manually with `pcb move` and `pcb lock` (automatic connector edge placement is not implemented), and set fabrication limits via `project rules` and `project netclass`.
4. Score placement, route, pour zones, gate with `check drc --fail-on-error`, and run `fab package`. Accept only `complete: true` and review BOM, CPL orientation, and Gerber layers before ordering.

### Netlist-first board from a specification

Keep the approved electrical intent in JSON inside the project (captured in the initial checkpoint), and keep plans, journals, and independent oracles outside the project directory. Execute every stage as an atomic `run` plan with acceptance predicates followed by an immutable checkpoint:

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

* **Schematic stage:** Applies intent, annotates, verifies strictly, and gates ERC.
* **Board stage:** Sets layers and outline, syncs with `--stable-uuids`, and scores placement (e.g., `expect: [{"path": "courtyard_overlaps.count", "op": "eq", "value": 0}]`).
* **Recovery:** If a later review rejects a committed stage, restore its preceding immutable checkpoint. Repeating a sync preserves the accepted footprint identity map.

### Engineering change on an existing project

1. Capture baseline nets, footprints/pads, placement score, and ERC/DRC, then create checkpoint `pre-eco`.
2. Rehearse and run the schematic plan with read-backs and strict intent verification (`sch verify --subset --strict`) before syncing the PCB.
3. Scope placement and routing to the ECO components, then run `project compare checkpoint:pre-eco` (or `git:HEAD`) with `--geometry` and an explicit `--allow` list.
4. Verify pin nets, positions, UUIDs, pad displacement, and routing on allowed refs. On regression, `session restore pre-eco` and verify restored bytes, nets, and score. Generate new outputs into a fresh directory (checkpoints exclude `outputs/`; pass `project archive --include-outputs` when archiving a release).

### Continuous integration and tagged releases

Use the [PR workflow template](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) and [hardware release workflow template](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml):
* Pin tool versions, fetch the base revision (`fetch-depth: 0`), gate ERC/DRC with explicit thresholds, compare against base, generate previews, check BOM fields/prices, and require a matching title-block revision before packaging.
* Preserve `stdout` alongside `stderr` on `design` (exit `3`) failures. `jobset execute` exits `3` on failed jobs by default; use `fab package` when assembly outputs are needed.
* Rehearse both a clean design and a seeded bad design, assert a clean Git tree, and inspect emitted files and SHA-256 hashes.

### Placement search with a warm worker

1. Verify intent and baseline ERC/DRC, then create immutable checkpoints `baseline` and `best-000000`, plus mutable `best`.
2. Keep a foreground `kce serve` process warm. Run each candidate placement as a trial plan that moves parts, verifies intent, and checks `pcb score` via `expect` predicates (requiring zero courtyard overlaps and zero out-of-outline parts).
3. Require a keeper candidate to pass DRC before saving its immutable generation and updating `best`. Restore `best` on rejection.
4. On crash, timeout, or transport loss, inspect `commit_state`, restore `best`, and verify bytes/nets/score before resuming.

### Part missing from the libraries

1. Search normalized names and exposed-pad variants (`lib sym search`, `lib fp search`) and inspect candidates before drawing custom parts.
2. Create symbols and footprints in project-local libraries via `lib sym new`, `lib fp new`, or `lib fp wizard` using `${KIPRJMOD}` table and 3D model paths.
3. Run `lib link-check` and `lib fp check`, inspect `coverage` and `unsupported` fields, and verify the pin table and pad geometry against the datasheet before running `sch apply`, `pcb update`, and ERC/DRC.

### Foreign boards, simulation, high-speed routing and panels

* **Foreign board import:** Import Altium or Eagle boards into a new directory (`project import` or `pcb import`). Preserve the source, inspect `read_back` and losses, carry design rules explicitly, and compare against an independent source inventory (companion tool [`ace`](https://github.com/Hybirdss/altium-cli-extended) can inspect native Altium files directly). A board-only import does not reconstruct a schematic or prove cross-format equivalence.
* **SPICE simulation:** Simulate subcircuits in a scratch project first (`sim`). Check SPICE node `0`, AC stimulus, exact probe names, simulator warnings, and analytical expectations.
* **High-speed routing:** Configure rules first, route differential pairs, tune the shorter leg with meanders, add via fences and stitching, apply scoped teardrops, refill zones, run DRC, and inspect the length report.
* **Panelization:** Panelize last into a separate board file (`pcb panelize`); verify effective mode/spacing, unchanged source board hash, and panel DRC before packaging.

---

## Command groups

| Group | What it does |
|---|---|
| `project` | Create from templates, import foreign boards, info, files, text variables, net classes, design rules, lib tables, variants, archive, compare revisions |
| `sch` | Place/move/rotate symbols, fields, wires, labels, power, no-connects, sheets, annotate, nets, intent `apply`/`verify`; `kicad-cli` `erc`, `export *`, `upgrade` |
| `pcb` | Inspect, update from schematic, score, legalize, place near, route, autoroute, diff pairs, length tuning, via stitching/fencing, teardrops, zones & keepouts, outline, layers, 3D models, panelization, barcodes, holes, fiducials, DSN/SES; `kicad-cli` `drc`, `render`, `export *`, `import`, `upgrade` |
| `lib` | Search & inspect symbols/footprints, suggest footprints, create/wizard, pin/pad checks, portable 3D models; `kicad-cli` SVG export & upgrade |
| `check` | ERC, DRC (with zone refill & schematic parity), `diagnose` with actionable fix hints |
| `fab` | Manufacturing packages (`generic`, `jlcpcb`, `pcbway`, `oshpark`), parts/BOM checks, price & revision gates, manifests |
| `jobset` | Create and execute `.kicad_jobset` files |
| `preview` | SVG/PNG previews rendered by `kicad-cli`, revision diff bundles, live preview sessions |
| `gui` | Drive a running KiCad GUI through its IPC API: status, selection, actions, save/reload/refill |
| `sim` | SPICE simulation with `ngspice`: directives, models, netlist, run |
| `session` | Status, undo/redo/history, save/discard, checkpoint/list/restore/drop |
| `run` | Execute atomic multi-step JSON plans with pre-commit acceptance predicates |
| `serve` | Resident JSON-RPC 2.0 server over stdio with a warm `pcbnew` worker, deadlines, and cancellation |
| `errors` | Query the catalog of stable error types, categories, exit codes, and recovery commands |
| `raw` | S-expression query/edit on any KiCad file; run `pcbnew` Python |
| `repl` | Interactive shell (default when no subcommand is given) |
| `info` / `skill` | Environment `doctor`, capabilities, versions, command metadata, offline JSON schemas / install the AI agent skill |

### Netlist intents: `sch apply` and `sch verify`

Describe a circuit by its connections instead of schematic coordinates ([schema](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/netlist-intent.schema.json)):

```bash
kce sch apply intent.json            # place symbols, set fields, label every net
kce sch verify --expect intent.json  # {"match": true, ...}
```

* **Pin-set matching:** Nets are matched by pin sets, so KiCad's auto-generated net names are reported as `renamed` rather than errors. Without `--strict`, a mismatch exits `0`; `--strict` exits with category `design` (`3`).
* **Idempotent & incremental:** Applying the same file twice changes zero bytes. `--power-flags` adds `PWR_FLAG` symbols where ERC requires them; `--prune` removes symbols and labels omitted from the intent. For small ECOs, use `sch apply --patch` and `sch verify --subset --strict`, with optional `--check-lib-id`, `--check-uuid`, and `--check-net-names` identity gates.
* **Custom library parts:** Use `lib sym search` and `lib fp search`, inspect unregistered files with `lib sym show FILE.kicad_sym:NAME` / `lib fp show FILE.kicad_mod`, and run `lib link-check LIB:SYM FP:NAME` and `lib fp check FP:NAME` before placement. `lib fp model set FP:NAME body.step --copy-into 3d` bundles the 3D model inside the project. See the [library and source-contract roadmap](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/LIBRARY-INTENT-ROADMAP.md).

Full command reference: [`skills/kicad-cli-extended/COMMANDS.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/COMMANDS.md). Architecture and internals: [`docs/ARCHITECTURE.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/ARCHITECTURE.md).

---

## Plans and resident sessions

* **Transactional plans (`kce run`):** `kce --json run plan.json` executes `{"version":1,"steps":[{"args":["sch","set-value","R1","330"]}]}` in a shared session and validates all edited files with KiCad on final save. Any step or validation failure rolls back all project file bytes and history. `--dry-run` leaves files untouched. See [`schemas/plan.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/plan.schema.json).
* **Named checkpoints (`kce session`):** `kce session checkpoint NAME` snapshots project files (including binary files, excluding `outputs/`, VCS metadata, lock files, and `kce` state). `kce session restore NAME` validates restored files and records undo history, including file additions and deletions. Both support `--dry-run`.
* **Resident JSON-RPC server (`kce serve`):** Accepts one JSON-RPC 2.0 request per stdin line (`{"jsonrpc":"2.0","id":1,"method":"run","params":{"args":["pcb","info"],"project":"blink"}}`), reusing a warm `pcbnew` worker across board requests. Errors include `data.kce`, `data.category`, and `data.exit_code`.
* **Piped REPL (`kce --json repl`):** Emits one JSON line per command. Edits remain in the shared session until `session save` (or `project save`); EOF discards unsaved edits.

---

## JSON output contract

* **Success (`0`):** `--json` (before or after the subcommand) prints one JSON document on `stdout`. Design and comparison gates retain their diagnostic JSON on `stdout` even when exiting non-zero.
* **Failure (`1`–`7`):** Emits one JSON error object on `stderr`, validated by [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json):
  `{"error": "...", "type": "KeyError", "category": "input", "hint": "kce sch symbols"}` with an optional `details` object.
* **Error categories and exit codes:**

  | category | exit | meaning | `type` values |
  |---|:-:|---|---|
  | `input` | 2 | bad arguments, unknown reference or pin, missing file, malformed input file, locked item | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked` (unlock it or pass `--force`), `RuntimeError` (no project loaded, project locked by another process, nothing to undo) |
  | `design` | 3 | refused because the result would be wrong | `NetMergeError` (an edit would join unrequested nets), `NetSplitError` (a symbol swap would lose connections), `RouteBlocked` (no clean wire route), `DesignMismatch` (strict intent verification), `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC failed) |
  | `validation` | 4 | KiCad rejected a saved file (the mutation was rolled back), or an explicit comparison gate found unexpected changes | `ValidationError` |
  | `crash` | 5 | kicad-cli (any other non-zero exit) or the pcbnew worker died or returned garbage | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` exceeded `KCE_TIMEOUT` (seconds, default 1800), or a pcbnew worker op exceeded its 3600 s budget | `KicadTimeout`; `ToolTimeout` (Freerouting, ngspice or kikit exceeded its budget) |
  | `environment` | 7 | KiCad or another tool was not found, or an OS-level I/O failure | `KicadNotFound`, `OSError`, `RuntimeError` (Freerouting missing) |
  | `internal` | 1 | a bug in kce (report it); details.traceback holds the stack | `KicadError` (worker exception), `RuntimeError` (unexpected), `InternalError`, `TypeError`, `IndexError` |

* **Additional guarantees:**
  * `kce --json errors` lists every stable error type with its category, exit code, and recovery command.
  * Passthrough exports report generated files in `outputs[]` (`path`, `bytes`, content-detected `format`, plus parsed Gerber X2 attributes); `fab package` reports `files[]`.
  * Failed storage restoration reports `rolled_back: false` and `commit_state: unknown`; repair storage and restore an accepted checkpoint before retrying.
  * Mutations auto-save, validate with KiCad, and support `kce session undo`. `--dry-run` runs on isolated copies and returns `dry_run: true` and `would_write`.
  * Non-UTF-8 consoles (such as Windows `cp1252`) automatically `\u`-escape non-ASCII characters so JSON output stays valid.
  * Stable IDs survive repeated edits via `sch add --uuid UUID` and `pcb update --stable-uuids` (`uuid5(6ba7b811-9dad-11d1-80b4-00c04fd430c8, symbol_path)`).
  * `sch nets` follows [`schemas/sch-nets.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/sch-nets.schema.json); compare pin sets and net names rather than transient net `code` numbers.

---

## Configuration

| Environment Variable | Purpose |
|---|---|
| `KCE_KICAD_CLI` (alias `KICAD_CLI`) | Explicit path to `kicad-cli` |
| `KCE_PYTHON` | Python interpreter capable of `import pcbnew` |
| `KCE_SHARE_DIR` | KiCad data directory (`share/kicad`) |
| `KICAD10_SYMBOL_DIR`, `KICAD10_FOOTPRINT_DIR`, `KICAD10_3DMODEL_DIR`, `KICAD10_TEMPLATE_DIR` | KiCad library directory overrides |
| `KICAD_CONFIG_HOME` | KiCad configuration directory |
| `KCE_NO_VALIDATE=1` | Skip re-loading saved files through `kicad-cli` |
| `KCE_LOCK_TIMEOUT` | Seconds to wait for another `kce` process editing the same project (default `120`) |
| `KCE_TIMEOUT` | Seconds before a single `kicad-cli` invocation times out (default `1800`) |
| `KCE_SESSION_DIR` | External base directory for per-project history and checkpoints (overridden by `--session-dir`) |
| `KCE_NGSPICE` / `KCE_LIBNGSPICE` | Explicit `ngspice` executable or shared library fallback |
| `FREEROUTING_JAR`, `JAVA` | Optional Freerouting autorouter JAR and Java runtime |

Explicit environment variables always take precedence over auto-detection. Per-project state (undo history, checkpoints, previews) lives in `<project>/.kicad-cli-extended/` unless `KCE_SESSION_DIR` or `--session-dir` is set. Test-only variables (`KCE_DEMOS`, `KCE_FORCE_INSTALLED`, `KCE_KEEP_ARTIFACTS`, `KCE_GUI_TESTS`, `KCE_COLD_CLI`, `KCE_AUDIT_CLI`, `KCE_AUDIT_LOG`) are documented in [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md#test-configuration).

---

## PowerShell and CI

In PowerShell, pass common flags as an argument array and use `run` plans instead of shell-specific loops:

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# Attach negative comma-separated values with '=': --rotate=-45,0,45
```

Copy the [Hardware PR workflow](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) and [release artifact workflow](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml) into `.github/workflows/`:
* Both templates use Linux and Windows runners with `fetch-depth: 0`, Bash, KiCad 10.0.7, and a pinned `kce` commit SHA (`REPLACE_WITH_REVIEWED_SHA`).
* Add your project's MPN, LCSC, assembly, and cost policies before treating artifacts as order-ready.

Add these entries to `.gitignore` before running CI:

```gitignore
out/
outputs/
*.kicad_prl
~*.lck
*-backups/
fp-info-cache
.kicad-cli-extended/
```

Keep native KiCad design files consistent across operating systems with `.gitattributes`:

```gitattributes
*.kicad_* text eol=lf
```

Editing, netlists, ERC, and DRC run headless without a display. On Linux, 3D rendering may require `xvfb-run -a kce ... pcb render`; check `info doctor --require step,render` before running 3D jobs.

---

## Development and tests

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # KiCad demo projects for end-to-end tests
python -m pytest tests/test_core.py            # unit tests (no KiCad needed)
python scripts/gen_skill.py --check            # generated skill/reference/schema gate
KCE_FORCE_INSTALLED=1 python -m pytest tests   # full suite against real KiCad
```

After a KiCad update, run `python scripts/gen_kicad_cli_spec.py` to regenerate the command specification; coverage tests fail until every `kicad-cli` command and option is reachable. See [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md).

---

## License

GPL-3.0-or-later. `kicad-cli-extended` is an independent open-source project, not affiliated with or endorsed by the KiCad project. KiCad is a trademark of the KiCad project. `src/kicad_cli_extended/core/sexpr.py` contains a port of KiCad's `KICAD_FORMAT::Prettify` (KiCad is GPL-3.0-or-later), and the bundled command spec and jobset catalog are derived from KiCad 10.0.7. See [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE).
