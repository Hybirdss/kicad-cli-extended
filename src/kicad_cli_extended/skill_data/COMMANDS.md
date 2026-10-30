# kce command reference

Generated from the CLI (283 commands). Global options: `--json`, `--project PATH`, `--dry-run`, `--read-only`, `--progress`, `--session-dir DIR`, `--no-validate`, `--version`. kicad-cli passthrough commands also accept `--show-command` and `--allow-fail`. Details: `kce <group> <command> --help`.

Public result schemas: [run-result](../../schemas/run-result.schema.json), [sch-apply](../../schemas/sch-apply.schema.json), [sch-verify](../../schemas/sch-verify.schema.json), [pcb-score](../../schemas/pcb-score.schema.json), [sch-nets](../../schemas/sch-nets.schema.json), [rpc](../../schemas/rpc.schema.json), [error](../../schemas/error.schema.json).

Use `kce --json info commands --full` or `kce info describe COMMAND` for effects and examples. Installed schemas: `kce info schema NAME`.

## Choose commands by intent

Start with the triggered recipes in [SKILL.md](SKILL.md). Use this map before composing a chain.

| Intent | Commands to reach for | Acceptance |
|---|---|---|
| Nets as data | `sch apply`, `sch verify --expect FILE --strict` | match:true; ERC |
| Atomic stage and rollback | `run PLAN`, `session checkpoint`, `session restore` | plan expect predicates; compare checkpoint:NAME |
| Placement | `pcb score`, `pcb place near`, `pcb legalize --strict` | closed outline; zero overlaps/outside |
| Preserve an ECO | dry-run `pcb update --stable-uuids --lock-existing`, scoped autoroute, `project compare` | pin nets, UUIDs, pad positions; DRC |
| Explain failed checks | `check diagnose`, `errors` | explicit --fail-on policy |
| Many PCB calls | `serve` | worker_reused; matching request ids; restore on lost outcome |
| Missing part | `lib sym new`, `lib fp new`, `lib fp wizard`, `lib link-check`, `lib fp check` | datasheet pins/pads; coverage; ERC/DRC |
| High-speed signals | `pcb diffpair`, `pcb length`, `pcb fence`, `pcb stitch`, `pcb teardrops` | skew/length budget; fill; DRC |
| Breakaway panels | `pcb panelize` | source hash; effective mode/spacing; panel DRC; fab review |
| Filters | `sim model`, `sim directive add`, `sim netlist`, `sim run` | node 0; AC stimulus; analytical/probe check |
| Foreign design | `project import` into a new directory | source preservation and independent loss/equivalence review |
| Release | `fab bom-check`, `fab package --fail-on-drc --manifest` | complete:true; files/hashes; CPL review |

`serve` reuses pcbnew; separate kicad-cli invocations such as ERC do not share that worker. Connector edge placement uses `pcb move` and `pcb lock`.

## Complete command reference

| Command | Args | Description | Options |
|---|---|---|---|
| `kce check diagnose` |  | Run ERC+DRC and return a prioritised fix plan; --fail-on warning is a CI gate. | `--only` `--fail-on-error` `--max-violations` `--summary` `--baseline` `--fail-on` |
| `kce check drc` |  | Check board rules; truncation never weakens --fail-on or --baseline gates. | `--output` `--severity` `--no-parity` `--no-refill` `--save-board` `--fail-on-error` `--max-violations` `--summary` `--baseline` `--fail-on` |
| `kce check erc` |  | Check schematic electrical rules; use --fail-on error for CI and plan acceptance. | `--output` `--severity` `--fail-on-error` `--max-violations` `--summary` `--baseline` `--fail-on` |
| `kce errors` |  | List stable error types, categories, exit codes and recovery commands. |  |
| `kce fab bom-check` |  | Gate assembly fields and file-based costs. Emits the full report before design exit 3. | `--require` `--variant` `--prices` `--price-key` `--against` `--max-delta-pct` |
| `kce fab package` |  | Stage and verify every export before publishing. Failures preserve the accepted directory. | `--preset` `--output-dir` `--no-drc` `--fail-on-drc` `--step` `--atomic-output` `--fail-on-any-export` `--require-lcsc` `--bom-fields` `--rotation-fix` `--manifest` `--name-suffix` `--require-rev` |
| `kce fab parts check` |  | List refs, LCSC IDs, footprints and lookup links; verify stock and package at the supplier. | `--offline` |
| `kce fab presets` |  |  |  |
| `kce gui action` | NAME | Run a KiCad tool action, e.g. common.Control.zoomFitScreen, pcbnew.InteractiveSelection.ClearSelection. |  |
| `kce gui refill` |  | Refill all zones in the open editor. |  |
| `kce gui reload` |  | Re-read the board from disk in the open editor (run after kce changed the file). |  |
| `kce gui save` |  | Save the board open in the editor (before editing it with kce). |  |
| `kce gui select` | REFS | Select footprints by reference in the PCB editor (show the user what you mean). | `--clear` |
| `kce gui selection` |  | Items selected in the PCB editor (type, uuid, reference, net, position). |  |
| `kce gui status` |  | Is a KiCad GUI reachable? Version and open documents (never fails: see 'connected'). |  |
| `kce info commands` |  | Discover every command. Use info describe COMMAND for its complete contract. | `--full` |
| `kce info describe` | COMMAND | Describe COMMAND: effect, arguments, public result schema, example and exit codes. |  |
| `kce info doctor` |  | Report kicad-cli, pcbnew interpreter, library dirs, lib tables (works on any OS). | `--require` |
| `kce info kicad-version` |  | Reports the version info in various formats | all `kicad-cli version` options |
| `kce info paths` |  | KiCad path variables (KICAD10_*_DIR, KIPRJMOD) as resolved for the project. |  |
| `kce info schema` | NAME | List public schemas, or print NAME (e.g. plan, run-result, sch-nets, error). Works offline. |  |
| `kce info version` |  | Versions of this tool, KiCad and pcbnew. |  |
| `kce jobset add-job` | JOB_TYPE |  | `--jobset` `--set` `--description` `--id` |
| `kce jobset add-output` | KIND OUTPUT_PATH |  | `--jobset` `--only` `--description` |
| `kce jobset execute` | NAME | Run a jobset with result listing (wraps `kicad-cli jobset run`; see also `kce jobset run`). | `--output` `--stop-on-error` `--fail-on-job-error` |
| `kce jobset list` |  |  |  |
| `kce jobset new` | NAME |  | `--overwrite` `--fab-template` |
| `kce jobset remove` | ITEM_ID |  | `--jobset` |
| `kce jobset run` | INPUT_FILE | Runs a jobset file | all `kicad-cli jobset run` options |
| `kce jobset show` | NAME |  |  |
| `kce jobset type-info` | JOB_TYPE |  |  |
| `kce jobset types` |  | Job types KiCad 10 supports and their setting keys. |  |
| `kce lib fp check` | FPID | Check a library footprint on its own, without a board or DRC run. | `--rules` `--ipc-class` |
| `kce lib fp export svg` | INPUT_FILE | Exports the footprint or entire footprint library to SVG | all `kicad-cli fp export svg` options |
| `kce lib fp libs` |  |  |  |
| `kce lib fp list` | LIBRARY | List footprints in a library nickname or an unregistered .pretty directory. |  |
| `kce lib fp model list` | FPID | List the model(s) of FPID with resolved paths and existence. |  |
| `kce lib fp model remove` | FPID | Remove a 3D model reference from a library footprint. | `--index` |
| `kce lib fp model set` | FPID FILE | Set the 3D model inside a library footprint, so every placement gets a body. | `--offset` `--rotation` `--scale` `--add` `--index` `--copy-into` `--no-check` |
| `kce lib fp new` | PRETTY_DIR NAME | Create a footprint (header/DIP/SOIC/chip generators or explicit pads). Validated by kicad-cli. | `--kind` `--pads` `--pitch` `--rows` `--row-spacing` `--pad-size` `--drill` `--pad` `--description` `--tags` `--overwrite` `--add-to-table` `--model` |
| `kce lib fp search` | QUERY | Find footprints by all query words, pad count and SMD/THT. | `--lib` `--pads` `--type` `--limit` `--explain` |
| `kce lib fp show` | FPID | Footprint detail: pads, courtyard, models. Accepts LIB:NAME or FILE.kicad_mod. |  |
| `kce lib fp upgrade` | INPUT_FILE | Upgrades the footprint library to the current kicad version format | all `kicad-cli fp upgrade` options |
| `kce lib fp wizard` | WIZARD PRETTY_DIR | Generate a footprint with a KiCad wizard (QFN/QFP/BGA/...) into a .pretty library. | `--param` `--name` `--description` `--tags` `--datasheet` `--model` `--overwrite` `--add-to-table` |
| `kce lib fp wizard-params` | WIZARD |  |  |
| `kce lib fp wizards` |  | KiCad's bundled footprint wizards (scripting/plugins). |  |
| `kce lib index` |  | Build (cached) search indexes of all symbol and footprint libraries. | `--rebuild` |
| `kce lib init-global-tables` |  | Install KiCad's default global sym/fp lib tables (first-run setup KiCad's GUI normally does). | `--force` |
| `kce lib link-check` | LIB_ID FPID | Compare a symbol's pins with its footprint's pads (catches a missing pad before DRC). |  |
| `kce lib suggest-footprints` | LIB_ID | Footprints matching a symbol's ki_fp_filters and pin count. | `--query` `--limit` `--size` |
| `kce lib sym delete` | LIBRARY_FILE NAME |  |  |
| `kce lib sym export svg` | INPUT_FILE | Exports the symbol or entire symbol library to SVG | all `kicad-cli sym export svg` options |
| `kce lib sym libs` |  |  |  |
| `kce lib sym list` | LIBRARY | List symbols in a library nickname or an unregistered .kicad_sym file. |  |
| `kce lib sym new` | LIBRARY_FILE NAME | Create a box symbol with pins in a .kicad_sym (created if missing). Validated by kicad-cli. | `--pin` `--reference` `--value` `--footprint` `--datasheet` `--description` `--keywords` `--fp-filters` `--overwrite` `--add-to-table` |
| `kce lib sym search` | QUERY | Find symbols by all query words; hyphens, underscores and spaces are equivalent. | `--lib` `--pins` `--power` `--footprint` `--limit` `--explain` |
| `kce lib sym show` | LIB_ID | Symbol detail: properties, units, pins. Accepts LIB:NAME or FILE.kicad_sym[:NAME]. |  |
| `kce lib sym upgrade` | INPUT_FILE | Upgrades the symbol library to the current kicad version format | all `kicad-cli sym upgrade` options |
| `kce pcb add-footprint` | FPID | Place a library footprint directly on the board (board-only parts: holes, logos, test points). | `--ref` `--at` `--rotation` `--side` `--value` |
| `kce pcb autoroute` |  | Autoroute unrouted connections (builtin grid router, or Freerouting via Specctra DSN/SES). | `--engine` `--grid` `--width` `--clearance` `--via-diameter` `--via-drill` `--layers` `--net` `--skip-net` `--passes` `--refs` `--area` |
| `kce pcb barcode` | TEXT | Add a KiCad 10 barcode (QR, Micro QR, Data Matrix, Code 39/128) on any layer. | `--at` `--kind` `--size` `--height` `--layer` `--ecc` `--show-text` `--knockout` `--angle` |
| `kce pcb batch` | ACTIONS_FILE | Apply a JSON list of worker actions in one pcbnew session (see SKILL.md 'pcb batch'). |  |
| `kce pcb component-classes` |  | KiCad 10 component classes per footprint (rules: A.hasComponentClass('NAME')). |  |
| `kce pcb connectivity` |  | Unrouted connection count and incomplete nets (pcbnew connectivity). |  |
| `kce pcb delete-footprint` | REF |  |  |
| `kce pcb delete-item` | UUID |  |  |
| `kce pcb delete-tracks` |  | Delete tracks/vias by net, layer or uuid (rip-up). | `--net` `--layer` `--uuid` `--vias-only` `--all` `--include-locked` |
| `kce pcb diffpair list` |  | Pairs KiCad recognises, with net class width/gap, lengths and skew. |  |
| `kce pcb diffpair route` | PAIR | Route a pair as two coupled parallel tracks (45-degree path between the two footprints). | `--from` `--to` `--layer` `--width` `--gap` `--via-points` |
| `kce pcb diffpair rule` | PAIR | Custom DRC rule for pairs matching PAIR (base name, wildcards ok: '/USB_D', '/LVDS*'). | `--name` `--gap-min` `--gap` `--gap-max` `--width` `--max-uncoupled` `--max-skew` `--min` `--max` |
| `kce pcb dimension` | KIND | Add a dimension (aligned, orthogonal, leader, center mark, radial) on any layer. | `--start` `--end` `--height` `--vertical` `--layer` `--units` `--precision` `--text` `--prefix` `--suffix` `--text-size` `--line-width` `--leader-length` |
| `kce pcb drawings` |  |  | `--layer` |
| `kce pcb drc` | INPUT_FILE | Runs the Design Rules Check (DRC) on the PCB and creates a report | all `kicad-cli pcb drc` options |
| `kce pcb dsn` |  | Export Specctra DSN (for external autorouters like Freerouting). | `--output` |
| `kce pcb export 3dpdf` | INPUT_FILE | Export PDF | all `kicad-cli pcb export 3dpdf` options |
| `kce pcb export brep` | INPUT_FILE | Export BREP | all `kicad-cli pcb export brep` options |
| `kce pcb export drill` | INPUT_FILE | Generate Drill Files | all `kicad-cli pcb export drill` options |
| `kce pcb export dxf` | INPUT_FILE | Generate a DXF from a list of layers | all `kicad-cli pcb export dxf` options |
| `kce pcb export gencad` | INPUT_FILE | Export the PCB in Gencad format | all `kicad-cli pcb export gencad` options |
| `kce pcb export gerbers` | INPUT_FILE | Plot multiple Gerbers for a PCB, including the ability to use stored board plot | all `kicad-cli pcb export gerbers` options |
| `kce pcb export glb` | INPUT_FILE | Export GLB (binary GLTF) | all `kicad-cli pcb export glb` options |
| `kce pcb export hpgl` | INPUT_FILE | No longer supported as of KiCad 10.0. | all `kicad-cli pcb export hpgl` options |
| `kce pcb export ipc2581` | INPUT_FILE | Export the PCB in IPC-2581 format | all `kicad-cli pcb export ipc2581` options |
| `kce pcb export ipcd356` | INPUT_FILE | Generate IPC-D-356 netlist file | all `kicad-cli pcb export ipcd356` options |
| `kce pcb export odb` | INPUT_FILE | Export the PCB in ODB++ format | all `kicad-cli pcb export odb` options |
| `kce pcb export pdf` | INPUT_FILE | Generate PDF from a list of layers | all `kicad-cli pcb export pdf` options |
| `kce pcb export ply` | INPUT_FILE | Export PLY | all `kicad-cli pcb export ply` options |
| `kce pcb export pos` | INPUT_FILE | Generate Position File | all `kicad-cli pcb export pos` options |
| `kce pcb export ps` | INPUT_FILE | Generate Postscript from a list of layers | all `kicad-cli pcb export ps` options |
| `kce pcb export stats` | INPUT_FILE | Generate a board statistics report | all `kicad-cli pcb export stats` options |
| `kce pcb export step` | INPUT_FILE | Export STEP | all `kicad-cli pcb export step` options |
| `kce pcb export stl` | INPUT_FILE | Export STL | all `kicad-cli pcb export stl` options |
| `kce pcb export stpz` | INPUT_FILE | Export STEPZ | all `kicad-cli pcb export stpz` options |
| `kce pcb export svg` | INPUT_FILE | Generate SVG outputs of a given layer list | all `kicad-cli pcb export svg` options |
| `kce pcb export u3d` | INPUT_FILE | Export U3D | all `kicad-cli pcb export u3d` options |
| `kce pcb export vrml` | INPUT_FILE | Export VRML | all `kicad-cli pcb export vrml` options |
| `kce pcb export xao` | INPUT_FILE | Export XAO | all `kicad-cli pcb export xao` options |
| `kce pcb fence` |  | Via fence: rows of vias both sides of a net's tracks (RF / high-speed shielding). | `--net` `--fence-net` `--pitch` `--offset` `--diameter` `--drill` `--clearance` `--layer` |
| `kce pcb fiducial` |  | Add a fiducial (kept in position files, excluded from the BOM). | `--at` `--copper` `--mask` `--side` `--ref` |
| `kce pcb flip` | REF |  | `--side` |
| `kce pcb footprint` | REF | One footprint with pads (absolute positions and nets) and fields. |  |
| `kce pcb footprints` |  |  | `--pads` `--match` |
| `kce pcb graphic` | SHAPE | Add a graphic shape on any layer (silk, fab, user, Edge.Cuts...). | `--start` `--end` `--center` `--radius` `--points` `--layer` `--width` `--filled` |
| `kce pcb hole` |  | Add a mounting hole (board-only footprint; NPTH, or plated with --pad). | `--at` `--drill` `--pad` `--net` `--ref` |
| `kce pcb import` | INPUT_FILE | Import a non-KiCad PCB file to KiCad format. Output defaults to the selected pro | all `kicad-cli pcb import` options |
| `kce pcb info` |  | Board summary from pcbnew: layers, counts, outline, unrouted connections. |  |
| `kce pcb layers` |  |  |  |
| `kce pcb legalize` |  | Move overlapping and out-of-outline footprints to the nearest legal spot (locked ones stay). | `--clearance` `--grid` `--max-dist` `--strict` `--refs` `--max-move` |
| `kce pcb length report` |  | Routed length per net (tracks + pad-to-die), spread per net class, skew per pair. | `--net` `--netclass` |
| `kce pcb length rule` | NAME | Length-matching rule (custom DRC rule; `kce check drc` reports length_out_of_range / skew). | `--nets` `--netclass` `--min` `--opt` `--max` `--max-skew` |
| `kce pcb length tune` | NET | Add accordion meanders to a net's straight segments until it reaches --target / --match length. | `--target` `--match` `--amplitude` `--spacing` `--clearance` `--layer` `--tolerance` |
| `kce pcb lock` | REFS |  | `--unlock` `--all` `--except` |
| `kce pcb model adjust` | REF | Change offset / rotation / scale / opacity / visibility of existing models. | `--index` `--offset` `--rotation` `--scale` `--opacity` `--show` |
| `kce pcb model remove` | REF |  | `--index` |
| `kce pcb model set` | REF FILE | Assign a 3D model file (stored as ${KIPRJMOD}/... or ${KICAD10_3DMODEL_DIR}/... when possible). | `--add` `--index` `--offset` `--rotation` `--scale` `--opacity` `--hide` `--no-check` |
| `kce pcb models` |  | 3D models per footprint with resolved paths; reports files that do not exist. | `--missing` `--ref` |
| `kce pcb move` | REF | Move a footprint (absolute --at x,y or relative --by dx,dy), optionally set rotation. | `--at` `--by` `--rotation` `--force` |
| `kce pcb net-ties` |  | Net-tie footprints with their pad groups and the nets they join. |  |
| `kce pcb nets` |  |  |  |
| `kce pcb new` |  | Create an empty board for the project (via pcbnew). | `--layers` `--overwrite` |
| `kce pcb origin` | KIND | Set the drill/place (aux) or grid origin. | `--at` |
| `kce pcb outline circle` |  |  | `--center` `--radius` `--keep` |
| `kce pcb outline clear` |  |  |  |
| `kce pcb outline polygon` | POINTS | Closed polygon 'x1,y1;x2,y2;...'. | `--keep` |
| `kce pcb outline rect` |  |  | `--at` `--size` `--radius` `--width` `--keep` |
| `kce pcb panelize` |  | Panelize the board: N x M copies with tabs + mouse bites or V-scores, rails, fiducials, tooling holes. | `--rows` `--cols` `--spacing` `--mode` `--rails` `--rail-width` `--tab-width` `--tabs` `--bite-drill` `--bite-pitch` `--no-fiducials` `--no-tooling-holes` `--vscore-layer` `--output` `--engine` `--drc` `--strict` |
| `kce pcb place grid` |  | Pack footprints inside the outline without overlapping standing ones (initial placement). | `--refs` `--area` `--spacing` `--margin` `--order` `--only-unplaced` |
| `kce pcb place near` | REF | Put REF at the nearest legal spot to a footprint or pad (same rules as `pcb legalize`). | `--to` `--max-dist` `--side` `--clearance` `--grid` `--net` `--pad` `--rotation` |
| `kce pcb ratsnest` |  | Pad pairs still to route (minimum spanning connections). | `--net` |
| `kce pcb rename-layer` | LAYER NAME |  |  |
| `kce pcb render` | INPUT_FILE | Renders the PCB in 3D view to PNG or JPEG image | all `kicad-cli pcb render` options |
| `kce pcb rotate` | REF |  | `--angle` `--by` |
| `kce pcb route` | FROM_PAD TO_PAD | Route REF:PAD to REF:PAD with a 45°/orthogonal track (no obstacle avoidance; check DRC). | `--layer` `--width` `--mode` `--via-points` |
| `kce pcb score` |  | Measure placement: per-net HPWL, courtyard overlaps, out-of-outline parts, ratsnest crossings, | `--net` `--bin-size` |
| `kce pcb ses` | SES_FILE | Import a routed Specctra session (.ses) back into the board. |  |
| `kce pcb set-attr` | REF ATTR VALUE |  |  |
| `kce pcb set-field` | REF NAME VALUE | Set a footprint field (Reference/Value/custom). Prefer editing the schematic + `kce pcb update`. | `--visible` |
| `kce pcb set-layers` | COUNT | Set the copper layer count (2, 4, 6, ...). |  |
| `kce pcb set-pad-net` | PAD NET | Assign a net to REF:PAD (board-only; schematic + `kce pcb update` is preferred). |  |
| `kce pcb stackup` |  | Physical stackup (from the board file) and layer table. |  |
| `kce pcb stitch` |  | Via stitching: a grid of vias inside a net's pours, clear of other nets, holes and keepouts. | `--net` `--pitch` `--diameter` `--drill` `--clearance` `--margin` `--pattern` `--layer` `--zone` `--rect` |
| `kce pcb teardrops add` |  | Create (or regenerate) teardrops; zones are refilled and DRC sees them like KiCad's own. | `--target` `--net` `--length-ratio` `--max-length` `--width-ratio` `--max-width` `--filter-ratio` `--curved` `--pads-in-zones` `--round-only` |
| `kce pcb teardrops remove` |  | Delete teardrop zones and disable teardrops on the pads/vias. | `--net` `--keep-settings` |
| `kce pcb text` | TEXT |  | `--at` `--layer` `--size` `--thickness` `--angle` |
| `kce pcb thickness` | MM |  |  |
| `kce pcb track` | POINTS | Add a track polyline 'x1,y1;x2,y2;...'. | `--layer` `--width` `--net` |
| `kce pcb tracks` |  |  | `--net` |
| `kce pcb update` |  | Sync footprints and nets, reporting adoption, displaced pads and missing pin/pad matches. | `--keep-extra` `--no-replace` `--no-place` `--stable-uuids` `--lock-existing` |
| `kce pcb update-footprints` | REFS | Update footprints from their libraries (keeps placement, nets, fields; reports changes). | `--match` `--reset-text` `--reset-models` `--force` |
| `kce pcb upgrade` | INPUT_FILE | Upgrade the board file's format to the latest one | all `kicad-cli pcb upgrade` options |
| `kce pcb via` |  |  | `--at` `--net` `--diameter` `--drill` `--type` `--top` `--bottom` |
| `kce pcb zone add` |  | Add a copper pour (filled by KiCad's ZONE_FILLER unless --no-fill). | `--net` `--layer` `--rect` `--points` `--margin` `--clearance` `--min-width` `--priority` `--pad-connection` `--thermal-gap` `--thermal-width` `--name` `--no-fill` |
| `kce pcb zone delete` |  |  | `--net` `--uuid` |
| `kce pcb zone fill` |  | Fill all zones (KiCad ZONE_FILLER). |  |
| `kce pcb zone keepout` |  | Add a rule area / keepout. | `--layer` `--rect` `--points` `--allow-tracks` `--allow-vias` `--no-pads` `--no-footprints` `--allow-fills` `--name` |
| `kce pcb zone rule-area` |  | Rule area: keepouts, KiCad 10 placement areas and area-scoped custom DRC rules. | `--name` `--layer` `--rect` `--points` `--no` `--placement` `--rule` |
| `kce pcb zone unfill` |  |  |  |
| `kce pcb zones` |  |  |  |
| `kce preview capture` |  |  | `--recipe` `--force` `--root-dir` `--width` `--height` `--label` |
| `kce preview diff` |  | Publish a diff bundle (before/after renders + structural diff.json). | `--against` `--recipe` `--root-dir` |
| `kce preview latest` |  | Newest existing bundle (read-only, never renders). | `--recipe` `--root-dir` |
| `kce preview live push` |  |  | `--recipe` `--root-dir` `--stage` `--note` |
| `kce preview live start` |  |  | `--recipe` `--root-dir` |
| `kce preview live status` |  | Cheap read-only probe of the live session (current bundle + history summary). | `--recipe` `--root-dir` |
| `kce preview live stop` |  |  | `--recipe` `--root-dir` |
| `kce preview recipes` |  |  |  |
| `kce project archive` |  | Zip the project (like File > Archive Project). | `--output` `--include-outputs` |
| `kce project compare` | BEFORE AFTER | Compare revisions: BEFORE/AFTER are project paths, .zip archives or git:REV (AFTER default: current). | `--geometry` `--allow` `--fail-on-unexpected` |
| `kce project files` |  | KiCad files in the project directory. |  |
| `kce project get` | KEY | Read a .kicad_pro JSON value by dotted key (e.g. board.design_settings.rules). |  |
| `kce project gitignore` |  | Print recommended Git ignore rules, or append missing rules with --append. | `--append` |
| `kce project import` | SOURCE | Import a foreign board (Altium .PcbDoc, Eagle .brd, CADSTAR, PADS, ...) into a new kce project. | `--dir` `--name` `--format` `--overwrite` |
| `kce project info` |  | Project summary: files, schematic/board counts, variables, net classes. |  |
| `kce project libtable add` | KIND NAME URI | Add a library (URI may use ${KIPRJMOD}, ${KICAD10_SYMBOL_DIR}, ...). | `--scope` `--descr` `--type` |
| `kce project libtable list` | KIND |  |  |
| `kce project libtable remove` | KIND NAME |  | `--scope` |
| `kce project netclass assign` | PATTERN NETCLASS | Assign nets matching PATTERN (wildcards, e.g. '/VCC*' or 'GND') to NETCLASS. |  |
| `kce project netclass delete` | NAME |  |  |
| `kce project netclass list` |  |  |  |
| `kce project netclass set` | NAME | Create or update a net class. | `--set` `--track-width` `--clearance` `--via-diameter` `--via-drill` `--diff-pair-width` `--diff-pair-gap` |
| `kce project new` | DIRECTORY | Create a project (KiCad itself normalises the new schematic and board). | `--name` `--template` `--title` `--overwrite` |
| `kce project open` | PATH | Open a project in this session (REPL). |  |
| `kce project rules custom-add` | NAME | Append a custom DRC rule to <project>.kicad_dru. | `--constraint` `--condition` `--layer` |
| `kce project rules custom-remove` | NAME |  |  |
| `kce project rules custom-show` |  | Show custom rules from <project>.kicad_dru. |  |
| `kce project rules preset-add` | KIND VALUES | Add a predefined size: track W \| via DIAMETER DRILL \| diff_pair W GAP [VIA_GAP]. |  |
| `kce project rules set` | VALUES | Set constraints: min_clearance=0.15 min_track_width=0.15 ... (mm). |  |
| `kce project rules severity` | CHECK SEVERITY | Set the severity of a DRC (or --erc ERC) check, e.g. `silk_overlap ignore`. | `--erc` |
| `kce project rules show` |  |  |  |
| `kce project save` |  | Write unsaved changes (REPL) — validated by kicad-cli. |  |
| `kce project set` | KEY VALUE | Set a .kicad_pro JSON value (VALUE parsed as JSON when possible). |  |
| `kce project templates` |  | List KiCad project templates. |  |
| `kce project textvar delete` | NAME |  |  |
| `kce project textvar list` |  |  |  |
| `kce project textvar set` | NAME VALUE |  |  |
| `kce project unarchive` | ARCHIVE DIRECTORY |  |  |
| `kce project variant add` | NAME | Create a variant; export it with e.g. `kce sch export bom --variant NAME`. | `--description` |
| `kce project variant list` |  | Variants of the project and each symbol's overrides (DNP, BOM, board, fields). |  |
| `kce project variant remove` | NAME | Delete a variant and every symbol override that belongs to it. |  |
| `kce raw append` | FILE QUERY SEXPR_TEXT | Append a child S-expression to the matched node ('' or '/' = root). |  |
| `kce raw check` | FILE | Parse a KiCad file and report whether our writer round-trips it byte-for-byte. |  |
| `kce raw delete` | FILE QUERY |  | `--all` |
| `kce raw pcbnew` |  | Run arbitrary pcbnew Python against the board (in the pcbnew interpreter). | `--code` `--file` `--readonly` `--param` |
| `kce raw query` | FILE QUERY | Query nodes: 'symbol[2]/property[Reference]', '//footprint/at', 'lib_symbols/symbol'. | `--limit` `--sexpr` |
| `kce raw set` | FILE QUERY VALUES | Replace the atoms of matched node(s): `kce raw set board.kicad_pcb 'general/thickness' 1.2`. | `--all` `--quoted` |
| `kce repl` | PROJECT_PATH | Interactive shell, or commands from piped stdin. |  |
| `kce run` | PLAN | Execute JSON plan steps in one session, then validate and save once. |  |
| `kce sch add` | LIB_ID | Place a symbol from a library (e.g. Device:R, power:GND, Timer:NE555P). | `--ref` `--at` `--angle` `--mirror` `--unit` `--value` `--footprint` `--field` `--dnp` `--no-snap` `--uuid` `--allow-merge` `--sheet` |
| `kce sch add-sheet` | NAME | Add a hierarchical sheet (creates the child schematic if missing). | `--file` `--at` `--size` `--sheet` |
| `kce sch annotate` |  | Annotate references across the whole hierarchy (kicad-cli has no annotate). | `--mode` `--order` `--start` `--sheet-offset` `--clear` |
| `kce sch apply` | INTENT_FILE | Make the schematic match an intent file: components, fields, nets, flags. | `--power-flags` `--prune` `--patch` |
| `kce sch at` | POINT | What is at x,y (or REF:PIN)? Wires, pins, labels, junctions. | `--sheet` |
| `kce sch auto-junctions` |  | Add missing junctions where 3+ wire ends/pins meet. | `--sheet` |
| `kce sch bus-entry` |  |  | `--at` `--size` `--sheet` |
| `kce sch components` |  | Components as KiCad's netlister sees them (whole hierarchy). |  |
| `kce sch connect` | A B | Wire two pins/points: `kce sch connect R1:2 U1:THR` (adds junctions where needed). | `--mode` `--via` `--allow-merge` `--sheet` |
| `kce sch delete` | REF | Delete a symbol (all its units). | `--sheet` |
| `kce sch delete-item` | UUID | Delete any item by uuid (see `kce sch items`). | `--sheet` |
| `kce sch erc` | INPUT_FILE | Runs the Electrical Rules Check (ERC) on the schematic and creates a report | all `kicad-cli sch erc` options |
| `kce sch export bom` | INPUT_FILE | Generate a Bill of Materials (BOM) | all `kicad-cli sch export bom` options |
| `kce sch export dxf` | INPUT_FILE | Export DXF | all `kicad-cli sch export dxf` options |
| `kce sch export hpgl` | INPUT_FILE | Export HPGL | all `kicad-cli sch export hpgl` options |
| `kce sch export netlist` | INPUT_FILE | Export a netlist | all `kicad-cli sch export netlist` options |
| `kce sch export pdf` | INPUT_FILE | Export PDF | all `kicad-cli sch export pdf` options |
| `kce sch export ps` | INPUT_FILE | Export PS | all `kicad-cli sch export ps` options |
| `kce sch export python-bom` | INPUT_FILE | Export the legacy BOM XML format used in the schematic editor with Python script | all `kicad-cli sch export python-bom` options |
| `kce sch export svg` | INPUT_FILE | Export SVG | all `kicad-cli sch export svg` options |
| `kce sch hierarchy` |  | Sheet hierarchy (one entry per sheet instance). |  |
| `kce sch info` |  | Counts, title block, lib_symbols cache and sheets of a schematic file. | `--sheet` |
| `kce sch items` |  | List non-symbol items (wires, labels, junctions, sheets, ...). | `--type` `--sheet` |
| `kce sch junction` |  | Place a junction at x,y (or REF:PIN). Refused when it would join two crossing nets. | `--at` `--allow-merge` `--sheet` |
| `kce sch label` | TEXT | Place a local/global/hierarchical label at x,y (or REF:PIN). | `--at` `--angle` `--kind` `--shape` `--allow-merge` `--sheet` |
| `kce sch label-pin` | PIN NET | Connect REF:PIN to NET via a short wire + net label (the fastest way to wire a design). | `--kind` `--shape` `--stub` `--allow-merge` `--sheet` |
| `kce sch mirror` | REF AXIS |  | `--allow-merge` `--sheet` |
| `kce sch move` | REF | Move a symbol (its fields follow). Wires are not dragged. | `--at` `--by` `--no-snap` `--allow-merge` `--sheet` |
| `kce sch net-of` | PIN | Which net is REF:PIN on (per KiCad's netlister)? |  |
| `kce sch nets` |  | Connectivity from KiCad's own netlister, in a stable machine format. | `--net` |
| `kce sch no-connect` | TARGET | Place a no-connect flag on REF:PIN (or x,y). | `--all-unconnected` `--ref` `--sheet` |
| `kce sch normalize` |  | Let KiCad re-save the schematic hierarchy in canonical form (kicad-cli sch upgrade --force). |  |
| `kce sch paper` | SIZE | Page size: A4, A3, A2, A1, A0, A, B, C, D, E, USLetter, USLegal, USLedger, User. | `--portrait` `--sheet` |
| `kce sch pins` | REF | Pins of a placed symbol: number, name, type, absolute x/y, outward direction. | `--sheet` |
| `kce sch power` | NET | Place a power symbol (power:GND, power:+5V, ...) — directly on a pin if --at REF:PIN. | `--at` `--lib-id` `--angle` `--allow-merge` `--sheet` |
| `kce sch remove-field` | REF NAME |  | `--sheet` |
| `kce sch replace-symbol` | REF LIB_ID | Replace a symbol while protecting its connections (also under --dry-run). | `--pin-map` `--map-by` `--rewire` `--require-same-nets` `--sheet` |
| `kce sch rotate` | REF |  | `--angle` `--by` `--allow-merge` `--sheet` |
| `kce sch set-attr` | REF ATTR VALUE |  | `--sheet` |
| `kce sch set-field` | REF NAME VALUE | Set/add a field (Reference, Value, Footprint, Datasheet, MPN, ...). Applies to all units. | `--hide` `--at` `--sheet` |
| `kce sch set-footprint` | REF FOOTPRINT | Assign a footprint (Library:Name), verified against the fp-lib-table. | `--no-check` `--sheet` |
| `kce sch set-value` | REF VALUE | Set all units of REF; unknown references fail with input/2 before recording undo. | `--sheet` |
| `kce sch sheet-pin` | SHEET_NAME PIN_NAME | Add a sheet pin and the matching hierarchical label inside the child sheet. | `--shape` `--side` `--no-label` `--sheet` |
| `kce sch show` | REF | Full detail of a placed symbol incl. absolute pin positions. | `--sheet` |
| `kce sch symbols` |  | List placed symbols. | `--sheet` `--no-power` `--match` |
| `kce sch text` | TEXT |  | `--at` `--angle` `--size` `--sheet` |
| `kce sch title` |  | Set title block fields. | `--title` `--date` `--rev` `--company` `--comment` `--sheet` |
| `kce sch update-symbols` |  | Refresh embedded symbols from the libraries (Update Symbols from Library), every sheet | `--match` `--sheet` |
| `kce sch upgrade` | INPUT_FILE | Upgrade the schematic file's format to the latest one | all `kicad-cli sch upgrade` options |
| `kce sch variant` | REF VARIANT | Override a symbol in one variant (DNP, BOM/board/position-file flags, field values). | `--dnp` `--in-bom` `--on-board` `--in-pos-files` `--exclude-from-sim` `--field` `--clear` `--sheet` |
| `kce sch verify` |  | Compare KiCad's netlist with an intent file: match, mismatches, and why. | `--expect` `--strict` `--subset` `--check-lib-id` `--check-uuid` `--check-net-names` |
| `kce sch wire` | POINTS | Draw a polyline 'x1,y1;x2,y2;...' (points may also be REF:PIN). | `--bus` `--allow-merge` `--sheet` |
| `kce serve` |  | Serve JSON-RPC 2.0 on stdin/stdout, one request and response per line. | `--request-timeout-ms` |
| `kce session checkpoint` | NAME | Publish an immutable generation under NAME; replacement atomically switches its head. | `--immutable` |
| `kce session checkpoints` |  | List accepted checkpoint heads with generation, content hash, files and size. |  |
| `kce session discard` |  | Drop unsaved in-memory edits (REPL). |  |
| `kce session drop-checkpoint` | NAME | Drop NAME and all its stored generations. |  |
| `kce session history` |  |  | `--limit` |
| `kce session redo` |  |  | `--steps` |
| `kce session restore` | NAME | Restore NAME, validate with KiCad and record undo history. Missing names are input errors. |  |
| `kce session save` |  |  |  |
| `kce session status` |  |  |  |
| `kce session undo` |  |  | `--steps` |
| `kce sim directive add` | TEXT | Add a directive, e.g. '.tran 10u 5m', '.ac dec 20 10 1meg', '.include models.lib'. | `--at` `--sheet` |
| `kce sim directive remove` | KEY | Remove by exact text, uuid, or analysis keyword ('.tran' removes every .tran). | `--sheet` |
| `kce sim directives` |  | SPICE directives placed in the schematic (text items starting with '.'). | `--sheet` |
| `kce sim engine` |  | The ngspice used: an executable, or the shared library shipped with KiCad. |  |
| `kce sim model` | REF | Set a symbol's simulation model (the Sim.* fields KiCad's netlister reads). | `--device` `--type` `--params` `--pins` `--library` `--name` `--clear` `--sheet` |
| `kce sim models` |  | Symbols with their Sim.* fields (empty = KiCad infers R/C/L from the value). | `--sheet` |
| `kce sim netlist` |  | Export KiCad's SPICE netlist (kicad-cli) and list its elements and directives. | `--output` |
| `kce sim run` |  | Simulate: KiCad netlist -> ngspice -> plots and CSV files. | `--analysis` `--probe` `--points` `--output-dir` `--timeout` |
| `kce skill install` |  | Copy SKILL.md + COMMANDS.md into <skills dir>/kicad-cli-extended/. | `--target` `--dir` `--dry-run` `--force` |
| `kce skill path` |  | Directory of the packaged skill files. |  |
| `kce skill show` |  | Print the packaged SKILL.md (or COMMANDS.md). | `--file` |
| `kce skill status` |  | Where the skill is installed and whether each copy is current. |  |
| `kce skill uninstall` |  | Remove the installed skill directories (only ones that hold this skill). | `--target` `--dir` `--dry-run` |
