# Agent recipe details

Use the real KiCad 10 CLI and pcbnew through `kce`. Choose a recipe from the user's intent
without waiting for command names. Run R1 first, then combine recipes as needed.
[command reference](../skills/kicad-cli-extended/COMMANDS.md) lists every command. Inspect `info describe COMMAND` before
using unfamiliar flags. Never invent a command from a roadmap.

## Rules

* Use `--json --project P`, or work inside P. Global options also work after subcommands.
  Distances are millimetres. Schematic Y grows down; symbols snap to 1.27 mm. Pins use REF:PIN.
* Mutations auto-save and KiCad validates them. Use `--dry-run` before editing existing parts:
  isolated execution returns dry_run/would_write without changing project/history/outputs.
  `--read-only` refuses writers, exporters and control commands. Arbitrary Python, external
  jobsets and simulator control programs are refused in transactions and dry-run.
* Errors are `{error,type,category,hint,details?}` on stderr, validated by the error schema.
  Exit codes: input 2, design 3, validation 4, crash 5, timeout 6, environment 7, internal 1.
  Branch on category, not message text. `errors` lists recovery commands. See the detailed
  [JSON output contract](https://github.com/Hybirdss/kicad-cli-extended#json-output-contract).
* ERC/DRC default to report-only. Use `--fail-on error` or `--fail-on warning` in plans/CI.
  Design gates keep their result on stdout and exit 3. Read clean/errors/unconnected/parity.
  `--baseline REPORT` gates only new findings; summary/truncation does not weaken the gate.
* Checkpoint before each stage. Restore removes later project files and excludes outputs/,
  state, VCS and locks. Keep journals/plans/approved oracle copies outside P. Keep inputs
  referenced by plan steps inside P, captured before checkpointing: run rejects external
  step paths. Use absolute paths inside P when invoking from a different working directory.
* `run` is atomic for project files/history. Put exports inside P during a plan. A successful
  plan has committed: restore on later rejection. If rolled_back:false or commit_state:unknown,
  repair storage and restore an accepted checkpoint before any retry.
* Do not bypass guards with --force/--allow-merge/--overwrite/--no-validate to silence errors.
  Do not order, pay, upload or commit unless asked. Review generated images and orientation.
* `sch nets` has stable ordering, pin_type/no_connect and nullable net code. Compare pin sets.
  `sch add --uuid` preserves caller identity; `pcb update --stable-uuids` converges every
  symbol-linked footprint, including existing ones, to uuid5(namespace, symbol_path).

## Choose a scenario recipe

### R1: Orient a new session

Triggers: KiCad, Gerber, JLCPCB, my board, open this project, first PCB.
Chain: doctor, skill status, commands, project info, then describe unfamiliar commands.

```bash
kce --json info doctor
kce skill status
kce --json info commands --full
kce --json -p P project info
kce --json info describe sch no-connect
kce --json errors
```

Verify doctor.ok and capabilities/warnings. If global tables are missing, run
`lib init-global-tables`. Missing optional models can yield a bare PCB despite ok:true;
use `info doctor --require step,render` before 3D delivery. Install KiCad 10 or configure
KCE_KICAD_CLI/KCE_PYTHON when the base environment is unavailable. Run commands in the foreground.

### R2: Build a board from a specification

Triggers: make me a board, first PCB, netlist-first, intent.json, USB-C LED, ESP32/STM32.
Chain: search/show parts and pin/pad maps, project new, checkpoint initial, dry-run apply,
run apply/annotate/verify/ERC, preview schematic, checkpoint schematic-ok, outline, sync, score.

```bash
kce --json project new sensor --name sensor
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run sch apply sensor/intent.json --power-flags
kce --json -p sensor run schematic-stage.json
kce --json -p sensor sch verify --expect sensor/intent.json --strict
kce --json -p sensor check erc --fail-on error
kce --json -p sensor preview capture --recipe schematic
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor pcb outline rect --at 100,100 --size 50,40
kce --json -p sensor --dry-run pcb update --stable-uuids
kce --json -p sensor pcb update --stable-uuids
kce --json -p sensor pcb score
```

Create sensor/intent.json before using it. Its schema is `info schema netlist-intent`.
Version 1 accepts components/nets plus no_connect, power_flags, remove and component attributes.
Use `sch apply` followed by `sch verify --expect ... --strict` inside schematic-stage.json;
require match:true and clean ERC. Inspect missing_footprints/unmatched_pins and overflow in sync.
Repeat apply must report changes:0. For patch ECOs use `--patch` and verify `--subset --strict`.
Optional verify flags --check-lib-id/--check-uuid/--check-net-names enforce identity too.
Continue with R6/R7. Never guess land patterns or supplier numbers.

### R3: Create a missing library part

Triggers: no symbol, custom footprint, datasheet, QFN/BGA wizard, vendor library, STEP model.
Chain: checkpoint before-part, search/show candidates, compare datasheet, create project-local
libraries, register tables, check/link-check, apply/verify, sync mismatch review, ERC/DRC.

```bash
kce --json lib sym search usb-c receptacle
kce --json lib sym search nfc eeprom --pins 8+ep
kce --json lib fp search DFN 2x2 --pads 9 --type smd
kce --json lib suggest-footprints Device:R --size 0603
kce --json -p P lib sym new P/parts.kicad_sym Switch --pin '1:A:passive:left,2:B:passive:right' --footprint partsfp:Switch --add-to-table parts
kce --json -p P lib fp new P/partsfp.pretty Switch --kind custom --pad '1:-1,0:1,1:smd' --pad '2:1,0:1,1:smd' --add-to-table partsfp
kce --json -p P lib fp wizard qfn P/partsfp.pretty --name DFN --param Pads.nx=0 --param Pads.ny=4 --param Pads.pitch=0.5 --add-to-table partsfp
kce --json -p P lib link-check parts:Switch partsfp:Switch
kce --json -p P lib fp check partsfp:Switch
kce --json -p P lib fp model set partsfp:Switch body.step --copy-into 3d
kce --json -p P session history
```

These are separate geometry examples, not a verified vendor part. Hyphens/underscores/spaces
normalize; EP counts as a pin. --size 0603 means imperial 1608 metric. Show unregistered files
with `lib sym show FILE.kicad_sym:NAME` / `lib fp show FILE.kicad_mod`. Tables use ${KIPRJMOD}
URIs. New/wizard refuse overwrite by default and record history. Inspect pad roles/counts,
model_details.exists, check coverage and unsupported[]. Clearance uses conservative pad boxes;
model existence does not verify STEP geometry. Project-local model copies join undo/transactions;
external libraries are outside checkpoint scope. DRC pin-map failures require a library fix.

### R4: Rehearse and accept a stage

Triggers: try this, rollback on failure, make it brighter, recover, show changes.
Chain: checkpoint, Preview dry-run plan, execute with expect, compare/preview diff, accept or restore.

```bash
kce --json -p P session checkpoint before-change --immutable
kce --json -p P --dry-run run stage.json
kce --json -p P run stage.json
kce --json -p P project compare checkpoint:before-change --geometry
kce --json -p P preview diff --against checkpoint:before-change --recipe board
# On rejection:
kce --json -p P session restore before-change
```

Example stage.json, with acceptance before commit:

```json
{"version":1,"steps":[{"args":["sch","set-value","R1","470"]},
 {"args":["sch","show","R1"],"expect":[{"path":"value","op":"eq","value":"470"}]},
 {"args":["check","erc","--fail-on","error"]}]}
```

Verify intended field/net changes and clean ERC/DRC. Predicates use dotted paths/array indices
and eq/ne/lt/le/gt/ge with value, or empty/nonempty/true/false. Missing paths fail; rejection
is AcceptanceFailed/design/3 before commit. Save an accepted immutable checkpoint.
`session checkpoints` lists generations/hashes. `session undo`, `redo`, `history` span processes.
Checkpoint pending REPL edits only after session save. Keep trial journals outside restore scope.

### R5: Make an ECO without disturbing routing

Triggers: ECO, rev B, second source, replace capacitor, add decoupling, regenerate outputs.
Checkpoint pre-eco after capturing approved nets/footprints/score and ERC/DRC baselines.
Prepare expected-eco.json inside P with only requested changes. Preview `--dry-run run eco.json`,
then run its schematic setters/read-backs/`sch verify --expect ... --strict`/ERC before PCB sync.
Preview `--dry-run pcb update --stable-uuids --lock-existing`, inspect added/removed/replaced,
then apply. Read adopted, replaced[].pads_moved, tracks_ending_on_old_pads and unmatched_pins.
--lock-existing rejects pad displacement; an approved pad-moving swap needs a separate review.

```bash
kce --json -p P pcb place near C10 --to U2 --net VCC --max-dist 6 --rotation auto
kce --json -p P pcb legalize --refs C10 --max-move 5 --strict
kce --json -p P --dry-run pcb autoroute --refs C10 --grid auto
kce --json -p P pcb autoroute --refs C10 --grid auto
kce --json -p P pcb connectivity
kce --json -p P check drc --fail-on error
kce --json -p P project compare checkpoint:pre-eco --geometry --allow C2,C10,net:VCC,net:GND --fail-on-unexpected
```

Verify unchanged positions, UUIDs, pad nets and tracks against the baseline. Compare retains
stdout and exits 4 for unexpected changes; review coverage.excluded and the full allowed-ref
diff too. Scoped near/legalize/autoroute keep others fixed without changing their lock flags.
Replace-symbol uses pin-number mapping by default; inspect connection changes before sync.
Reject unrequested merges/splits. R7 regenerates into fresh outputs; restore pre-eco on regression.

### R6: Place, route and pour

Triggers: connector edge, decoupling near chip, route it, ground pour, DRC errors.
Chain: checkpoint pre-place, move/lock anchors, place near, score, bounded legalize, preview,
set actual rules/netclasses, route, zones/fill, connectivity, DRC, checkpoint accepted.

```bash
kce --json -p P pcb score
kce --json -p P --dry-run pcb legalize --strict --max-move 5
kce --json -p P pcb legalize --strict --max-move 5
kce --json -p P pcb autoroute --grid auto --skip-net GND
kce --json -p P pcb zone add --net GND --layer F.Cu --layer B.Cu --pad-connection auto
kce --json -p P pcb zone fill
kce --json -p P pcb connectivity
kce --json -p P check drc --fail-on error
```

Verify outline.closed, courtyard_overlaps.count=0, outside_outline.count=0; inspect geometry
methods/fallback refs. HPWL/crossings/congestion rank placements, not electrical correctness.
No closed outline makes score fail with design/3. Place near exhaustion is PlacementBlocked;
inspect locked_blockers/reason_code. Never unlock anchors to hide failure. Non-strict legalize
can succeed with unmoved[], so gate it empty. Route uses effective netclass width by default.
Grid auto refines 0.25/0.15/0.1/0.05 mm; read failed[]/grid_tried. Zone auto is solid SMD,
thermal THT. Connector edge placement is manual pcb move/lock; no place edge command exists.
Dense boards may need Freerouting (Java/FREEROUTING_JAR). `check diagnose` supplies fix hints.

### R7: Build a fabrication hand-off

Triggers: order PCB, send to fab, JLCPCB/PCBWay/OSH Park, assembly, BOM+CPL, Gerber.
Chain: verify intent, gate ERC/DRC, inspect manufacturer limits, parts/BOM check, fresh package,
review files/manifest/CPL orientation and hand off. Never pay or place an order automatically.

```bash
kce --json -p P fab parts check --offline
kce --json -p P fab bom-check --require MPN,LCSC,Footprint
kce --json -p P fab package --preset jlcpcb --fail-on-drc --require-lcsc --manifest -o RELEASE
kce --json -p P pcb export drill --output P/outputs/drill/
kce --json -p P pcb export step --output P/outputs/board.step
```

Verify complete:true, failed_steps empty and nonempty files[]/zip hashes. Export failure returns
complete:false/design/3 and preserves the accepted package. Existing-directory exchange is
atomic on Linux; use fresh directories elsewhere. DRC rules_preset:null is not manufacturer
qualification. Review bom.missing_lcsc and cpl.review, pin 1, polarity and rotation in the fab
viewer. --rotation-fix changes CPL only. Native jobset fab-template archives Gerber/drill;
use fab package for assembly. Passthrough exports use outputs[], fab uses files[].

### R8: Gate CI and tagged releases

Triggers: GitHub Actions, ERC/DRC on PRs, BOM/cost gate, build Gerbers on tag, jobset CI.
Chain: doctor, explicit check thresholds/baseline policy, compare base, preview diff, BOM gate,
revision gate, fresh fab package --fail-on-drc, clean-tree assertion, artifact review.
Use README workflow templates with fetch-depth: 0 and reviewed pins. Preserve stdout/stderr.
For costs use `fab bom-check --prices prices.csv --price-key LCSC --against git:HEAD
--max-delta-pct 10`; CSV needs LCSC/unit_price_usd. Unpriced is not free. Release can require
`fab package --require-rev v1.3.0 --manifest --name-suffix '{rev}-{preset}'`.
Verify a seeded bad design goes red as well as a clean one green. jobset execute defaults to
design/3 on failed jobs; inspect failed_jobs/failed[]. Templates are not Windows execution evidence.

### R9: Search thousands of placement trials

Triggers: optimize placement, hypcb, warm worker, 5000 trials, keep/revert, deterministic replay.
Chain: strict intent and ERC/DRC baseline, immutable baseline/best-000000 checkpoint then best,
foreground serve, move/verify/score plan with expect, DRC for keeper, immutable generation then
best; restore best for any rejection. Keep journal/approved oracle outside P, step inputs inside.
Use --session-dir or KCE_SESSION_DIR outside P consistently; checkpoint contents exclude state.

```bash
kce --json -p P serve --request-timeout-ms 30000
```

Send one JSON-RPC object per line; wait for each mutation's matching id:

```json
{"jsonrpc":"2.0","id":1,"method":"run","params":{"args":["pcb","info"],"timeout_ms":2000}}
{"jsonrpc":"2.0","id":2,"method":"run","params":{"args":["run","trial.json"]}}
{"jsonrpc":"2.0","id":3,"method":"shutdown"}
```

Verify meta.worker_reused on a second board request, objective improvement and hard gates.
A reply's result is the CLI value, including bare arrays. raw pcbnew adds its own result field.
Cancel with method cancel, params {request_id:2}; acknowledgement follows rollback/reaping.
Timeout kills/reaps the native worker; the next request starts another. Read commit_state and
retryable; on lost transport or unknown state restore the accepted generation and verify bytes,
nets and score before restarting. Journal design rejection separately from crash/timeout/input.
Progress is JSON lines on stderr with --progress; unmeasured phase timings are null. EOF/shutdown
drains accepted requests; close pipes and wait. Never score infrastructure failure as a bad trial.

### R10: Intake a foreign board

Triggers: Altium/Eagle import, .PcbDoc/.brd, contractor design, migrate into KiCad.
Chain: preserve source and independent source inventory, project import SOURCE --dir NEW,
inspect read_back/source_unchanged/losses, checkpoint imported, carry rules explicitly, DRC.
A board import is not an editable schematic migration. Companion ace (altium-cli-extended,
GPL-3.0-or-later) can inspect native Altium inputs; inspect its help before using it.
Verify source hash/membership and compare independent counts, nets, layers and lengths.
If reconstructing a schematic, verify source-derived intent, then preview
`--dry-run pcb update --keep-extra` and review every removal/adoption before sync.
Self-consistency does not establish source equivalence or translate missing rules.

### R11: Simulate before layout

Triggers: SPICE, ngspice, RC filter, corner frequency, transfer function.
Chain: scratch project, sim model, sim directive, sim netlist, sim run, inspect probes/CSV,
compare analytical result, then apply checked intent to the real project and follow R6.

```bash
kce --json -p P sch label-pin C1:2 0 --kind global
kce --json -p P sim model V1 --device V --type SIN --params 'dc=0 ampl=1 f=1k ac=1'
kce --json -p P sim directive add '.ac dec 20 10 1meg'
kce --json -p P sim netlist
kce --json -p P sim run --probe 'v(/out)' --points 3
```

Verify node 0, source ac=1, exact probe names (including /), warnings/errors and nonzero samples.
Compare RC corner with 1/(2*pi*R*C). power:GND alone is not SPICE ground; empty errors is
insufficient. Simulation does not replace ERC/DRC or prove physical high-speed behaviour.

### R12: Route a high-speed block and panelize

Triggers: differential pair, USB/LVDS/MIPI/DDR, skew, length match, via fence, teardrop, panel.
Chain: checkpoint pre-hs, inspect stackup/netclasses/pairs, rules, route, shorter-leg tune,
fence, stitch, scoped teardrops, fill, DRC, length report, separate panel and panel DRC/package.

```bash
kce --json -p P pcb diffpair list
kce --json -p P pcb diffpair rule /USB --gap 0.2 --gap-max 0.25 --max-skew 0.5
kce --json -p P pcb diffpair route /USB --from U1 --to J1 --layer F.Cu
kce --json -p P pcb length tune /USB_D- --match /USB_D+ --amplitude 1 --tolerance 0.02
kce --json -p P pcb fence --net /CLK --fence-net GND
kce --json -p P pcb stitch --net GND --pitch 2.5
kce --json -p P pcb teardrops add --target vias --net /CLK
kce --json -p P pcb panelize --rows 2 --cols 3 --mode vscore --spacing 0 --strict --drc -o panel/board-panel.kicad_pcb
```

Use actual pair/net names. Verify routed pairs, skew budget, no diff_pair/length violations,
added/rejected via counts and source hash unchanged. V-score forces spacing zero (warning;
--strict rejects conflicting spacing). Fab panel capability/impedance certification is not
implemented; obtain independent review. A panel export alone is not a manufacturing package.

## Advanced

Use `preview capture`/`preview diff` for visual review and `preview live` while iterating:

```bash
kce --json -p P preview live start --recipe board
kce --json -p P preview live push
kce --json -p P preview live status
kce --json -p P preview live stop
```

Other tools: pcb zone rule-area, pcb model(s), pcb update-footprints, pcb dimension/barcode/hole,
project variants and sch export bom --variant. Read COMMANDS.md for exact paths/options.
Live gui commands require a running PCB editor with IPC enabled and optional kicad-python.
Save GUI edits before disk edits, then reload. sch move does not drag wires; prefer label-pin.
Piped `kce --json repl` returns one JSON line per command; call session save before EOF or edits
are discarded. Checkpoints exclude outputs; archive with --include-outputs to preserve releases.

PowerShell uses arrays and its call operator:

```powershell
$KCE = '--json','-p','P'
& kce @KCE info doctor
```

Pass negative lists as --rotate=-45,0,45. Prefer plans to shell loops.
