# Historical cold-agent acceptance evidence

These records document Linux discovery through an installed skill and selected native runs.
They predate the final documentation pass; they do not prove discovery with every later skill
revision or first-user execution on Windows.

## Public evidence and path aliases

Personal paths in commands, excerpts and JSON were replaced with stable aliases for publication:
/workspace/kicad-cli-extended denotes the checkout, /home/example denotes the capture user's
home, and /tmp/agent-* denotes temporary agent directories. These are sanitized records,
not commands rerun at those example locations. Original output_bytes, output_sha256, wheel
hashes and quoted source hashes refer to the original captured bytes, before path sanitization.
They must not be used as checksums of the redacted excerpts or the current documents.
Session ids, timestamps, command order, exit codes, assertions, failed attempts and outcomes
remain in the records. JSON escaping and relative source_plan links remain intact.

## Discovery capture

The original audit used 19 independent agent sessions with fork_turns="none". Prompts allowed
only the installed SKILL.md, COMMANDS.md and installed CLI help. Repository source, tests,
reviews and other agent records were excluded by prompt policy, not an OS sandbox.
[read-evidence.json](read-evidence.json) preserves actual read commands, output excerpts,
original output hashes and session ids. Reasoning traces were not collected.

The distribution was built with pip wheel --no-deps . and installed into a separate environment.
Its kce skill install populated the isolated skill directory. [provenance.json](provenance.json)
links four recorded builds/installs to read times before the agents' first answers. Individual
historical installed-skill hashes were not measured. At the final capture, the repository,
package and installed SKILL.md copies all had this SHA-256:

```text
78075345b5dfae308e4d25cc2ccf21047fb8792f71c10662475209427639477b
```

That historical wheel's SHA-256 was
07852913bf9341d1341338e63da682f3b50b5e4a925b735266f2aa89126105b9. Its info version result was:

```json
{"version":"0.1.0","kicad":"10.0.7","pcbnew":"10.0.7","kicad_cli_spec":"10.0.7"}
```

R1.json through R12.json are the final captured discovery plans. P, B, references and pins
are placeholders that require real project context before execution. These are not complete,
replayable product designs. R2 initially split its choices between argv and continuation notes;
its later formatting transcription quotes the original decisions. The checker requires those
quotes to appear in the saved original notes. Both forms remain under attempts/.

The first R5 omitted strict verify; its first retry omitted plan preview. Early R1/R2/R4/R9
placed plan inputs outside the project. The audit retained those failures, clarified the skill's
preview/run/verify order and project-local input boundary, then captured fresh attempts.
R3 had no datasheet pin table and therefore planned search before creation.

Run the saved discovery checker without calling a model:

```bash
python scripts/check_cold_acceptance.py docs/acceptance/cold-agent
```

A passing result checks command selection, ordering and gates. It does not validate every
placeholder or prove fabrication success. Historical results remain historical evidence.

## Selected native executions

[executions/](executions/) stores selected runs; source_plan identifies the plan chosen before
execution. The recorder retains original stdout hashes and reduces only the large command
catalog to command paths.

| Recipe | Recorded CLI calls | Failure preservation and actual scope |
|---|---:|---|
| R2 | 29 | Initial projectless link-check input/2 retained; after project creation, a representative two-resistor circuit passed intent/ERC/sync/score. This was an intermediate schematic plan, not the final ESP32 product. |
| R5 | 20 | Relative expected-path input/2 retained; after correction, value/footprint/pad preservation and byte restoration of five source files were asserted. The fixture had no existing copper. |
| R9 | 24 | Relative-path input/2 and baseline unrouted DRC design/3 retained; one trial verified intent/score, then DRC rejected it and recovery asserted rollback, worker reuse and byte restoration. |

R9 did not pass baseline DRC. It deliberately tested rejection/recovery, not a keeper or a
5,000-trial search. Its summary also preserves an initial transport failure from a recorder
that waited for stdin EOF, followed by a streaming-recorder continuation. The R5 fixture does
not establish preservation of existing routing. These runs do not establish crash/timeout
recovery across all native engines or operating systems.

The separate tests/test_cold_acceptance.py checks captured-plan correspondence and reruns
small native preview/intent/ECO/resident recovery cases. Those tests supplement the recorded
agent executions; they do not replace them. KCE_COLD_CLI can select a separately installed CLI.

scripts/capture_cold_evidence.py extracts actual read outputs from local session logs;
scripts/import_cold_execution.py collects scripts/record_cold_cli.py results. They do not call
a paid model. Sanitize new captures before publishing them.

## Windows limitation

The historical environment probe returned Linux with no pwsh. Native Windows PowerShell,
fresh configuration and paths with spaces/Unicode were not exercised. Linux path tests and
workflow templates are not Windows first-user acceptance evidence.
