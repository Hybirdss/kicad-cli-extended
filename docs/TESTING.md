# Testing

Use Python 3.10+ and an installed KiCad 10 with pcbnew for native suites. Run from the repository
root with the repository environment. Fetch the official demo fixtures before end-to-end work.

```bash
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py
python -m pytest tests/test_core.py -q
python -m pytest tests/test_core.py tests/test_contract.py tests/test_board.py tests/test_intent.py tests/test_serve.py -q
python -m pytest tests -m smoke -q
python scripts/gen_skill.py --check
KCE_FORCE_INSTALLED=1 python -m pytest tests -q
```

Tests execute in the foreground. Wait for completion before reporting a result. The first command
installs the CLI and test dependencies; repeat it after dependencies or entry points change.
`test_core.py` needs no KiCad. The end-to-end fixture setup fails when KiCad or required demos
are missing. Individual cases have explicit skips for platform-specific behaviour, optional
GUI access, engine-absence checks or an optional demo. A selected suite's pass count is not
a full-suite result.

## Suites and ownership

| File | What it proves | KiCad needed |
|---|---|---|
| `tests/test_core.py` | Parser/geometry helpers, sessions/history/rollback, packaging, generated docs, discovery, errors, safety boundaries and synthetic subprocess cancellation | no |
| `tests/test_contract.py` | Real net-merge protection, sch nets schema/determinism, caller UUIDs and failure categories | yes |
| `tests/test_board.py` | Actual score geometry, stable anonymous IDs, bounded legalize/near, locked placement, closed-outline and sync behaviour | yes |
| `tests/test_intent.py` | Version-1 apply/verify, idempotency, NC/power flags, patch/subset, identity checks, plans and checkpoint recovery | yes |
| `tests/test_serve.py` | Resident JSON-RPC, worker reuse, deadlines/cancellation/recovery, request metadata, imports and real multi-stage journeys | yes |
| `tests/test_e2e.py` | Official demos, live CLI coverage, round trips, from-scratch circuits, exports, packages, previews and library workflows | yes |
| `tests/test_advanced.py` | Differential pairs/length tuning, stitching/fences/teardrops, panelization, models/variants, SPICE and optional live IPC | yes; GUI tests opt in |
| `tests/test_*_gaps.py` | Focused native/synthetic board, scope, score, sync, compare, routing, zone, import, BOM/CPL, manifest and panel regressions | yes for native cases |
| `tests/test_cold_acceptance.py` | Captured agent-chain checks plus small native intent/ECO/resident recovery runs | yes for native cases |

The smoke marker selects a smaller native cross-platform set. It includes CLI coverage, contract,
intent and resident journeys, rather than every export or advanced feature. To select suites:

```bash
python -m pytest tests/test_advanced.py -q
python -m pytest tests/test_e2e.py -q
python -m pytest tests/test_contract.py tests/test_intent.py tests/test_serve.py -q
python -m pytest tests -m smoke -q
python -X warn_default_encoding -W error::EncodingWarning -m pytest tests/test_core.py -q
```

## Generated documentation and schemas

Edit scripts/skill_template.md, then regenerate both skill locations and packaged schemas:

```bash
python scripts/gen_skill.py
python scripts/gen_skill.py --check
python -m pytest tests/test_core.py -k 'commands_md_covers_every_command or ScenarioRecipes or PortableDocumentation' -q
```

`--check` compares bytes without writing. COMMANDS.md walks the live Click tree; its command
count must equal info commands. SKILL.md remains a concise intent-to-chain guide; COMMANDS.md
is the complete reference. Installed skill files live in skill_data beside skill.py so the data
directory cannot shadow the installer module. info schema reads the packaged public schemas.

After a KiCad update, run scripts/gen_kicad_cli_spec.py and rerun live command coverage.
The stored 10.0.7 tree has 43 commands and 582 options. At the same version the live tree must
match exactly. A newer KiCad must retain every stored command/option; new items are reported.

## Native acceptance and recovery

The intent/plan/serve suites cover core/intent.py, core/plan.py and core/serve.py together.
They compare KiCad's exported pin partitions to the approved intent, exercise acceptance before
commit and verify file/history recovery after rejection. Checkpoint tests cover immutable
names, generation publication failure and restore of file additions/deletions.

The resident tests exercise the keep-alive pcbnew worker in utils/kicad_backend.py. A second
request must report reuse; native timeout/cancellation must reap the worker and preserve or
restore project state before acknowledgement. Follow-up requests prove recovery. Timing metadata
with null phase values is not evidence that the phase took zero time. The current deadline is
for native discovery/execution; it does not prove arbitrary Python preemption or Windows process-tree
termination in every environment.

The from-scratch tests build a 555 LED blinker, check nets/ERC, sync and route the PCB, fill
zones, gate DRC and inspect fab packages. The README has a smaller three-part steady LED circuit;
run its command block in a new directory when changing that example. Require the intended three
two-pin nets, no ERC/DRC errors, no unconnected/parity issues, no score overlaps/outside parts,
and a package with complete:true and nonempty files. A static help/example check alone cannot
prove this behaviour.

## Agent discovery evidence

Historical installed-skill records and their limits are in
[acceptance/cold-agent/README.md](acceptance/cold-agent/README.md). They predate later skill edits.
Do not present captured discovery plans as proof of a full manufactured board or Windows execution.
The checker uses saved records and does not call a paid model:

```bash
python scripts/check_cold_acceptance.py docs/acceptance/cold-agent
python -m pytest tests/test_cold_acceptance.py -q
```

## Test configuration

| Variable | Scope |
|---|---|
| `KCE_DEMOS` | Official demo directory, default .fixtures/kicad-src/demos |
| `KCE_FORCE_INSTALLED=1` | Require kce on PATH for suites that otherwise allow python -m fallback |
| `KCE_KEEP_ARTIFACTS=1` | Retain temporary native test projects for inspection |
| `KCE_GUI_TESTS=1` | Enable advanced IPC tests; requires a running editor with API enabled |
| `KCE_COLD_CLI` | Choose a separately installed CLI for cold-acceptance native runs |
| `KCE_AUDIT_CLI`, `KCE_AUDIT_LOG` | scripts/record_cold_cli.py executable and JSONL evidence destination |
| `TMPDIR` | Optional private temporary directory on POSIX, useful when /tmp has a quota |

Operational KCE variables, including native tool paths, timeouts and external session storage,
are listed in the [README configuration](../README.md#configuration).

## Continuous integration and release checks

.github/workflows/ci.yml gates generated docs and runs core tests with implicit-encoding warnings
as errors across its OS/Python matrix. Linux runs the complete native suite; Windows/macOS run
smoke, and Windows also runs the feature-contract files. These are configured jobs, not a claim
that a remote run completed during local documentation work.

.github/workflows/release.yml checks a v-prefixed tag against the package version and main
ancestry, gates generated documents, builds/checks sdist and wheel, tests the installed wheel
and publishes through the protected PyPI environment. Hardware workflow examples are separate
in docs/examples/; they require project-specific pins, assembly policy and rehearsal.
