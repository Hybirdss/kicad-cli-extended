"""Installed-distribution executions accompanying the cold-agent discovery records.

KCE_COLD_CLI selects a separately installed wheel when auditing a release. These
small circuits prove stage acceptance and recovery, not a complete USB-C product.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest


REPO = Path(__file__).parents[1]
CLI = ([os.environ["KCE_COLD_CLI"]] if os.environ.get("KCE_COLD_CLI") else
       [shutil.which("kce")] if shutil.which("kce") else [sys.executable, "-m", "kicad_cli_extended"])
spec = importlib.util.spec_from_file_location("cold_checker", REPO / "scripts/check_cold_acceptance.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def run(args, *, project=None, input=None):
    command = [*CLI, "--json", *(["-p", str(project)] if project else []), *map(str, args)]
    result = subprocess.run(command, input=input, capture_output=True, text=True,
                            encoding="utf-8", timeout=180, cwd=project.parent if project else None)
    assert result.returncode == 0, (command, result.stdout, result.stderr)
    return json.loads(result.stdout) if input is None else [json.loads(line) for line in result.stdout.splitlines()]


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
            and ".kicad-cli-extended" not in path.parts and "outputs" not in path.parts}


@pytest.mark.parametrize("recipe", range(1, 13))
def test_captured_first_chain(recipe):
    record = json.loads((REPO / "docs/acceptance/cold-agent" / f"R{recipe}.json").read_text(encoding="utf-8"))
    assert checker.check_record(recipe, record) == []


def test_original_eco_discovery_is_rejected_for_omitting_strict_verification():
    record = json.loads((REPO / "docs/acceptance/cold-agent/attempts/R5-initial.json").read_text(encoding="utf-8"))
    errors = checker.check_record(5, record)
    assert "missing sch verify" in errors
    assert "pcb update precedes strict intent verification" in errors


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


@pytest.mark.parametrize("recipe,paths", [
    (2, ["session checkpoint", "sch apply", "sch verify", "check erc"]),
    (5, ["session checkpoint", "run", "sch verify", "pcb update", "project compare"]),
    (9, ["session checkpoint", "sch verify", "pcb score", "serve", "session restore"]),
])
def test_agent_execution_evidence_corresponds_to_its_selected_stages(recipe, paths):
    root = REPO / "docs/acceptance/cold-agent"
    execution = json.loads((root / f"executions/R{recipe}.json").read_text(encoding="utf-8"))
    original = json.loads((root / execution["source_plan"]).read_text(encoding="utf-8"))
    planned = checker.expanded_chain(original)
    observed = [node["args"] for node in objects(execution) if isinstance(node.get("args"), list)]
    for path in paths:
        assert any(checker.starts(row, path) for row in planned), (recipe, path, "not selected")
        assert any(checker.starts(row, path) for row in observed), (recipe, path, "not executed")
    assert any(node.get("match") is True for node in objects(execution))
    assert any(call["exit"] == 2 for call in execution["calls"]), "initial input errors must remain visible"
    if recipe == 9:
        assert any(node.get("category") == "design" for node in objects(execution))
        assert any(node.get("rolled_back") is True for node in objects(execution))
        assert any(node.get("worker_reused") is True for node in objects(execution))
    summary = json.loads((root / f"executions/R{recipe}-summary.json").read_text(encoding="utf-8"))
    failures = [row for row in summary["assertions"] if not row["passed"]]
    if recipe == 2:
        assert len(failures) == summary["expected_initial_failure_count"] == 1
        assert failures[0]["actual"]["exit"] == 2
        assert "needs a project" in failures[0]["actual"]["stderr"]
        assert summary["representative_execution_passed"] is True
        assert any(checker.starts(call["args"], "lib link-check") and call["exit"] == 0
                   for call in execution["calls"])
    else:
        assert summary["assertions"] and failures == []


def test_discovery_gate_rejects_missing_preview_verify_checkpoint_and_drc():
    record = {"prompt": "ECO", "reads": ["installed SKILL.md"], "chain": [
        ["pcb", "update"], ["fab", "package"]]}
    errors = checker.check_record(5, record)
    assert "pcb update precedes its preview" in errors
    assert "pcb update precedes strict intent verification" in errors
    assert "mutation precedes checkpoint" in errors
    assert "fab package omits --fail-on-drc" in errors


@pytest.mark.parametrize("args", [
    ["check", "diagnose", "--fail-on", "none"],
    ["check", "diagnose", "--only", "drc", "--fail-on", "error"],
    ["check", "erc"],
])
def test_report_only_or_drc_only_diagnose_is_not_an_erc_gate(args):
    assert checker.design_gate(args, "erc") is False


def test_recorder_forwards_a_reply_before_stdin_eof(tmp_path):
    log = tmp_path / "calls.jsonl"
    env = dict(os.environ, KCE_AUDIT_CLI=sys.executable, KCE_AUDIT_LOG=str(log))
    child = "import sys; [print(line.strip(), flush=True) for line in sys.stdin]"
    process = subprocess.Popen([sys.executable, str(REPO / "scripts/record_cold_cli.py"), "-u", "-c", child],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            process.stdin.write("first request\n")
            process.stdin.flush()
            assert executor.submit(process.stdout.readline).result(timeout=5) == "first request\n"
            process.stdin.close()
            assert process.wait(timeout=5) == 0
            record = json.loads(log.read_text(encoding="utf-8"))
            assert record["stdout"] == "first request\n" and record["exit"] == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


@pytest.fixture(scope="module")
def circuit(tmp_path_factory):
    control = tmp_path_factory.mktemp("cold agent Ω space")
    project = control / "board Ω space"
    run(["info", "doctor"])
    commands = run(["info", "commands", "--full"])
    assert {"run", "sch apply", "sch verify", "pcb score", "serve"} <= {row["command"] for row in commands}
    run(["project", "new", project, "--name", "board"])
    run(["session", "checkpoint", "initial", "--immutable"], project=project)
    intent = write(project / "intent.json", {"version": 1, "components": [
        {"ref": ref, "lib_id": "Device:R", "value": "330", "at": [60 + 20 * i, 60],
         "footprint": "Resistor_SMD:R_0805_2012Metric"} for i, ref in enumerate(("R1", "R2"))],
        "nets": [{"name": "A", "pins": ["R1:1", "R2:1"]},
                 {"name": "B", "pins": ["R1:2", "R2:2"]}]})
    plan = write(control / "schematic.json", {"version": 1, "steps": [
        {"args": ["sch", "apply", str(intent)]},
        {"args": ["sch", "verify", "--expect", str(intent), "--strict"],
         "expect": [{"path": "match", "op": "true"}]},
        {"args": ["check", "erc", "--fail-on", "error"],
         "expect": [{"path": "errors", "op": "eq", "value": 0}]}]})
    before = snapshot(project)
    preview = run(["--dry-run", "run", plan], project=project)
    assert preview["dry_run"] and snapshot(project) == before
    accepted = run(["run", plan], project=project)
    assert accepted["steps"][1]["result"]["match"] is True
    assert accepted["steps"][2]["result"]["errors"] == 0
    run(["pcb", "outline", "rect", "--at", "0,0", "--size", "100,100"], project=project)
    before = snapshot(project)
    run(["--dry-run", "pcb", "update", "--stable-uuids"], project=project)
    assert snapshot(project) == before
    run(["pcb", "update", "--stable-uuids"], project=project)
    run(["pcb", "move", "R1", "--at", "20,20"], project=project)
    run(["pcb", "move", "R2", "--at", "30,20"], project=project)
    score = run(["pcb", "score"], project=project)
    assert score["outside_outline"]["count"] == score["courtyard_overlaps"]["count"] == 0
    return control, project, intent


@pytest.mark.e2e
def test_first_board_accepts_intent_and_stable_sync(circuit):
    _, project, intent = circuit
    assert run(["sch", "verify", "--expect", intent, "--strict"], project=project)["match"] is True
    before = run(["pcb", "footprints"], project=project)
    run(["--dry-run", "pcb", "update", "--stable-uuids"], project=project)
    run(["pcb", "update", "--stable-uuids"], project=project)
    assert run(["pcb", "footprints"], project=project) == before


@pytest.mark.e2e
def test_eco_preview_verifies_then_restores_bytes(circuit):
    control, project, intent = circuit
    checkpoint = run(["session", "checkpoint", "pre-eco", "--immutable"], project=project)
    before = snapshot(project)
    footprints = run(["pcb", "footprints"], project=project)
    edited = json.loads(intent.read_text(encoding="utf-8"))
    edited["components"][0]["value"] = "470"
    expected = write(project / "eco-intent.json", edited)
    plan = write(control / "eco.json", {"version": 1, "steps": [
        {"args": ["sch", "set-value", "R1", "470"]},
        {"args": ["sch", "verify", "--expect", str(expected), "--strict"],
         "expect": [{"path": "match", "op": "true"}]}]})
    preview_before = snapshot(project)
    run(["--dry-run", "run", plan], project=project)
    assert snapshot(project) == preview_before
    result = run(["run", plan], project=project)
    assert result["steps"][1]["result"]["match"] is True
    run(["--dry-run", "pcb", "update", "--stable-uuids"], project=project)
    run(["pcb", "update", "--stable-uuids"], project=project)
    after = run(["pcb", "footprints"], project=project)
    # Value changes may update text; footprint identities and positions must survive.
    assert [(fp["reference"], fp["uuid"], fp["x"], fp["y"]) for fp in after] == [
        (fp["reference"], fp["uuid"], fp["x"], fp["y"]) for fp in footprints]
    diff = run(["project", "compare", "checkpoint:pre-eco"], project=project)
    assert any(row["ref"] == "R1" for row in diff["schematic"]["components_changed"])
    restored = run(["session", "restore", "pre-eco"], project=project)
    assert restored["content_sha256"] == checkpoint["content_sha256"]
    assert snapshot(project) == before


@pytest.mark.e2e
def test_resident_trial_keeps_warm_worker_and_restores(circuit):
    control, project, intent = circuit
    checkpoint = run(["session", "checkpoint", "best", "--immutable"], project=project)
    before = snapshot(project)
    plan = write(control / "trial.json", {"version": 1, "steps": [
        {"args": ["pcb", "move", "R1", "--by", "0.5,0"]},
        {"args": ["sch", "verify", "--expect", str(intent), "--strict"]},
        {"args": ["pcb", "score"], "expect": [
            {"path": "courtyard_overlaps.count", "op": "eq", "value": 0},
            {"path": "outside_outline.count", "op": "eq", "value": 0}]}]})
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "run", "params": {"args": ["pcb", "info"]}},
        {"jsonrpc": "2.0", "id": 2, "method": "run", "params": {"args": ["run", str(plan)]}},
        {"jsonrpc": "2.0", "id": 3, "method": "run", "params": {"args": ["session", "restore", "best"]}},
        {"jsonrpc": "2.0", "id": 4, "method": "shutdown"}]
    replies = run(["serve"], project=project, input="".join(json.dumps(row) + "\n" for row in requests))
    assert all("error" not in reply for reply in replies), replies
    assert replies[1]["meta"]["worker_reused"] is True
    assert replies[1]["result"]["steps"][1]["result"]["match"] is True
    assert replies[2]["result"]["content_sha256"] == checkpoint["content_sha256"]
    assert snapshot(project) == before
