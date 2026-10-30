# Benchmark method

Question: how many tokens does an AI agent spend, and how long does it wait, to do the same
KiCad jobs with `kce` (CLI + skill) versus the most-starred public KiCad MCP servers?

## Subjects

Selected with `gh search repos "kicad mcp" --sort stars` on 2026-10-28; pinned in
[`servers.json`](servers.json) and fetched (not vendored) by [`fetch_servers.py`](fetch_servers.py):

| Server | Stars | Pinned |
|---|---:|---|
| [mixelpixx/KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server) | 2,995 | release v2.8.3, `1d204fe` |
| [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) | 531 | `main` `98c9ea4` (no releases) |

mixelpixx is measured in its default configuration (all tools) and with
`KICAD_MCP_TOOLBOXES=core`, its documented way to load fewer tools. Tasks use the default
configuration, because with `core` the task tools must first be added with `open_toolbox`.

## 1. Context overhead

What an agent carries before doing any work, counted with **tiktoken `o200k_base`**:

* **MCP**: every tool from `tools/list` as model APIs receive it (`name`, `description`,
  `input_schema` as JSON). These definitions are sent with every model request. Resources,
  resource templates and prompts are counted separately: clients list them but do not
  necessarily place them in the model context.
* **kce**: the skill frontmatter (name + description), which skill-capable agents keep in the
  system prompt, and the SKILL.md body, read once when the skill triggers. `COMMANDS.md` (full
  reference) is only opened on demand and is reported separately.
* Not counted for either side: the agent's own built-in tools (e.g. its shell tool).

## 2. Tasks

Four identical jobs on fresh copies of KiCad's `pic_programmer` demo (KiCad 10 format), each
done the way the side's own tools are meant to be used:

| Task | kce | mixelpixx | lamaalrajih |
|---|---|---|---|
| ERC summary | `check erc` | `run_erc` | no ERC tool |
| DRC | `check drc` | `open_project` + `run_drc` | `run_drc_check` |
| BOM | `sch export bom` | `export_sch_bom` | `export_bom_csv` |
| Gerber + drill | `pcb export gerbers` + `pcb export drill` | `export_gerbers` + `export_drill` | no export tool |

Per task: number of calls; **request tokens** (the tool-call JSON an agent emits: MCP
`{"name", "arguments"}`, kce the shell call `{"command": "kce ..."}`); **response tokens** (the
text the agent gets back: MCP text content, kce stdout + stderr); **wall-clock** seconds per
task (MCP: server already running, startup reported separately; kce: a new process per call).
Outputs are checked on disk (Gerber/drill/BOM files exist); ERC and DRC must return an actual
violation count, not merely an error string. lamaalrajih's tool schemas mark a leaked FastMCP
`ctx` parameter as required, so its calls pass `"ctx": null`. Runs are interleaved across sides
and repeated **five times** (`--reps 5`, the harness default, not the earlier 3); tokens and time
are reported as medians. Five because the median of five interleaved repetitions is a real
observation with two samples on either side. Up to two unusually slow samples cannot occupy
the middle position; with three repetitions, only one can be tolerated. This reduces the
influence of cold starts, timeouts and load bursts without treating five samples as a confidence
interval. Paths are scrubbed after token counting: the home directory is written as `~`, and
the temporary project root as `<benchmark>`. Token counts describe the original local calls.

## 3. Machine load

Wall clock is the one column a busy machine distorts, so the harness gates it. Before every
repetition `run_bench.py` waits — up to `--max-wait` seconds (default 1800, 30 minutes) — until
the 1-minute load average is below `--max-load` (default: CPUs * 0.25, i.e. 4.0 on 16 CPUs;
`--max-load 0` disables the wait). It records `machine.loadavg_at_start` once, one entry per
repetition under `load_gate.reps` (`loadavg`, `waited_seconds`, `started_above_limit`), and
`loadavg` on every run. A repetition that is still above the limit when the wait runs out is run
anyway and recorded with `started_above_limit: true`, so the results file shows which numbers
came from a busy machine instead of hiding it. Timing columns are final only when every
repetition started below the limit.

## Caveats

* The tokenizer is OpenAI's `o200k_base`; other model families tokenize differently, but the
  same text is counted for every side.
* Wall-clock numbers come from one machine. New results record load average at the start,
  per repetition and at the end. Compare sides within a run, not across machines.
  [`results-2026-10-29.json`](results/results-2026-10-29.json) predates the load gate and records
  only `machine.loadavg_at_end` (1-minute load 26.09); that historical run's time column is
  provisional. It has no start, per-repetition or per-run load records.
* Each side's responses reflect its own design choices (e.g. how much of a DRC report it
  returns); the raw first 400 characters of every response are kept in the results file.
* `kce` reports the files written at the output path since that command started (one second of
  slack), so its drill step sometimes lists the Gerber files written just before it and
  sometimes only the `.drl`: the Gerber + drill response varies between runs, and the median is
  reported.
* mixelpixx's tools embed a 30 s command timeout; one ERC run hit it in the historical
  [`results-2026-10-29.json`](results/results-2026-10-29.json) (`tasks.erc.mixelpixx.runs[4]`).

## Reproduce

```bash
python bench/fetch_servers.py --workdir /tmp/kce-bench --kicad-python /usr/bin/python3
python scripts/fetch_fixtures.py
uv run --no-project --with tiktoken==0.14.0 python bench/run_bench.py --workdir /tmp/kce-bench --reps 5
python bench/summarize.py bench/results/results-<date>.json   # the Markdown summary, from the JSON
uv run --no-project --with tiktoken==0.14.0 python bench/test_bench.py   # context-figure self-check
```

The defaults gate every repetition on load (above). On a machine that stays busy, `--max-load 0`
turns the gate off and a shorter `--max-wait` bounds it.

The benchmark runs `kce` from `PATH`, or through this repository's `.venv` when `kce` is not
installed there (`uv run --no-project` has no access to the editable install).

The summary in [`results/`](results/) is generated from the JSON by `bench/summarize.py`, so every
figure in it, and in the README, is traceable to that JSON.
