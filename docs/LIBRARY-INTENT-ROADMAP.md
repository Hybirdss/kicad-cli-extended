# Library and intent follow-up

The supported chain is search → inspect → create/register a KiCad library →
`lib fp check` and `lib link-check` → `sch apply` → `sch verify` → ERC → PCB sync → DRC.
Use `sch apply eco.json --patch` and `sch verify --subset` for a partial ECO.
Source conversion and source equivalence require additional evidence described below.

## Common source contract (CROSS-CHECK 12, D10)

The shipped intent remains **version 1**. Optional `no_connect`, `power_flags`, `remove`,
`dnp`, `in_bom` and `on_board` preserve existing inputs and have parser/schema checks.
No consumer should infer that an ACE native pin-row array is a kce intent. A matching
`sch verify` proves that the KiCad schematic realizes that intent; it does not prove that
the intent represents the Altium source completely.

A common successor contract needs agreement with the ACE exporter before either tool
publishes a new version. Its acceptance fixture must distinguish:

- Source document identity, sheet **instance** path, component UUID, unit and symbol pin;
  footprint identity and pad identifiers occupy a separate domain. References are display
  names, not globally unique endpoint identifiers.
- Connected nets (including single-pin nets), explicit no-connect decisions, unresolved
  endpoints and electrically unconnected pins. Omitted source endpoints are losses.
- Per-variant effective DNP, BOM and on-board decisions, with their source identities.
- Effective compile options, authoritative compiled versus provisional native extraction,
  enumerated coverage, unavailable dependencies, and named unsupported constructs.
- Explicit source-to-target endpoint mapping, artifact hashes, and deterministic reruns.

Completion requires an ACE-produced fixture, JSON Schema validation on both sides, every
endpoint accounted for, an independent partition comparison against actual KiCad exports,
and an idempotent apply/verify/sync rerun. This branch does not invent a v1.1/v2 negotiation
or silently accept unknown intent keys. The ACE `--format kce-intent` exporter (D10) belongs
in the ACE repository; symbol guesses must remain review-required in its loss inventory.

## Library conversion verification (CROSS-CHECK 13)

KCE can inspect unregistered `.kicad_sym` / `.kicad_mod` files, inspect/copy project-relative
models, compare symbol pin numbers with copper pad numbers, and report conservative
pad-clearance and courtyard findings. `lib fp check` exposes its coverage and unsupported
geometry. Model existence is a filesystem check, not STEP geometry verification. Default
netclass clearance does not evaluate conditional custom rules. Requested IPC compliance
and custom pad primitives cannot yield `ok:true`.

A complete conversion needs an ACE exporter for **all** SchLib/PcbLib/IntLib members,
including unused members, and an inventory with source hashes and identities. It must
account for pin names/numbers/types/units, source encoding and units, symbol graphics,
pad numbers/shapes/stacks/primitives, footprint graphics, model transforms, and embedded
model bytes. Named losses block `converted/verified`, even if a placed subset passes DRC.

The KCE acceptance gate then needs source-independent expected data, target reload through
KiCad, complete membership and endpoint checks, geometry comparisons with explicit
tolerances, resolved model paths, link/clearance checks, and deterministic output hashes.
A target comparing against its own generated manifest is insufficient evidence. Neither
`lib fp check ok:true` nor `sch verify match:true` is a conversion certificate.

## Remaining catalog items

| Item | Reason for deferral | Next acceptance proof |
|---|---|---|
| C6 stacked pins, hidden/power flags, multi-unit creation, pins JSON | Extending the box generator needs a precise unit/common-graphics and body-style model; preserving placement on a show→new round trip also needs pin coordinates and visibility, not only pin lists. | Stack pins 4+9 at one point; round-trip every show pin property and each unit through KiCad. |
| C8 bundle/vendor import | ZIP extraction needs collision/traversal handling, atomic multi-file registration, model relocation and a source loss inventory. Native Altium conversion also depends on the ACE exporter above. | Import a deterministic self-contained KiCad ZIP, verify all links/models, then verify a native vendor fixture without omitted members. |
| C9 incremental index | Current aggregate-cache contract remains. Per-library cache invalidation and concurrent atomic publication need design and Windows coverage; no speed target was measured here. | Change one symbol file or one footprint within a pretty directory; prove only that library reparses, including concurrent readers and a lib-table change. |
| D4 schematic free-space and add-near | Symbol origins do not bound graphics, fields, wires or hierarchy pins. A placement heuristic needs obstacle geometry before promising free space. | A chosen box contains no relevant items and insertion adds no ERC overlap/wire faults on pic_programmer. |
| D5 grouped units and PCB noise baselines | Grouped schematic output changes the consumer shape; PCB path/noise reporting and baseline persistence are shared with the board worker. | Group U2 units while preserving instance identities; prove repeat PCB sync has no material updates or newly unmatched pads. |
| D7 deterministic generated UUID namespace | Component UUIDs are supported, but byte-identical schematics also require deterministic root/sheet UUIDs and generated power references. Changing only label UUIDs would not satisfy the stated hash test. | Two fresh projects under an explicit namespace produce equal schematic hashes and retain identities on rerun. |
| D8 USB-C design rule checker | A correct rule engine must distinguish sink/source roles, parallel resistor paths, shield policy and power-path topology. ERC cleanliness is insufficient. | Positive USB sink plus deleted/wrong-value/shorted CC resistor and missing-power-path mutants; each finding names a repair. |

Symbol replacement rewires existing endpoints and attached labels/flags. If a pin lies in
the middle of a wire, other symbols share its point, or the requested mapping is ambiguous,
KiCad netlist verification may refuse the candidate. Automatic sheet rerouting and arbitrary
multi-unit pin migration remain outside this command's promise.
