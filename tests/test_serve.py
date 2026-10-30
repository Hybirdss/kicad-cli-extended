"""Transactions and the resident server: ``run`` plans, session checkpoints, ``serve``, piped REPL.

Real KiCad 10 (kicad-cli + pcbnew), like ``test_e2e.py``; every test drives the
installed ``kce`` command, so what it asserts is what a caller sees.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from kicad_cli_extended import COMMAND, ENV_PREFIX, STATE_DIR

pytestmark = pytest.mark.e2e

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
UTF8 = {"encoding": "utf-8", "errors": "replace"}


def _resolve_cli():
    force = os.environ.get(ENV_PREFIX + "FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(COMMAND)
    if path:
        return [path]
    if force:
        raise RuntimeError(f"{COMMAND} not found in PATH. Install with: pip install -e .")
    return [sys.executable, "-m", "kicad_cli_extended"]


CLI = _resolve_cli()


def kce(args, cwd=None, input=None, check=True, timeout=1800, project=None):
    argv = ["--project", project] if project else []
    r = subprocess.run(CLI + [*argv, *[str(a) for a in args]], capture_output=True, text=True, cwd=cwd,
                       input=input, stdin=None if input is not None else subprocess.DEVNULL,
                       timeout=timeout, **UTF8)
    if check and r.returncode != 0:
        raise AssertionError(f"{COMMAND} {' '.join(map(str, args))} failed ({r.returncode}):\n"
                             f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-3000:]}")
    return r


def kcej(args, cwd=None, project=None):
    return json.loads(kce(["--json", *args], cwd=cwd, project=project).stdout)


def kce_err(args, cwd=None, input=None, project=None):
    r = kce(["--json", *args], cwd=cwd, input=input, project=project, check=False)
    assert r.returncode != 0, f"expected failure, got 0: {r.stdout}"
    return r, json.loads(r.stderr)


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
    return path


def digest(root: str) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != STATE_DIR)
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="kce-serve-")
    yield d
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def master():
    d = tempfile.mkdtemp(prefix="kce-serve-master-")
    yield build_project(d)
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


def build_project(root: str) -> str:
    """A small two-symbol schematic plus a board, built only through the CLI."""
    p = os.path.join(root, "tx")
    kce(["project", "new", p, "--name", "tx"])
    kce(["lib", "init-global-tables"], cwd=p)
    kce(["sch", "add", "Device:R", "--ref", "R1", "--at", "60,60", "--value", "330",
         "--footprint", "Resistor_SMD:R_0805_2012Metric"], cwd=p)
    kce(["sch", "add", "Connector_Generic:Conn_01x02", "--ref", "J1", "--at", "40,60",
         "--footprint", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"], cwd=p)
    kce(["sch", "connect", "J1:1", "R1:1"], cwd=p)
    return p


@pytest.fixture
def proj(master, tmp_dir):
    dst = os.path.join(tmp_dir, "tx")
    shutil.copytree(master, dst, ignore=shutil.ignore_patterns(STATE_DIR))
    return dst


# ═════════════════════════════════════════════════════════════════════════════
# run: plans as one transaction
# ═════════════════════════════════════════════════════════════════════════════


class TestPlanSchema:
    def test_shipped_examples_match_the_schema(self):
        import jsonschema
        with open(os.path.join(REPO, "schemas", "plan.schema.json"), encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)
        v = jsonschema.Draft202012Validator(schema)
        for example in schema["examples"]:
            v.validate(example)
        with pytest.raises(jsonschema.ValidationError):
            v.validate({"version": 1, "steps": []})
        with pytest.raises(jsonschema.ValidationError):
            v.validate({"version": 1, "steps": [{"args": ["sch"], "print": True}]})

    def test_help_text(self):
        out = kce(["--help"]).stdout
        assert "run" in out
        assert "steps" in kce(["run", "--help"]).stdout.lower()


class TestRunPlan:
    @pytest.mark.smoke
    def test_plan_runs_steps_in_one_session(self, proj):
        plan = write_json(os.path.join(os.path.dirname(proj), "plan.json"), {
            "version": 1,
            "project": proj,
            "steps": [
                {"args": ["sch", "add", "Device:C", "--ref", "C1", "--at", "80,60"]},
                {"args": ["sch", "connect", "R1:2", "C1:1"]},
                {"args": ["session", "status"]},
            ],
        })
        res = kcej(["run", plan])
        assert [s["step"] for s in res["steps"]] == [1, 2, 3]
        assert [s["args"][0] for s in res["steps"]] == ["sch", "sch", "session"]
        assert res["steps"][0]["result"]["reference"] == "C1"
        # add and connect each recorded one undo entry in the same session
        assert res["steps"][2]["result"]["undo_depth"] == 2
        assert res["steps"][2]["result"]["dirty_files"]
        assert res["saved"] and len(res["validation"]) == 1
        symbols = [s["reference"] for s in kcej(["sch", "symbols"], project=proj)]
        assert sorted(symbols) == ["C1", "J1", "R1"]
        nets = kcej(["sch", "nets"], project=proj)["nets"]
        assert any({"R1:2", "C1:1"} <= {f"{p['ref']}:{p['pin']}" for p in n["pins"]} for n in nets)

    @pytest.mark.smoke
    def test_failing_step_rolls_every_file_back(self, proj):
        before = digest(proj)
        plan = write_json(os.path.join(os.path.dirname(proj), "bad.json"), {
            "version": 1, "project": proj,
            "steps": [
                {"args": ["sch", "add", "Device:C", "--ref", "C1", "--at", "80,60"]},
                {"args": ["sch", "add", "Device:NO_SUCH_SYMBOL", "--ref", "X1", "--at", "100,60"]},
            ],
        })
        r, err = kce_err(["run", plan])
        assert r.returncode == 2
        assert err["category"] == "input" and err["type"] == "KeyError"
        assert err["details"]["step"] == 2
        assert err["details"]["rolled_back"] is True
        assert "NO_SUCH_SYMBOL" in err["error"]
        assert digest(proj) == before

    def test_final_validation_failure_rolls_back(self, proj):
        before = digest(proj)
        plan = write_json(os.path.join(os.path.dirname(proj), "v.json"), {
            "version": 1, "project": proj,
            "steps": [
                {"args": ["sch", "add", "Device:C", "--ref", "C1", "--at", "80,60"]},
                {"args": ["raw", "set", os.path.join(proj, "tx.kicad_sch"), "lib_symbols", "x"]},
            ],
        })
        r, err = kce_err(["run", plan])
        assert r.returncode != 0
        assert err["details"]["rolled_back"] is True
        assert digest(proj) == before

    def test_malformed_plan_is_an_input_error(self, proj):
        for bad in ({"version": 2, "steps": [{"args": ["info", "version"]}]},
                    {"version": 1, "steps": []},
                    {"version": 1, "steps": [{"args": ["sch", "add"]}], "extra": 1},
                    {"version": 1, "steps": ["sch add"]}):
            plan = write_json(os.path.join(os.path.dirname(proj), "m.json"), bad)
            r, err = kce_err(["run", plan])
            assert r.returncode == 2, bad
            assert err["category"] == "input" and err["type"] == "ValueError", bad
            assert "kce run --help" in err["hint"]

    def test_missing_plan_file(self, proj):
        r = kce(["--json", "run", os.path.join(os.path.dirname(proj), "absent.json")], check=False)
        assert r.returncode != 0
        assert os.path.exists(os.path.dirname(proj))
        assert "absent.json" in r.stdout + r.stderr


class TestCheckpoints:
    def test_restore_replaces_unsaved_repl_edits(self, proj):
        commands = ("--json session checkpoint base\n"
                    "--json sch set-value R1 999\n"
                    "--json session restore base\n"
                    "--json sch show R1\nquit\n")
        result = kce(["repl"], project=proj, input=commands)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 4
        assert responses[3]["value"] == "330"
        assert "tx.kicad_sch" in responses[2]["restored"]
        assert kcej(["sch", "show", "R1"], project=proj)["value"] == "330"
        kcej(["session", "undo"], project=proj)
        assert kcej(["sch", "show", "R1"], project=proj)["value"] == "999"
        kcej(["session", "redo"], project=proj)
        assert kcej(["sch", "show", "R1"], project=proj)["value"] == "330"

    @pytest.mark.smoke
    def test_checkpoint_and_restore_round_trip(self, proj):
        saved = kcej(["session", "checkpoint", "base"], project=proj)
        assert saved["checkpoint"] == "base"
        assert "tx.kicad_sch" in saved["files"]
        before = digest(proj)
        kce(["sch", "add", "Device:C", "--ref", "C1", "--at", "80,60"], cwd=proj)
        assert digest(proj) != before
        res = kcej(["session", "restore", "base"], project=proj)
        assert res["checkpoint"] == "base"
        assert "tx.kicad_sch" in res["restored"]
        assert all(v["ok"] for v in res["validation"])
        assert digest(proj) == before

    def test_restore_removes_files_created_later(self, proj):
        kcej(["session", "checkpoint", "base"], project=proj)
        extra = os.path.join(proj, "notes.txt")
        with open(extra, "w", encoding="utf-8") as f:
            f.write("created after the checkpoint")
        res = kcej(["session", "restore", "base"], project=proj)
        assert res["deleted"] == ["notes.txt"]
        assert not os.path.exists(extra)

    def test_missing_checkpoint_is_an_input_error(self, proj):
        kcej(["session", "checkpoint", "base"], project=proj)
        r, err = kce_err(["session", "restore", "nope"], project=proj)
        assert r.returncode == 2
        assert err["category"] == "input"
        assert "base" in err["error"]

    def test_invalid_checkpoint_name(self, proj):
        r, err = kce_err(["session", "checkpoint", "../escape"], project=proj)
        assert r.returncode == 2
        assert err["category"] == "input"
        assert not glob.glob(os.path.join(os.path.dirname(proj), "escape"))

    def test_checkpoint_restore_is_undoable(self, proj):
        kcej(["session", "checkpoint", "base"], project=proj)
        changed = digest(proj)
        kce(["sch", "add", "Device:C", "--ref", "C1", "--at", "80,60"], cwd=proj)
        after_add = digest(proj)
        kcej(["session", "restore", "base"], project=proj)
        assert digest(proj) == changed
        kcej(["session", "undo"], project=proj)
        assert digest(proj) == after_add

    def test_restore_undo_redo_tracks_added_deleted_and_binary_files(self, proj):
        from pathlib import Path
        root = Path(proj)
        (root / 'saved.bin').write_bytes(b'\x00\xff\r\n')
        kcej(['session', 'checkpoint', 'base'], project=proj)
        (root / 'saved.bin').unlink()
        (root / 'later.bin').write_bytes(b'\xfe\x00')
        before = digest(proj)
        kcej(['session', 'restore', 'base'], project=proj)
        restored = digest(proj)
        assert (root / 'saved.bin').read_bytes() == b'\x00\xff\r\n'
        assert not (root / 'later.bin').exists()
        kcej(['session', 'undo'], project=proj)
        assert digest(proj) == before
        kcej(['session', 'redo'], project=proj)
        assert digest(proj) == restored

    def test_checkpoint_and_restore_dry_run(self, proj):
        kcej(['session', 'checkpoint', 'base'], project=proj)
        kce(['sch', 'set-value', 'R1', '999'], cwd=proj)
        before = digest(proj)
        kcej(['--dry-run', 'session', 'restore', 'base'], project=proj)
        assert digest(proj) == before
        kcej(['--dry-run', 'session', 'checkpoint', 'preview'], project=proj)
        assert not os.path.exists(os.path.join(proj, STATE_DIR, 'checkpoints', 'preview'))


class TestPlanBoundaries:
    @pytest.mark.parametrize('bad', [True, '1', None])
    def test_version_is_an_integer(self, bad):
        from kicad_cli_extended.core.plan import validate_plan
        with pytest.raises(ValueError):
            validate_plan({'version': bad, 'steps': [{'args': ['session', 'status']}]})

    @pytest.mark.parametrize('arg', [1, True, {}, None])
    def test_arguments_are_strings(self, arg):
        from kicad_cli_extended.core.plan import validate_plan
        with pytest.raises(ValueError):
            validate_plan({'version': 1, 'steps': [{'args': ['sch', arg]}]})

    def test_plan_dry_run(self, proj, tmp_dir):
        plan = write_json(os.path.join(tmp_dir, 'dry.json'), {'version': 1, 'steps': [
            {'args': ['sch', 'set-value', 'R1', '999']}]})
        before = digest(proj)
        history = kcej(['session', 'history'], project=proj)
        res = kcej(['--dry-run', 'run', plan], project=proj)
        assert res['saved'] == []
        assert digest(proj) == before
        assert kcej(['session', 'history'], project=proj) == history

    def test_final_validation_is_real_and_preserves_history(self, proj, tmp_dir):
        kce(['sch', 'set-value', 'R1', '999'], cwd=proj)
        kcej(['session', 'undo'], project=proj)
        history = kcej(['session', 'history'], project=proj)
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, 'invalid.json'), {'version': 1, 'steps': [
            {'args': ['raw', 'set', os.path.join(proj, 'tx.kicad_sch'), 'version', '99999999']}]})
        r, err = kce_err(['run', plan], project=proj)
        assert r.returncode == 4, err
        assert err['category'] == 'validation'
        assert err['details']['phase'] == 'validation'
        assert digest(proj) == before
        assert kcej(['session', 'history'], project=proj) == history

    def test_project_switch_is_rejected_before_steps(self, proj, tmp_dir):
        other = os.path.join(tmp_dir, 'other')
        kce(['project', 'new', other])
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, 'switch.json'), {'version': 1, 'steps': [
            {'args': ['sch', 'set-value', 'R1', '999']},
            {'args': ['project', 'open', other]}]})
        r, err = kce_err(['run', plan], project=proj)
        assert r.returncode == 2
        assert err['category'] == 'input'
        assert digest(proj) == before


class TestTransactionFiles:
    def test_plan_direct_output_is_undoable(self, proj, tmp_dir):
        before = digest(proj)
        output = os.path.join(proj, "outputs", "snapshot.net")
        plan = write_json(os.path.join(tmp_dir, "export.json"), {"version": 1, "steps": [
            {"args": ["sch", "export", "netlist", "--output", output]}]})
        result = kcej(["run", plan], project=proj)
        assert output in result["saved"]
        after = digest(proj)
        assert after != before
        kcej(["session", "undo"], project=proj)
        assert digest(proj) == before
        kcej(["session", "redo"], project=proj)
        assert digest(proj) == after

    def test_direct_disk_write_is_validated_at_commit(self, proj, tmp_dir):
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, "overwrite.json"), {"version": 1, "steps": [
            {"args": ["sch", "export", "netlist", "--output", os.path.join(proj, "tx.kicad_sch")]}]})
        result, error = kce_err(["run", plan], project=proj)
        assert result.returncode == 4, error
        assert error["category"] == "validation"
        assert error["details"]["phase"] == "validation"
        assert digest(proj) == before

    def test_output_files_and_history_roll_back(self, proj, tmp_dir):
        from pathlib import Path
        root = Path(proj)
        output = root / "outputs" / "snapshot.net"
        output.parent.mkdir()
        output.write_bytes(b"original output\r\n")
        state = root / STATE_DIR / "session.json"
        kce(["sch", "set-value", "R1", "999"], project=proj)
        kcej(["session", "undo"], project=proj)
        history = state.read_bytes()
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, "outputs.json"), {"version": 1, "steps": [
            {"args": ["sch", "export", "netlist", "--output", str(output)]},
            {"args": ["sch", "set-field", "MISSING", "Value", "1"]}]})
        result, error = kce_err(["run", plan], project=proj)
        assert result.returncode == 2
        assert error["details"]["step"] == 2
        assert len(error["details"]["results"]) == 1
        assert digest(proj) == before
        assert state.read_bytes() == history

    def test_rejected_checkpoint_is_atomic(self, proj):
        from pathlib import Path
        import re
        kcej(["session", "checkpoint", "bad"], project=proj)
        root = Path(proj)
        checkpoint = root / STATE_DIR / "checkpoints" / "bad"
        generation = json.loads((checkpoint / 'head.json').read_text(encoding='utf-8'))['generation']
        stored = checkpoint / 'generations' / generation / "files" / "tx.kicad_sch"
        stored.write_bytes(re.sub(rb"\(version \d+\)", b"(version 99999999)", stored.read_bytes(), count=1))
        kce(["sch", "set-value", "R1", "999"], project=proj)
        before = digest(proj)
        state = root / STATE_DIR / "session.json"
        history = state.read_bytes()
        result, error = kce_err(["session", "restore", "bad"], project=proj)
        assert result.returncode == 4
        assert error["category"] == "validation"
        assert digest(proj) == before
        assert state.read_bytes() == history

    def test_plan_rejects_external_output_before_any_edit(self, proj, tmp_dir):
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, "outside.json"), {"version": 1, "steps": [
            {"args": ["sch", "set-value", "R1", "999"]},
            {"args": ["sch", "export", "netlist", "--output", os.path.join(tmp_dir, "outside.net")]}]})
        result, error = kce_err(["run", plan], project=proj)
        assert result.returncode == 2
        assert error["category"] == "input"
        assert digest(proj) == before
        assert not os.path.exists(os.path.join(tmp_dir, "outside.net"))


class TestServer:
    @pytest.mark.smoke
    def test_run_error_and_shutdown(self, proj):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "run", "params": {"args": ["sch", "symbols"], "project": proj}},
            {"jsonrpc": "2.0", "id": 2, "method": "run", "params": {"args": ["sch", "set-field", "MISSING", "Value", "1"], "project": proj}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
            {"jsonrpc": "2.0", "id": 4, "method": "run", "params": {"args": ["session", "status"]}},
        ]
        result = kce(["serve"], input="".join(json.dumps(r) + "\n" for r in requests))
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert [r["id"] for r in responses] == [1, 2, 3]
        assert responses[0]["result"] == kcej(["sch", "symbols"], project=proj)
        _, cli_error = kce_err(["sch", "set-field", "MISSING", "Value", "1"], project=proj)
        error = responses[1]["error"]
        assert error["data"]["kce"] == cli_error
        assert error["data"]["category"] == "input"
        assert error["data"]["exit_code"] == 2
        assert responses[2]["result"] == {"shutdown": True}
        assert result.stderr == ""

    def test_eof_exits_cleanly(self):
        result = kce(["serve"], input=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "run",
                      "params": {"args": ["info", "version"]}}) + "\n")
        response, = [json.loads(line) for line in result.stdout.splitlines()]
        assert response["id"] == 0 and "result" in response

    def test_malformed_requests_and_notifications(self):
        lines = ["{", "[]", '{"jsonrpc":"2.0","id":{},"method":"shutdown"}',
                 '{"jsonrpc":"2.0","id":1,"method":"run","params":{"args":[42]}}',
                 '{"jsonrpc":"2.0","id":2,"method":"missing"}',
                 '{"jsonrpc":"2.0","method":"run","params":{"args":["session","status"]}}',
                 '{"jsonrpc":"2.0","id":3,"method":"shutdown"}']
        result = kce(["serve"], input="\n".join(lines) + "\n")
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 6
        assert [r["error"]["code"] for r in responses[:-1]] == [-32700, -32600, -32600, -32602, -32601]
        assert responses[-1]["result"] == {"shutdown": True}

    def test_request_flags_and_validation_failure_are_isolated(self, proj):
        before = kcej(["sch", "symbols"], project=proj)
        commands = [
            ["raw", "set", os.path.join(proj, "tx.kicad_sch"), "version", "99999999"],
            ["sch", "symbols"],
            ["--dry-run", "sch", "set-value", "R1", "111"],
            ["--no-validate", "session", "status"],
            ["session", "status"],
            ["sch", "show", "R1"],
            ["sch", "set-value", "R1", "123"],
            ["sch", "show", "R1"],
        ]
        requests = [{"jsonrpc": "2.0", "id": i, "method": "run", "params": {"args": args}}
                    for i, args in enumerate(commands)]
        requests.append({"jsonrpc": "2.0", "id": 9, "method": "shutdown"})
        result = kce(["serve"], project=proj, input="".join(json.dumps(r) + "\n" for r in requests))
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 9
        assert responses[0]["error"]["data"]["category"] == "validation"
        assert responses[0]["error"]["data"]["exit_code"] == 4
        assert responses[1]["result"] == before
        assert responses[3]["result"]["validate_on_save"] is False
        assert responses[4]["result"]["validate_on_save"] is True
        assert responses[5]["result"]["value"] == "330"
        assert responses[7]["result"]["value"] == "123"
        assert kcej(["sch", "show", "R1"], project=proj)["value"] == "123"
        assert result.stderr == ""

    def test_help_text(self):
        assert "JSON-RPC" in kce(["serve", "--help"]).stdout

    def test_resident_worker_first_and_warm_latency(self, proj):
        from concurrent.futures import ThreadPoolExecutor
        code = "import os; result = {'pid': os.getpid(), 'drawings': len(list(board.GetDrawings()))}"
        proc = subprocess.Popen(CLI + ["--project", proj, "serve"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **UTF8, text=True)
        executor = ThreadPoolExecutor(max_workers=1)
        def call(rid, args):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "run", "params": {"args": args}}) + "\n")
            proc.stdin.flush()
            response = json.loads(executor.submit(proc.stdout.readline).result(timeout=120))
            assert response["id"] == rid
            assert "error" not in response, response
            return response["result"]
        try:
            args = ["raw", "pcbnew", "--readonly", "--code", code]
            start = time.perf_counter()
            first = call(1, args)["result"]
            first_time = time.perf_counter() - start
            start = time.perf_counter()
            warm = call(2, args)["result"]
            warm_time = time.perf_counter() - start
            assert first["pid"] == warm["pid"]
            assert first["drawings"] == warm["drawings"] == 0
            call(3, ["pcb", "outline", "rect", "--at", "100,100", "--size", "30,20"])
            changed = call(4, args)["result"]
            assert changed["pid"] == first["pid"]
            assert changed["drawings"] == 4
            print(f"resident worker: first_call={first_time:.6f}s warm_call={warm_time:.6f}s; "
                  "same_pid=True, drawings=0->4, errors=0; one observed pair")
            proc.stdin.write('{"jsonrpc":"2.0","id":5,"method":"shutdown"}\n')
            proc.stdin.flush()
            response = json.loads(executor.submit(proc.stdout.readline).result(timeout=120))
            assert response["result"] == {"shutdown": True}
            assert proc.wait(timeout=30) == 0
            assert proc.stdout.read() == ""
            assert proc.stderr.read() == ""
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            executor.shutdown(wait=True)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


class TestWorkerLifecycle:
    def test_timeout_crash_restart_and_reaping(self, proj):
        from kicad_cli_extended.cli import classify_error
        from kicad_cli_extended.utils import kicad_backend as kb
        worker = kb.ResidentWorker()
        params = {"path": os.path.join(proj, "tx.kicad_pcb"),
                  "code": "import os; result = os.getpid()"}
        try:
            original_pid = worker.run("query_script", params)["result"]
            original = worker.proc
            with pytest.raises(kb.KicadError) as caught:
                worker.run("query_script", {**params, "code": "import time; time.sleep(5)"}, timeout=0.1)
            assert classify_error(caught.value) == ("KicadTimeout", "timeout")
            assert original.poll() is not None
            restarted_pid = worker.run("query_script", params)["result"]
            assert restarted_pid != original_pid
            restarted = worker.proc
            with pytest.raises(kb.KicadError) as caught:
                worker.run("query_script", {**params, "code": "import os; os._exit(9)"})
            assert classify_error(caught.value) == ("KicadCrash", "crash")
            assert restarted.poll() == 9
            worker.run("query_script", params)
            last = worker.proc
        finally:
            worker.close()
        assert last.poll() == 0


class TestServerBoundaries:
    @pytest.mark.parametrize("params", [None, [], {"args": []}, {"args": [1]},
                                       {"args": ["session", "status"], "project": None}])
    def test_invalid_params_do_not_dispatch(self, params):
        from kicad_cli_extended.core.serve import Server
        server = Server(lambda *args: pytest.fail("invalid request reached dispatch"))
        response = server.handle(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "run", "params": params}))
        assert response["error"]["code"] == -32602

    def test_bad_shutdown_does_not_stop_server(self):
        from kicad_cli_extended.core.serve import Server
        server = Server(lambda *args: None)
        response = server.handle('{"method":"shutdown","id":1}')
        assert response["error"]["code"] == -32600
        assert not server.stopped

    def test_raw_stdout_is_not_a_response(self):
        import io
        from kicad_cli_extended.core.serve import Server
        def noisy(args, project):
            print("diagnostic")
            return [1, 2]
        out, err = io.StringIO(), io.StringIO()
        server = Server(noisy, out=out, err=err)
        server.serve_forever(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"run","params":{"args":["info","version"]}}\n'))
        assert json.loads(out.getvalue())["result"] == [1, 2]
        assert err.getvalue() == "diagnostic\n"


class TestPipedRepl:
    @pytest.mark.smoke
    def test_one_json_line_per_command(self, proj):
        commands = "sch set-value R1 999\nsch show R1\nproject save\nsch nets\nquit\n"
        result = kce(["--json", "repl"], project=proj, input=commands)
        lines = result.stdout.splitlines()
        assert len(lines) == 5, result.stdout
        responses = [json.loads(line) for line in lines]
        assert {k: responses[0][k] for k in ('reference', 'value')} == {"reference": "R1", "value": "999"}
        assert responses[0]['dry_run'] is False
        assert os.path.join(proj, 'tx.kicad_sch') in responses[0]['would_write']
        assert responses[1]["value"] == "999"
        assert responses[2]["saved"] and len(responses[2]["validation"]) == 1
        assert responses[3]["nets"]
        assert responses[4] == {"quit": True}
        assert kcej(["sch", "show", "R1"], project=proj)["value"] == "999"
        assert result.stderr == ""

    def test_json_mode_survives_multiple_commands(self):
        result = kce(["--json", "repl"], input="session status\nsession history\nquit\n")
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 3
        assert "has_project" in responses[0]
        assert responses[1] == {"undo": [], "redo": []}
        assert responses[2] == {"quit": True}

    def test_help_errors_and_parse_errors_are_json_lines(self, proj):
        commands = "help\nsch set-field MISSING Value 1\nnot-a-command\nsch add 'unterminated\nsession status\nquit\n"
        result = kce(["--json"], project=proj, input=commands)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 6
        assert "help" in responses[0]
        for response in responses[1:4]:
            assert response["category"] == "input"
            assert "kce" in response["hint"]
        assert "has_project" in responses[4]
        assert responses[5] == {"quit": True}

    def test_non_utf8_stdout_stays_valid_json(self, proj, monkeypatch):
        kce(["sch", "set-value", "R1", "Ωé"], project=proj)
        monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
        result = kce(["--json", "repl"], project=proj, input="sch show R1\nquit\n")
        assert result.stdout.isascii()
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 2
        assert responses[0]["value"] == "Ωé"


class TestIsolatedDryRun:
    @pytest.mark.smoke
    def test_plan_preview_preserves_every_project_and_history_byte(self, proj, tmp_dir):
        from pathlib import Path
        def all_bytes():
            return {str(p.relative_to(proj)): p.read_bytes() for p in Path(proj).rglob('*') if p.is_file()}
        before = all_bytes()
        plan = write_json(os.path.join(tmp_dir, 'preview.json'), {'version': 1, 'project': proj, 'steps': [
            {'args': ['sch', 'set-value', 'R1', '470']},
            {'args': ['sch', 'show', 'R1']}]})
        result = kcej(['--dry-run', 'run', plan])
        assert result['dry_run'] and result['saved'] == []
        assert str(Path(proj, 'tx.kicad_sch').resolve()) in result['would_write']
        assert result['steps'][1]['result']['value'] == '470'
        assert before == all_bytes()

    def test_external_library_and_export_destinations_are_isolated(self, proj, tmp_dir):
        from pathlib import Path
        external = Path(tmp_dir) / 'external.kicad_sym'
        result = kcej(['--dry-run', 'lib', 'sym', 'new', str(external), 'Test', '--pin', '1:A:passive:left'], project=proj)
        assert result['dry_run'] and str(external) in result['would_write'] and not external.exists()
        output = Path(tmp_dir) / 'out.net'
        result = kcej(['--dry-run', 'sch', 'export', 'netlist', '--output', str(output)], project=proj)
        assert result['dry_run'] and str(output) in result['would_write'] and not output.exists()


class TestRequestDeadlines:
    @pytest.mark.smoke
    def test_timeout_cancel_reap_and_restart(self, proj):
        from concurrent.futures import ThreadPoolExecutor
        proc = subprocess.Popen(CLI + ['--project', proj, 'serve', '--request-timeout-ms', '30000'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **UTF8)
        executor = ThreadPoolExecutor(max_workers=1)
        def send(rid, method='run', **params):
            proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}) + '\n')
            proc.stdin.flush()
        def read():
            return json.loads(executor.submit(proc.stdout.readline).result(timeout=30))
        try:
            send(1, args=['pcb', 'info'])
            first = read()
            assert first['meta']['worker_reused'] is False
            send(2, args=['pcb', 'info'])
            assert read()['meta']['worker_reused'] is True
            slow = ['raw', 'pcbnew', '--readonly', '--code', 'import time; time.sleep(10); result = 1']
            started = time.monotonic()
            send(3, args=slow, timeout_ms=200)
            error = read()['error']['data']
            assert time.monotonic() - started < 3
            assert error['category'] == 'timeout' and error['details']['exit_code'] == 6
            for key in ('backend', 'operation', 'request_id', 'worker_pid', 'returncode', 'signal',
                        'timeout_ms', 'elapsed_ms', 'commit_state', 'retryable'):
                assert key in error['kce']['details']
            if os.name != 'nt':
                with pytest.raises(ProcessLookupError):
                    os.kill(error['kce']['details']['worker_pid'], 0)
            send(4, args=['pcb', 'info'])
            assert read()['meta']['worker_reused'] is False
            send(5, args=slow)
            time.sleep(0.1)
            send(6, method='cancel', request_id=5)
            responses = [read(), read()]
            assert next(r for r in responses if r['id'] == 6)['result']['cancelled'] is True
            assert next(r for r in responses if r['id'] == 5)['error']['data']['category'] == 'timeout'
            send(7, args=['pcb', 'info'])
            assert read()['meta']['worker_reused'] is False
            send(8, method='shutdown')
            assert read()['result'] == {'shutdown': True}
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            executor.shutdown(wait=True)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


class TestPlanAcceptance:
    @pytest.mark.smoke
    def test_failed_acceptance_rolls_back_files_and_history(self, proj, tmp_dir):
        plan = write_json(os.path.join(tmp_dir, 'accept.json'), {'version': 1, 'steps': [
            {'args': ['sch', 'set-value', 'R1', '470']},
            {'args': ['sch', 'show', 'R1'], 'expect': [{'path': 'value', 'op': 'eq', 'value': '330'}]}]})
        before = digest(proj)
        history = kcej(['session', 'history'], project=proj)
        r, error = kce_err(['run', plan], project=proj)
        assert r.returncode == 3 and error['type'] == 'AcceptanceFailed'
        assert error['details']['actual'] == '470' and error['details']['step'] == 2
        assert error['details']['rolled_back'] and error['details']['commit_state'] == 'rolled_back'
        assert digest(proj) == before and kcej(['session', 'history'], project=proj) == history

    def test_unknown_operator_fails_before_first_mutation(self, proj, tmp_dir):
        plan = write_json(os.path.join(tmp_dir, 'invalid-expect.json'), {'version': 1, 'steps': [
            {'args': ['sch', 'set-value', 'R1', '470']},
            {'args': ['sch', 'show', 'R1'], 'expect': [{'path': 'value', 'op': 'foo', 'value': '470'}]}]})
        before = digest(proj)
        result, error = kce_err(['run', plan], project=proj)
        assert result.returncode == 2 and error['category'] == 'input'
        assert digest(proj) == before


class TestCheckpointHeads:
    def test_list_compare_and_drop_named_checkpoint(self, proj):
        first = kcej(['session', 'checkpoint', 'before', '--immutable'], project=proj)
        assert len(first['content_sha256']) == 64 and first['generation']
        listed = kcej(['session', 'checkpoints'], project=proj)
        assert listed[0]['name'] == 'before' and listed[0]['content_sha256'] == first['content_sha256']
        r, error = kce_err(['session', 'checkpoint', 'before', '--immutable'], project=proj)
        assert r.returncode == 2 and error['type'] == 'FileExistsError'
        kcej(['sch', 'set-value', 'R1', '470'], project=proj)
        diff = kcej(['project', 'compare', 'checkpoint:before'], project=proj)
        assert any(c['ref'] == 'R1' for c in diff['schematic']['components_changed'])
        restored = kcej(['session', 'restore', 'before'], project=proj)
        assert restored['generation'] == first['generation']
        assert restored['content_sha256'] == first['content_sha256']
        kcej(['session', 'drop-checkpoint', 'before'], project=proj)
        assert kcej(['session', 'checkpoints'], project=proj) == []


class TestLandingJourney:
    @pytest.mark.smoke
    def test_one_resident_trial_verifies_intent_scores_and_restores(self, proj, tmp_dir):
        kce(['sch', 'connect', 'J1:2', 'R1:2'], project=proj)
        symbols = kcej(['sch', 'symbols'], project=proj)
        nets = kcej(['sch', 'nets'], project=proj)['nets']
        intent = write_json(os.path.join(proj, 'intent.json'), {'version': 1,
            'components': [{'ref': s['reference'], 'lib_id': s['lib_id'], 'value': s['value']} for s in symbols],
            'nets': [{'name': n['name'], 'pins': [p['ref'] + ':' + p['pin'] for p in n['pins']]} for n in nets]})
        kce(['sch', 'verify', '--expect', intent, '--strict'], project=proj)
        kce(['pcb', 'outline', 'rect', '--at', '100,100', '--size', '30,20'], project=proj)
        kce(['pcb', 'update', '--stable-uuids'], project=proj)
        checkpoint = kcej(['session', 'checkpoint', 'best', '--immutable'], project=proj)
        before = digest(proj)
        plan = write_json(os.path.join(tmp_dir, 'trial.json'), {'version': 1, 'steps': [
            {'args': ['pcb', 'move', 'R1', '--by', '0.5,0']},
            {'args': ['sch', 'verify', '--expect', intent, '--strict']},
            {'args': ['pcb', 'score'], 'expect': [{'path': 'hpwl.total', 'op': 'ge', 'value': 0}]}]})
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'run', 'params': {'args': ['pcb', 'info']}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'run', 'params': {'args': ['run', plan]}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'run', 'params': {'args': ['session', 'restore', 'best']}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'shutdown'}]
        result = kce(['serve'], project=proj, input=''.join(json.dumps(r) + '\n' for r in requests))
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert all('error' not in reply for reply in replies), replies
        assert replies[1]['meta']['worker_reused'] is True
        assert replies[1]['result']['steps'][1]['result']['match'] is True
        assert replies[2]['result']['content_sha256'] == checkpoint['content_sha256']
        assert digest(proj) == before


class TestResidentSessionDirectory:
    @pytest.mark.smoke
    def test_server_requests_inherit_external_state_directory(self, proj, tmp_dir):
        state = os.path.join(tmp_dir, 'external-state')
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'run', 'params': {'args': ['session', 'checkpoint', 'best']}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'run', 'params': {'args': ['session', 'checkpoints']}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'shutdown'}]
        result = kce(['--session-dir', state, 'serve'], project=proj,
                     input=''.join(json.dumps(r) + '\n' for r in requests))
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert all('error' not in r for r in replies), replies
        assert replies[0]['result']['dir'].startswith(state + os.sep)
        assert replies[1]['result'][0]['name'] == 'best'
        assert not os.path.exists(os.path.join(proj, STATE_DIR))


@pytest.mark.parametrize('outside', [False, True])
@pytest.mark.parametrize('absolute', [False, True])
def test_projectless_import_dry_run_preserves_source_and_target(tmp_path, outside, absolute):
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    source_dir = (tmp_path if outside else cwd) / 'source'
    source_dir.mkdir()
    source = source_dir / 'board.PcbDoc'
    fixture = Path(REPO) / '.fixtures/kicad-src/qa/data/pcbnew/plugins/altium/issue24654/PCB1.PcbDoc'
    shutil.copyfile(fixture, source)
    (source_dir / 'keep.txt').write_bytes(b'source companion')
    target = (cwd if outside else tmp_path) / 'target'
    target.mkdir()
    (target / 'keep.txt').write_bytes(b'accepted')
    before = digest(str(tmp_path))
    value = str(source) if absolute else os.path.relpath(source, cwd)
    result = kcej(['--dry-run', 'project', 'import', value, '--dir', target, '--format', 'altium'], cwd=str(cwd))
    assert result['dry_run'] is True
    assert result['read_back']['footprints'] > 0
    assert result['read_back']['source_unchanged'] is True
    assert result['source'] == str(source)
    assert result['would_write'] and all(str(target) in p for p in result['would_write'])
    assert digest(str(tmp_path)) == before


@pytest.mark.smoke
def test_dry_run_creates_project_only_on_isolated_copy(tmp_path):
    target = tmp_path / 'new project'
    result = kcej(['--dry-run', 'project', 'new', target, '--name', 'demo'])
    assert result['dry_run'] and result['would_write']
    assert not target.exists()
    assert all(str(target) in p for p in result['would_write'])


@pytest.mark.smoke
def test_model_copy_dry_run_failure_and_undo(tmp_path):
    from kicad_cli_extended.core import library, sexpr
    project = tmp_path / 'p.kicad_pro'
    project.write_text('{}', encoding='utf-8')
    mod = tmp_path / 'BODY.kicad_mod'
    mod.write_text(sexpr.dumps(library.make_footprint('BODY', library.generate_pads('chip', 2, 2))),
                   encoding='utf-8')
    before = mod.read_bytes()
    source = tmp_path / 'vendor.step'
    source.write_bytes(b'vendor STEP bytes\x00\xff')
    dest = tmp_path / '3d' / source.name
    args = ['lib', 'fp', 'model', 'set', mod, source, '--copy-into', '3d']
    dry = kcej(['--dry-run', *args], project=project)
    assert dry['dry_run'] and not dest.exists() and mod.read_bytes() == before
    failed, error = kce_err([*args, '--offset', 'bad'], project=project)
    assert failed.returncode == 2 and error['category'] == 'input'
    assert not dest.exists() and mod.read_bytes() == before
    kcej(args, project=project)
    assert dest.read_bytes() == source.read_bytes()
    kcej(['session', 'undo'], project=project)
    assert not dest.exists() and mod.read_bytes() == before
