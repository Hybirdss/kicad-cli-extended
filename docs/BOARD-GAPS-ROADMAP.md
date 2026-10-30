# Board and manufacturing scenario coverage

Implemented commands are listed in the generated [command reference](../skills/kicad-cli-extended/COMMANDS.md).
The acceptance tests in `tests/test_*_gaps.py` exercise the delivered behavior, including real KiCad intake,
routing, replacement geometry, scoped placement, score, panelization and BOM exports.

The following work remains. These are roadmap entries, not supported commands or manufacturing assurances.

| Catalog item | Remaining work | Why it needs a separate implementation |
|---|---|---|
| E1 | Edge placement and connector-facing validation | Courtyard geometry does not identify a connector's opening. A face convention or explicit face metadata and rendered connector fixtures are needed before `face auto` can be trusted. |
| E3 (partial) | Uniform library-reload report and imported-board warnings without adoption | Schematic sync has the detailed report and library reload reports displaced pads. Library-only reload still needs schematic pin coverage and the remaining common report fields. Imported boards with no reference matches also need an explicit warning independent of successful adoption. |
| E5 | Pinned Freerouting installer | Needs release/checksum maintenance, Java compatibility checks and installation tests on each OS. Existing external Freerouting support remains available. |
| E8 | Score baseline deltas and policy thresholds | Needs baseline materialization, explicit zero-baseline semantics and policy acceptance tests on placement trials. The delivered score schema supplies the measurements. |
| E9 | Fence/stitch rejection reasons, coverage and frequency sizing | Clearance, pour and keepout decisions must report their actual rejection cause. Adding counters after the fact would mislabel geometry decisions. Frequency sizing also needs a documented dielectric model. |
| E10 | Differential-pair and group tuning | Requires physical meander/skew acceptance fixtures; selecting the shorter leg alone does not establish a valid tuned route. |
| E11 (partial) | Fab-specific V-score checks | Spacing warnings, strict rejection and source protection are implemented. Copper-to-cut distance, panel rails and tooling requirements need verified manufacturer policies and geometric checks. `--check-fab` is not implemented. |
| E12 | Teardrop recovery on poisoned imported zones | Needs the crashing imported fixture, native zone isolation and a worker-recovery test. Catching a Python exception cannot contain a native crash. |
| E13 | Stackup editing, impedance and differential sizing | Needs loss/stackup provenance and a validated transmission-line model. Imported dielectric defaults must not be presented as source-preserved values. |
| E14 | Altium rule/class translation | KiCad's import report is not an authoritative source constraint export. Needs an ACE rule contract, explicit supported translations, named dropped rules and cross-tool fixtures. |
| E15 | Original encoding/layer provenance and library severity policy | Needs raw source names/bytes and explicit import-loss metadata. Automatically ignoring library errors can conceal genuine footprint mismatches. |
| E16 | ACE differential-pair extraction | ACE-owned parser work; no ACE repository is changed here. |
| F1 | Verified manufacturer rule presets | Requires dated manufacturer capability sources and stackup-specific policy tests. A clean DRC without a recorded fab preset is not a manufacturer qualification. |
| F3 (partial) | Structured rows on raw `sch export bom` | `fab bom-check` implements required fields, variants, missing-price rejection and baseline cost gates. The passthrough CSV export still needs a structured schema covering custom delimiters, labels, ranges and grouping without changing its raw-output contract. |
| F5 | Complete order metadata | Geometry and assembly counts need a published report contract; extended-part classification requires supplier data. Unknown catalog classification must not become a fabricated count. |
| F6 (partial) | `run.rollback_scope` for external artifacts | The package itself stages and publishes atomically. Transaction-wide inclusion/exclusion and restoring previously accepted external outputs belong to the shared plan/checkpoint workstream. A package that was successfully published before a later plan failure is outside the current plan rollback scope. |
| F7 | Preset-aware complete jobset archive | KiCad has no native `fab_package` job. Needs a declared kce extension or wrapper, staged assembly outputs, and archive acceptance tests. Current native jobset archives contain Gerbers/drill; use `fab package` for BOM/CPL hand-off. |
| F11 | Stable visual diff directory and overlays | Needs renderer/layout changes and image acceptance tests for moved/changed parts on both OSes. The semantic compare report does not substitute for this artifact. |
| F13 | File-aware check violations and CI report formats | Requires hierarchy-to-file provenance, then GitHub/JUnit/SARIF escaping and schema validation. A guessed root schematic filename would produce misleading annotations. |
| F14 | Parallel checks and progress protocol | Needs backend concurrency/deadline validation. Progress must not corrupt the established single JSON error envelope on stderr. |
| F15 | CI/release workflow generation | Needs verified KiCad/kce installation and version-pinning recipes on Linux and Windows, plus local workflow linting. |
| F16 | Cross-platform project CI artifacts | Needs actual Windows and Ubuntu executions and artifact comparison. No remote workflow is triggered or claimed here; this task does not authorize pushing. |
| CROSS-CHECK 14 (partial) | Independent cross-format equivalence | KCE sync now reports adoption, missing pins, displaced pads and stranded track endpoints, preserves footprint UUIDs/routing, and offers `--lock-existing`. Comparing the imported source's compiled partitions, geometry and rules still requires the shared ACE/KCE source contract and an independent checker. |

For existing output directories, atomic publication uses Linux `renameat2(RENAME_EXCHANGE)`.
On platforms without directory exchange, use a fresh `--output-dir`; replacement fails without deleting the accepted package.
Package completeness includes every requested export, including STEP. Use `--no-step` deliberately if models are unavailable.
