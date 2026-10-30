"""End-to-end tests: real kicad-cli + pcbnew, KiCad's official demo projects, installed ``kce``.

KiCad 10 is a hard dependency: these tests FAIL (never skip) when it is missing.
Demo fixtures: $KCE_DEMOS or <repo>/.fixtures/kicad-src/demos
(fetch with ``python scripts/fetch_fixtures.py``).
Run against the installed command with KCE_FORCE_INSTALLED=1.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from kicad_cli_extended import COMMAND, ENV_PREFIX, STATE_DIR
from kicad_cli_extended.core import sexpr
from kicad_cli_extended.utils import kicad_backend as kb
from kicad_cli_extended.utils.kicad_cli_help import walk_kicad_cli

pytestmark = pytest.mark.e2e

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def demos_dir() -> str:
    d = os.environ.get(ENV_PREFIX + "DEMOS") or os.path.join(REPO, ".fixtures", "kicad-src", "demos")
    if not os.path.isdir(d):
        raise RuntimeError(f"KiCad demo fixtures not found at {d}. Fetch them with `python scripts/fetch_fixtures.py` "
                           f"or set {ENV_PREFIX}DEMOS.")
    return d


def demo_copy(name: str, dest_root: str) -> str:
    dst = os.path.join(dest_root, name)
    shutil.copytree(os.path.join(demos_dir(), name), dst)
    return dst


def _resolve_cli(name):
    """Installed command, else ``python -m kicad_cli_extended`` (set KCE_FORCE_INSTALLED=1 to require it)."""
    force = os.environ.get(ENV_PREFIX + "FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m kicad_cli_extended")
    return [sys.executable, "-m", "kicad_cli_extended"]


CLI = _resolve_cli(COMMAND)
UTF8 = {"encoding": "utf-8", "errors": "replace"}


def run(args, check=True, cwd=None, env=None, input=None):
    r = subprocess.run(CLI + [str(a) for a in args], capture_output=True, text=True, cwd=cwd, env=env,
                       input=input, stdin=None if input is not None else subprocess.DEVNULL, timeout=1800, **UTF8)
    if check and r.returncode != 0:
        raise AssertionError(f"{COMMAND} {' '.join(map(str, args))} failed ({r.returncode}):\n"
                             f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-3000:]}")
    return r


def rj(args, cwd=None):
    r = run(["--json", *args], cwd=cwd)
    return json.loads(r.stdout)


def read_text(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def opath(result, entry_or_name, key="output_dir"):
    """Absolute path of an output listed relative to the result's output_dir."""
    name = entry_or_name["path"] if isinstance(entry_or_name, dict) else entry_or_name
    return os.path.join(result[key], name)


def artifact(path, label=""):
    print(f"\n  {label or os.path.splitext(path)[1]}: {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="kce-e2e-")
    yield d
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 fixture: a 555 LED blinker built from scratch through the CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_blinker(root: str) -> str:
    p = os.path.join(root, "blink")
    run(["project", "new", p, "--name", "blink", "--title", "555 LED blinker"])
    P = ["--project", p]
    run(P + ["lib", "init-global-tables"])
    adds = [
        ("Timer:NE555P", "U1", "101.6,76.2", "Package_DIP:DIP-8_W7.62mm", None, []),
        ("Device:R", "R1", "76.2,63.5", "Resistor_SMD:R_0805_2012Metric", "1k", []),
        ("Device:R", "R2", "76.2,81.28", "Resistor_SMD:R_0805_2012Metric", "10k", []),
        ("Device:C", "C1", "76.2,99.06", "Capacitor_SMD:C_0805_2012Metric", "10u", []),
        ("Device:C", "C2", "88.9,99.06", "Capacitor_SMD:C_0805_2012Metric", "10n", []),
        ("Device:R", "R3", "127,71.12", "Resistor_SMD:R_0805_2012Metric", "330", []),
        ("Device:LED", "D1", "127,86.36", "LED_SMD:LED_0805_2012Metric", "RED", ["--angle", "90"]),
        ("Connector_Generic:Conn_01x02", "J1", "50.8,76.2",
         "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "PWR", ["--mirror", "y"]),
    ]
    for lib_id, ref, at, fp, val, extra in adds:
        args = P + ["sch", "add", lib_id, "--ref", ref, "--at", at, "--footprint", fp] + extra
        if val:
            args += ["--value", val]
        run(args)
    nets = {"VCC": ["U1:VCC", "U1:~{RST}", "R1:1", "J1:1"], "GND": ["U1:GND", "C1:2", "C2:2", "D1:1", "J1:2"],
            "DIS": ["R1:2", "U1:DISCH"], "THR": ["R2:2", "U1:THRES", "U1:TRIG", "C1:1"],
            "CV": ["U1:CONT", "C2:1"], "OUT": ["U1:OUT", "R3:1"]}
    for net, pins in nets.items():
        for pin in pins:
            run(P + ["sch", "label-pin", pin, net])
    run(P + ["sch", "connect", "R1:2", "R2:1"])
    run(P + ["sch", "connect", "R3:2", "D1:2"])
    run(P + ["sch", "power", "PWR_FLAG", "--at", "J1:1"])
    run(P + ["sch", "power", "PWR_FLAG", "--at", "J1:2"])
    run(P + ["sch", "annotate"])
    run(P + ["pcb", "outline", "rect", "--at", "100,100", "--size", "40,30", "--radius", "1"])
    run(P + ["pcb", "update"])
    run(P + ["pcb", "autoroute", "--skip-net", "/GND"])
    run(P + ["pcb", "zone", "add", "--net", "/GND", "--layer", "F.Cu", "--layer", "B.Cu", "--clearance", "0.3",
             "--pad-connection", "tht_thermal"])
    return p


@pytest.fixture(scope="module")
def blink_master():
    root = tempfile.mkdtemp(prefix="kce-blink-")
    path = build_blinker(root)
    print(f"\n  blinker project: {os.path.basename(path)}")
    yield path
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def blink(blink_master, tmp_dir):
    dst = os.path.join(tmp_dir, "blink")
    shutil.copytree(blink_master, dst, ignore=shutil.ignore_patterns("outputs", STATE_DIR))
    return dst


# ═════════════════════════════════════════════════════════════════════════════
# kicad-cli coverage
# ═════════════════════════════════════════════════════════════════════════════

def _cli_module():
    from kicad_cli_extended import cli
    return cli


def _find_command(group, path):
    cmd = group
    for name in path:
        assert isinstance(cmd, click.Group), f"{' '.join(path)}: {name} parent is not a group"
        assert name in cmd.commands, f"kce command missing: {' '.join(path)} (no '{name}')"
        cmd = cmd.commands[name]
    return cmd


@pytest.fixture(scope="module")
def live_tree():
    if True:
        tree = walk_kicad_cli()
        leaves = {k: v for k, v in tree.items() if not v["subcommands"]}
        print(f"\n  live kicad-cli {kb.kicad_version()}: {len(leaves)} leaf commands, "
              f"{sum(len(v['options']) for v in leaves.values())} options")
        return leaves


def _check_spec(stored, live_tree, live_version):
    """Stored spec vs. live tree. Same version: exact. Newer live: stored must survive, extras warn."""
    spec = stored["commands"]
    live_cmds = set(live_tree)
    gone = sorted(set(spec) - live_cmds)
    assert not gone, f"kicad-cli dropped commands; regenerate with scripts/gen_kicad_cli_spec.py: {gone}"
    added_opts = {}
    for k in spec:
        stored_opts = {o["long"] for o in spec[k]["options"]}
        live_opts = {o["long"] for o in live_tree[k]["options"]}
        gone_opts = sorted(stored_opts - live_opts)
        assert not gone_opts, f"{k}: kicad-cli dropped options {gone_opts}"
        if live_opts - stored_opts:
            added_opts[k] = sorted(live_opts - stored_opts)
    added = sorted(live_cmds - set(spec))
    if live_version == stored["kicad_cli_version"]:
        assert not added, ("kicad-cli changed; regenerate with scripts/gen_kicad_cli_spec.py: "
                           f"new commands {added}, new options {added_opts}")
        assert not added_opts, f"kicad-cli changed; regenerate with scripts/gen_kicad_cli_spec.py: {added_opts}"
    elif added or added_opts:
        warnings.warn(
            f"live kicad-cli {live_version} has commands/options newer than the stored "
            f"{stored['kicad_cli_version']} spec: commands={added} options={added_opts}; "
            "regenerate with scripts/gen_kicad_cli_spec.py", stacklevel=2)


class TestKicadCliCoverage:
    @pytest.mark.smoke
    def test_every_subcommand_and_option_reachable(self, live_tree):
        from kicad_cli_extended.core.kicad_cli_cmds import command_path
        mod = _cli_module()
        missing = []
        checked = 0
        for kpath, info in live_tree.items():
            try:
                cmd = _find_command(mod.cli, command_path(kpath))
            except AssertionError as e:
                missing.append(str(e))
                continue
            opts = {o for p in cmd.params for o in getattr(p, "opts", [])}
            for o in info["options"]:
                checked += 1
                if o["long"] not in opts:
                    missing.append(f"{kpath}: option {o['long']} not exposed by `kce {' '.join(command_path(kpath))}`")
            if info["positionals"] and not any(isinstance(p, click.Argument) for p in cmd.params):
                missing.append(f"{kpath}: positional input not exposed")
        print(f"\n  checked {len(live_tree)} commands / {checked} options against kce")
        assert not missing, "kicad-cli features not reachable through kce:\n" + "\n".join(missing)

    @pytest.mark.smoke
    def test_spec_matches_live_kicad(self, live_tree):
        """Exact equality on the stored KiCad version; a newer KiCad may only add things."""
        from kicad_cli_extended.core.kicad_cli_cmds import load_spec
        stored = load_spec()
        _check_spec(stored, live_tree, kb.kicad_version())

        # The newer-KiCad branch CI takes when the PPA/cask move past the stored version.
        older = {"kicad_cli_version": "10.0.5", "commands": {"pcb export svg": {"options": [{"long": "--output"}]}}}
        newer = {"pcb export svg": {"options": [{"long": "--output"}, {"long": "--new"}]},
                 "pcb export foo": {"options": []}}
        with pytest.warns(UserWarning, match="10.0.5"):
            _check_spec(older, newer, "10.1.0")
        with pytest.raises(AssertionError, match="dropped"):
            _check_spec(older, {"pcb export svg": {"options": []}}, "10.1.0")

    @pytest.mark.smoke
    def test_show_command_forwards_every_option(self, live_tree, tmp_dir):
        from kicad_cli_extended.core.kicad_cli_cmds import command_path
        mod = _cli_module()
        runner = CliRunner()
        for kpath, info in sorted(live_tree.items()):
            args = ["--json"] + command_path(kpath)
            for o in info["options"]:
                if o["takes_value"]:
                    val = os.path.join(tmp_dir, "out") if o["long"] == "--output" else "VAL"
                    args += [o["long"], val] + ([o["long"], "VAL2"] if o["repeatable"] else [])
                else:
                    args.append(o["long"])
            if info["positionals"]:
                args.append(os.path.join(tmp_dir, "input.file"))
            args.append("--show-command")
            res = runner.invoke(mod.cli, args, catch_exceptions=False)
            assert res.exit_code == 0, f"{kpath}: {res.output}"
            argv = json.loads(res.output)["command"]
            assert argv[len(kb.kicad_cli_command()):len(kb.kicad_cli_command()) + len(kpath.split())] == kpath.split()
            for o in info["options"]:
                assert o["long"] in argv, f"{kpath}: {o['long']} not forwarded"
                if o["repeatable"]:
                    assert argv.count(o["long"]) == 2, f"{kpath}: repeatable {o['long']} not repeated"


# ═════════════════════════════════════════════════════════════════════════════
# Native format fidelity
# ═════════════════════════════════════════════════════════════════════════════

class TestFormatFidelity:
    @pytest.mark.smoke
    def test_demo_files_roundtrip_byte_identical(self):
        n = 0
        bad, malformed = [], []
        for ext in ("kicad_sch", "kicad_pcb", "kicad_sym", "kicad_mod"):
            for f in glob.glob(os.path.join(demos_dir(), "**", f"*.{ext}"), recursive=True):
                if os.path.getsize(f) > 20_000_000:
                    continue
                with open(f, encoding="utf-8", newline="") as fh:  # no newline translation: true byte check
                    txt = fh.read()
                head = txt[:400]
                if '(generator_version "9.0")' not in head and '(generator_version "10.0")' not in head:
                    continue
                if "\t(" not in txt[:300]:  # not KiCad-formatted (hand edited)
                    continue
                n += 1
                try:
                    tree = sexpr.loads(txt)
                except sexpr.SexprError:
                    malformed.append(f)  # e.g. a demo .kicad_mod with two root lists
                    continue
                if sexpr.dumps(tree, sexpr.mode_for_path(f)) != txt:
                    bad.append(f)
        print(f"\n  round-tripped {n} KiCad 9/10 files; mismatches: {len(bad)}; malformed: {len(malformed)}")
        assert len(malformed) <= 1, malformed
        assert n >= 15  # the CI fixture subset has ~30 KiCad 9/10 files, a full demos checkout ~340
        # one known hand-edited board in the demos (no whitespace between lists) is allowed
        assert len(bad) <= 1, bad


# ═════════════════════════════════════════════════════════════════════════════
# Project creation & validation
# ═════════════════════════════════════════════════════════════════════════════

class TestProjectCreation:
    @pytest.mark.smoke
    def test_new_default_project_loads_in_kicad(self, tmp_dir):
        d = rj(["project", "new", os.path.join(tmp_dir, "p1"), "--name", "p1"])
        sch, pcb = d["project"]["sch"], d["project"]["pcb"]
        assert read_text(sch).startswith("(kicad_sch") and read_text(pcb).startswith("(kicad_pcb")
        kb.run_kicad_cli(["sch", "export", "netlist", "-o", os.path.join(tmp_dir, "n.net"), sch])
        st = os.path.join(tmp_dir, "s.json")
        kb.run_kicad_cli(["pcb", "export", "stats", "--format", "json", "-o", st, pcb])
        assert load_json(st)["metadata"]["generator"].startswith("KiCad 10")
        info = rj(["--project", d["project"]["pro"], "project", "info"])
        assert info["kicad_version"].startswith("10.") and info["schematic"]["symbols"] == 0

    def test_new_from_template(self, tmp_dir):
        d = rj(["project", "new", os.path.join(tmp_dir, "uno"), "--name", "uno", "--template", "Arduino_Uno"])
        info = rj(["--project", d["project"]["pro"], "pcb", "info"])
        assert info["outline_bbox"]["width"] > 50 and info["footprints"] >= 1
        print(f"\n  Arduino_Uno template board: {info['outline_bbox']}")

    @pytest.mark.smoke
    def test_templates_listed(self):
        names = [t["name"] for t in rj(["project", "templates"])]
        assert "default" in names and "Arduino_Uno" in names

    def test_broken_edit_is_rejected_and_rolled_back(self, blink):
        sch = os.path.join(blink, "blink.kicad_sch")
        before = read_text(sch)
        # corrupt the lib_id of a placed symbol so KiCad cannot parse it
        r = run(["--json", "--project", blink, "raw", "set", "blink.kicad_sch", "symbol[0]/lib_id", "bad:id:x", "--quoted"],
                check=False)
        assert r.returncode == 4  # validation: KiCad refused the saved file
        err = json.loads(r.stderr.strip().splitlines()[-1])
        assert err["category"] == "validation" and err["type"] == "ValidationError"
        assert "rolled back" in err["error"] and "kicad-cli" in err["error"]
        assert read_text(sch) == before

    def test_textvars_netclass_rules_roundtrip_through_kicad(self, blink):
        P = ["--project", blink]
        run(P + ["project", "textvar", "set", "REV", "C"])
        run(P + ["project", "netclass", "set", "POWER", "--track-width", "0.6", "--clearance", "0.25"])
        run(P + ["project", "netclass", "assign", "/VCC", "POWER"])
        run(P + ["project", "rules", "set", "min_track_width=0.15", "min_clearance=0.15"])
        run(P + ["project", "rules", "custom-add", "edge", "-c", "edge_clearance min 0.3mm"])
        nets = {n["name"]: n["netclass"] for n in rj(P + ["pcb", "nets"])}
        assert nets["/VCC"] == "POWER" and nets["/GND"] == "Default"
        d = rj(P + ["check", "drc"])
        assert d["errors"] == 0, d["by_type"]


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 1: from-scratch design
# ═════════════════════════════════════════════════════════════════════════════

class TestScratchWorkflow:
    def test_replacement_preserves_no_connect_decisions(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "nc-swap"), "--name", "nc-swap"])["project"]["pro"]
        P = ["--project", p]
        rj(P + ["sch", "add", "Device:R", "--ref", "R1", "--at", "50,50"])
        for pin in ("R1:1", "R1:2"):
            rj(P + ["sch", "no-connect", pin])
        denied = run(["--json", *P, "sch", "replace-symbol", "R1", "Device:R_Small"], check=False)
        assert denied.returncode == 3 and json.loads(denied.stderr)["type"] == "NetSplitError"
        done = rj(P + ["sch", "replace-symbol", "R1", "Device:R_Small", "--rewire"])
        assert done["connections_lost"] == []
        assert rj(P + ["check", "erc"])["clean"]

    def test_symbol_replacement_preserves_net_partitions(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "replace"), "--name", "replace"])["project"]["pro"]
        P = ["--project", p]
        rj(P + ["sch", "add", "Regulator_Linear:LM7805_TO220", "--ref", "U3", "--at", "50,50"])
        for pin, net in (("1", "VIN"), ("2", "GND"), ("3", "VOUT")):
            rj(P + ["sch", "label-pin", "U3:" + pin, net])
        root = Path(p).parent
        before = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file() and f.name != "lock"}
        nets = rj(P + ["sch", "nets"])
        denied = run(["--json", "--dry-run", *P, "sch", "replace-symbol", "U3", "Regulator_Linear:LM1117T-5.0"], check=False)
        assert denied.returncode == 3
        err = json.loads(denied.stderr)
        assert err["type"] == "NetSplitError" and err["category"] == "design"
        assert err["details"]["connections_lost"]
        after = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file() and f.name != "lock"}
        assert before == after
        swapped = rj(P + ["sch", "replace-symbol", "U3", "Regulator_Linear:LM1117T-5.0", "--map-by", "number", "--rewire"])
        assert swapped["pin_map"] == {"1": "1", "2": "2", "3": "3"}
        assert swapped["connections_lost"] == []
        current = rj(P + ["sch", "nets"])
        assert [(n["name"], [(p["ref"], p["pin"]) for p in n["pins"]]) for n in nets["nets"]] == [(n["name"], [(p["ref"], p["pin"]) for p in n["pins"]]) for n in current["nets"]]

    def test_no_connect_all_unconnected_matches_erc(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "nc"), "--name", "nc"])["project"]["pro"]
        P = ["--project", p]
        rj(P + ["sch", "add", "Connector_Generic:Conn_01x06", "--ref", "J1", "--at", "50,50"])
        rj(P + ["sch", "add", "Device:R", "--ref", "R1", "--at", "80,50"])
        run(P + ["sch", "no-connect", "J1:1"])
        run(P + ["sch", "no-connect", "R1:1"])
        run(P + ["sch", "no-connect", "R1:2"])
        erc = rj(P + ["check", "erc"])
        assert erc["by_type"] == {"pin_not_connected": 5}, erc["by_type"]
        assert sorted(v["fix"] for v in erc["violations"]) == [f"kce sch no-connect J1:{n}" for n in range(2, 7)]
        dry = rj(["--dry-run", *P, "sch", "no-connect", "--all-unconnected", "--ref", "J1"])
        assert dry["dry_run"] is True and dry["count"] == 5
        assert [a["pin"] for a in dry["added"]] == [f"J1:{n}" for n in range(2, 7)]
        assert rj(P + ["check", "erc"])["by_type"] == {"pin_not_connected": 5}  # dry-run wrote nothing
        real = rj(P + ["sch", "no-connect", "--all-unconnected", "--ref", "J1"])
        assert real["count"] == 5 and real["dry_run"] is False
        assert rj(P + ["check", "erc"])["clean"] is True
        assert rj(P + ["sch", "no-connect", "--all-unconnected", "--ref", "J1"])["count"] == 0

    def test_erc_clean_and_nets(self, blink):
        e = rj(["--project", blink, "check", "erc"])
        assert e["errors"] == 0 and e["warnings"] == 0, e["by_type"]
        nets = {n["name"]: sorted(n["nodes"]) for n in rj(["--project", blink, "sch", "nets"])["nets"]}
        assert nets["/THR"] == ["C1:1", "R2:2", "U1:2(TRIG_2)", "U1:6(THRES_6)"]
        assert set(nets["/VCC"]) == {"J1:1(Pin_1_1)", "R1:1", "U1:4(~{RST}_4)", "U1:8(VCC_8)"}
        assert sorted(nets["Net-(D1-A)"]) == ["D1:2(A_2)", "R3:2"]
        assert len(nets) == 7

    def test_board_complete_and_drc_clean(self, blink):
        info = rj(["--project", blink, "pcb", "info"])
        assert info["footprints"] == 8 and info["unrouted"] == 0 and info["zones"] == 1
        d = rj(["--project", blink, "check", "drc", "--output", os.path.join(blink, "drc.json")])
        assert d["errors"] == 0 and d["warnings"] == 0 and d["unconnected"] == 0 and d["parity"] == 0, d["by_type"]
        rep = load_json(os.path.join(blink, "drc.json"))
        assert rep["kicad_version"].startswith("10.") and rep["violations"] == []
        artifact(os.path.join(blink, "drc.json"), "DRC")

    def test_gerbers_x2_and_drill(self, blink):
        out = os.path.join(blink, "g")
        r = rj(["--project", blink, "pcb", "export", "gerbers", "--output", out + os.sep])
        gbr = [f for f in r["outputs"] if f["format"] == "gerber"]
        assert len(gbr) >= 9
        funcs = {f.get("function", "") for f in gbr}
        assert any(fn.startswith("Copper,L1,Top") for fn in funcs) and any(fn.startswith("Copper,L2,Bot") for fn in funcs)
        assert "Profile,NP" in funcs
        from kicad_cli_extended.utils.verify import gerber_info
        assert all(gerber_info(opath(r, f))["GenerationSoftware"].startswith("KiCad,Pcbnew,10.") for f in gbr)
        top = next(opath(r, f) for f in gbr if f["function"].startswith("Copper,L1"))
        assert "%TO.N," in read_text(top)  # X2 net attributes
        artifact(top, "Gerber F.Cu")
        r = rj(["--project", blink, "pcb", "export", "drill", "--output", out + os.sep, "--generate-map",
                "--map-format", "pdf"])
        drl = [f for f in r["outputs"] if f["format"] == "excellon"]
        assert drl and "T1C" in read_text(opath(r, drl[0])).replace(" ", "")
        assert any(f["format"] == "pdf" for f in r["outputs"])
        artifact(opath(r, drl[0]), "Excellon")

    def test_fab_package_jlcpcb(self, blink):
        r = rj(["--project", blink, "fab", "package", "--preset", "jlcpcb"])
        assert r["drc"]["clean"]
        z = zipfile.ZipFile(opath(r, r["zip"]))
        names = z.namelist()
        assert any(n.endswith(".gtl") for n in names) and any(n.endswith(".gm1") for n in names)
        assert any(n.endswith(".drl") for n in names)
        gtl = z.read(next(n for n in names if n.endswith(".gtl"))).decode()
        assert "%TF." not in gtl  # JLC preset: no X2 attributes
        cpl = read_csv(opath(r, r["cpl"]))
        assert cpl[0] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"] and len(cpl) == 9
        bom = (read_csv(os.path.join(r["output_dir"], "blink-bom.csv")))
        assert bom[0] == ["Comment", "Designator", "Footprint", "LCSC Part #"]
        assert any(row[0] == "NE555P" and row[1] == "U1" for row in bom)
        z.close()
        artifact(opath(r, r["zip"]), "JLCPCB zip")

    def test_fab_package_generic(self, blink):
        r = rj(["--project", blink, "fab", "package", "--preset", "generic"])
        fmts = {os.path.basename(f["path"]): f["format"] for f in r["files"]}
        assert fmts["blink.step"] == "step" and fmts["blink.net"] in ("kicad-netlist", "kicad-sexpr")
        assert fmts["blink.d356"] == "ipc-d-356"
        assert read_text(os.path.join(r["output_dir"], "blink.step"))[:12] == "ISO-10303-21"
        assert r["summary"]["drill_files"] == 2  # separate PTH / NPTH
        artifact(os.path.join(r["output_dir"], "blink.step"), "STEP")

    EXPORTS = [
        (["pcb", "export", "step"], "step"), (["pcb", "export", "stpz"], "gzip"), (["pcb", "export", "glb"], "glb"),
        (["pcb", "export", "stl"], "stl"), (["pcb", "export", "ply"], "ply"), (["pcb", "export", "vrml"], "vrml"),
        (["pcb", "export", "brep"], "brep"), (["pcb", "export", "xao"], None), (["pcb", "export", "u3d"], "u3d"),
        (["pcb", "export", "3dpdf"], "pdf"), (["pcb", "export", "pdf", "--layers", "F.Cu,Edge.Cuts"], "pdf"),
        (["pcb", "export", "svg", "--layers", "F.Cu,Edge.Cuts"], "svg"),
        (["pcb", "export", "dxf", "--layers", "Edge.Cuts"], "dxf"),
        (["pcb", "export", "ps", "--layers", "F.Cu"], "postscript"), (["pcb", "export", "ipc2581"], "xml"),
        (["pcb", "export", "odb"], "zip"), (["pcb", "export", "ipcd356"], "ipc-d-356"),
        (["pcb", "export", "gencad"], "gencad"), (["pcb", "export", "pos", "--format", "csv"], "csv"),
        (["pcb", "export", "stats", "--format", "json"], "json"),
        (["sch", "export", "pdf"], "pdf"), (["sch", "export", "svg"], "svg"), (["sch", "export", "dxf"], "dxf"),
        (["sch", "export", "ps"], "postscript"), (["sch", "export", "netlist"], "kicad-netlist"),
        (["sch", "export", "netlist", "--format", "spice"], "text"), (["sch", "export", "bom"], "csv"),
        (["sch", "export", "python-bom"], "xml"),
    ]

    @pytest.mark.parametrize("args,fmt", EXPORTS, ids=[" ".join(a[:3]) + ("+" + a[3] if len(a) > 3 else "")
                                                         for a, _ in EXPORTS])
    def test_export(self, blink_master, tmp_dir, args, fmt):
        out = os.path.join(tmp_dir, "x")
        os.makedirs(out)
        from kicad_cli_extended.core.kicad_cli_cmds import OUT_EXT, OUT_EXT_EXTRA, load_spec, output_is_dir
        kpath = " ".join(args[:3]) if args[1] == "export" else " ".join(args[:2])
        is_dir = output_is_dir(load_spec()["commands"][kpath], kpath)
        ext = OUT_EXT.get(kpath) or OUT_EXT_EXTRA.get(kpath, "")
        if kpath == "pcb export pos":
            ext = ".csv"
        target = out + os.sep if is_dir else os.path.join(out, "file" + ext)
        r = rj(["--project", blink_master, *args, "--output", target])
        assert r["returncode"] == 0, r["stderr"]
        files = [f for f in r["outputs"] if f["bytes"] > 0]
        assert files, f"no output for {args}"
        if fmt:
            assert any(f["format"] == fmt for f in files), [(f["path"], f["format"]) for f in files]
        assert all(not os.path.isabs(f["path"]) and os.path.exists(opath(r, f)) for f in files)
        main = max(files, key=lambda f: f["bytes"])
        if fmt == "csv" and args[1] == "export" and args[2] == "bom":
            rows = read_csv(opath(r, main))
            assert rows[0][:3] == ["Refs", "Value", "Footprint"] and any("NE555P" in r_ for r_ in rows)
        if fmt == "json":
            assert load_json(opath(r, main))["board"]["has_outline"] is True
        artifact(opath(r, main), " ".join(args[:3]))

    def test_render_png(self, blink_master, tmp_dir):
        out = os.path.join(tmp_dir, "r.png")
        r = rj(["--project", blink_master, "pcb", "render", "--output", out, "--side", "top", "--width", "800",
                "--height", "600", "--quality", "basic"])
        f = r["outputs"][0]
        assert f["format"] == "png" and f["width"] > 700 and f["height"] > 500
        with open(out, "rb") as fh:
            assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
        artifact(out, "3D render")

    def test_erc_drc_passthrough_json(self, blink_master, tmp_dir):
        r = rj(["--project", blink_master, "sch", "erc", "--output", os.path.join(tmp_dir, "e.json")])
        assert r["summary"]["clean"] is True
        rep = load_json(os.path.join(tmp_dir, "e.json"))
        assert rep["$schema"].endswith("erc.v1.json")
        r = rj(["--project", blink_master, "pcb", "drc", "--schematic-parity", "--output", os.path.join(tmp_dir, "d.json")])
        assert r["summary"]["errors"] == 0

    def test_preview_bundles(self, blink):
        P = ["--project", blink]
        c = rj(P + ["preview", "capture", "--recipe", "quick"])
        assert c["status"] == "ok" and not c["cached"]
        m = load_json(c["manifest"])
        assert m["schema"].endswith(".preview-bundle/1") and m["tool"]["name"] == "kicad-cli-extended"
        assert m["source"]["fingerprint"].startswith("sha256:")
        roles = {a["role"] for a in m["artifacts"]}
        assert "hero" in roles
        for a in m["artifacts"]:
            p = os.path.join(c["bundle_dir"], a["path"])
            assert os.path.getsize(p) > 100
            if a["media_type"] == "image/png":
                with open(p, "rb") as fh:
                    assert fh.read(4) == b"\x89PNG"
            else:
                assert "<svg" in read_text(p)[:2000]
        assert rj(P + ["preview", "capture", "--recipe", "quick"])["cached"] is True
        assert rj(P + ["preview", "latest", "--recipe", "quick"])["bundle_id"] == c["bundle_id"]
        c3 = rj(P + ["preview", "capture", "--recipe", "3d", "--width", "640", "--height", "360"])
        assert {a["view"] for a in c3["artifacts"]} == {"iso", "top", "bottom"}
        s = rj(P + ["preview", "live", "start", "--recipe", "board"])
        assert s["active"] and s["history"]["steps"] == 1
        run(P + ["pcb", "move", "R3", "--by", "1,0"])
        s = rj(P + ["preview", "live", "push", "--recipe", "board", "--stage", "moved R3"])
        assert s["history"]["steps"] == 2 and s["latest_reason"] == "push"
        assert s["current"]["bundle_id"] != c["bundle_id"]
        assert rj(P + ["preview", "live", "stop", "--recipe", "board"])["status"] == "stopped"
        assert os.path.isdir(os.path.join(blink, STATE_DIR, "previews", "quick"))

    def test_jobset_fab_template_runs(self, blink):
        P = ["--project", blink]
        js = rj(P + ["jobset", "new", "--fab-template"])
        assert len(js["jobs"]) == 9
        r = rj(P + ["jobset", "execute"])
        assert r["returncode"] == 0 and r["failed_jobs"] == 0, r["stdout"][-1500:]
        files = r["outputs"][0]["files"]
        assert files and all(os.path.exists(os.path.join(r["outputs"][0]["path"], f["path"])) for f in files)
        fmts = {f["format"] for f in files}
        assert {"gerber", "excellon", "pdf", "step", "png", "json", "csv"} <= fmts, fmts
        assert r["outputs"][1]["files"][0]["format"] == "zip"
        sh = rj(P + ["jobset", "run", "--show-command"])
        assert "jobset" in sh["command"] and "--file" in sh["command"]


# ═════════════════════════════════════════════════════════════════════════════
# Official KiCad demos
# ═════════════════════════════════════════════════════════════════════════════

class TestDemos:
    @pytest.mark.smoke
    def test_pic_programmer_erc_drc_and_update_noop(self, tmp_dir):
        d = demo_copy("pic_programmer", tmp_dir)
        P = ["--project", d]
        e = rj(P + ["check", "erc"])
        assert e["kicad_version"].startswith("10.") and e["total"] == e["errors"] + e["warnings"]
        drc = rj(P + ["check", "drc", "--no-refill"])
        assert drc["errors"] == 0 and drc["unconnected"] == 0, drc["by_type"]
        before = read_text(os.path.join(d, "pic_programmer.kicad_pcb"))
        u = rj(P + ["pcb", "update"])
        assert u["added"] == [] and u["removed"] == [] and u["updated"] == [] and u["nets_assigned"] == 0
        assert u["components"] == 63
        drc2 = rj(P + ["check", "drc", "--no-refill"])
        assert drc2["parity"] == 0 and drc2["errors"] == 0
        print(f"\n  pic_programmer: board {len(before):,} bytes, ERC {e['total']} findings, DRC clean")

    def test_video_hierarchy_and_netlist(self, tmp_dir):
        d = demo_copy("video", tmp_dir)
        h = rj(["--project", d, "sch", "hierarchy"])
        assert len(h) == 8 and h[0]["name"] == "/"
        nets = rj(["--project", d, "sch", "nets"])
        assert nets["components"] > 150 and nets["net_count"] > 300
        u = rj(["--project", d, "pcb", "update"])
        assert not u["added"] and not u["removed"] and u["nets_assigned"] == 0

    def test_complex_hierarchy_annotation_multi_instance(self, tmp_dir):
        d = demo_copy("complex_hierarchy", tmp_dir)
        P = ["--project", d]
        refs_before = sorted(c["ref"] for c in rj(P + ["sch", "components"]))
        run(P + ["sch", "annotate", "--clear"])
        assert all(c["ref"].endswith("?") for c in rj(P + ["sch", "components"]) if not c["ref"].startswith("#"))
        r = rj(P + ["sch", "annotate", "--mode", "all"])
        refs = [c["ref"] for c in rj(P + ["sch", "components"])]
        assert len(refs) == len(set(refs)) == len(refs_before) and not any(x.endswith("?") for x in refs)
        print(f"\n  complex_hierarchy: {r['count']} references assigned across multi-instance sheets")

    @pytest.mark.smoke
    def test_pic_programmer_fab_outputs(self, tmp_dir):
        d = demo_copy("pic_programmer", tmp_dir)
        P = ["--project", d]
        g = rj(P + ["pcb", "export", "gerbers", "--output", os.path.join(tmp_dir, "g") + os.sep])
        gbr = [f for f in g["outputs"] if f["format"] == "gerber"]
        assert any(f.get("function", "").startswith("Copper,L1,Top") for f in gbr)
        dr = rj(P + ["pcb", "export", "drill", "--output", os.path.join(tmp_dir, "g") + os.sep])
        assert any(f["format"] == "excellon" for f in dr["outputs"])
        st = rj(P + ["pcb", "export", "step", "--output", os.path.join(tmp_dir, "b.step")])
        assert st["outputs"][0]["format"] == "step"
        bom = rj(P + ["sch", "export", "bom", "--output", os.path.join(tmp_dir, "bom.csv")])
        rows = read_csv(opath(bom, bom["outputs"][0]))
        assert rows[0][:3] == ["Refs", "Value", "Footprint"] and len(rows) > 10
        print(f"\n  pic_programmer: {len(gbr)} gerbers, drill, STEP {st['outputs'][0]['bytes']:,} bytes, "
              f"BOM {len(rows) - 1} lines")

    @pytest.mark.smoke
    def test_interf_u_exports(self, tmp_dir):
        d = demo_copy("interf_u", tmp_dir)
        r = rj(["--project", d, "sch", "export", "pdf"])
        pdf = opath(r, r["outputs"][0])
        assert open(pdf, "rb").read(5) == b"%PDF-"
        r = rj(["--project", d, "pcb", "export", "svg", "--layers", "F.Cu,B.Cu,Edge.Cuts", "--mode-single",
                "--output", os.path.join(tmp_dir, "board.svg")])
        assert r["outputs"][0]["format"] == "svg"
        artifact(pdf, "interf_u schematic PDF")


# ═════════════════════════════════════════════════════════════════════════════
# Libraries
# ═════════════════════════════════════════════════════════════════════════════

class TestLibraries:
    @pytest.mark.smoke
    def test_index_search_show_suggest(self):
        st = rj(["lib", "index"])
        assert st["symbols"] > 20000 and st["footprints"] > 15000
        assert st["sym"]["libraries"] >= 200 and st["fp"]["libraries"] >= 150
        hits = rj(["lib", "sym", "search", "555", "timer", "--pins", "8"])
        assert any(h["lib_id"] == "Timer:NE555P" for h in hits)
        info = rj(["lib", "sym", "show", "Timer:NE555P"])
        assert {p["name"] for p in info["pins"]} >= {"VCC", "GND", "TRIG", "THRES", "OUT"}
        fps = rj(["lib", "fp", "search", "SOIC-8", "--pads", "8", "--type", "smd"])
        assert any(f["fpid"] == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" for f in fps)
        fp = rj(["lib", "fp", "show", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"])
        assert fp["pad_count"] == 8 and fp["models"] and fp["courtyard"]["size"][0] > 5
        sug = rj(["lib", "suggest-footprints", "Device:R", "-q", "0603"])
        assert any(f["fpid"] == "Resistor_SMD:R_0603_1608Metric" for f in sug["footprints"])

    def test_imperial_footprint_size(self):
        hits = rj(["lib", "suggest-footprints", "Device:R", "--size", "0603"])["footprints"]
        assert hits[0]["fpid"] == "Resistor_SMD:R_0603_1608Metric"
        assert all(h["imperial_size"] == "0603" and h["metric_code"] == "1608" for h in hits)
        broad = rj(["lib", "suggest-footprints", "Device:R", "-q", "0603"])["footprints"]
        ids = [h["fpid"] for h in broad]
        assert ids.index("Resistor_SMD:R_0603_1608Metric") < ids.index("Resistor_SMD:R_0201_0603Metric")
        assert next(h for h in broad if h["fpid"].endswith(":R_0201_0603Metric"))["note"]

    def test_normalized_search_and_empty_hint(self):
        usb = rj(["lib", "sym", "search", "usb-c", "receptacle", "--limit", "8"])
        assert any(h["lib_id"] == "Connector:USB_C_Receptacle_USB2.0_16P" for h in usb)
        assert all(isinstance(h["fp_filters"], list) for h in usb)
        nfc = rj(["lib", "sym", "search", "nfc", "eeprom", "--pins", "8+ep"])
        assert any(h["lib_id"] == "RF_NFC:ST25DV04K-IER8C3" for h in nfc)
        dfn = rj(["lib", "fp", "search", "DFN", "2x2", "--pads", "9", "--type", "smd"])
        assert any("TDFN-8-1EP_2x2mm" in h["fpid"] for h in dfn)
        empty = run(["--json", "lib", "sym", "search", "zzzzqq"])
        assert json.loads(empty.stdout) == []
        hint = json.loads(empty.stderr)
        assert hint["warning"] == "no_results" and hint["relaxations_tried"] and hint["hint"]
        explained = rj(["lib", "sym", "search", "usb-c", "--explain", "--limit", "1"])
        assert explained["results"] and explained["score_parts"]["terms"] == ["usb", "c"]

    def test_create_symbol_and_footprint_libraries(self, blink):
        P = ["--project", blink]
        r = rj(P + ["lib", "sym", "new", os.path.join(blink, "mylib.kicad_sym"), "MYCHIP",
                    "--pin", "1:VCC:power_in:top,2:GND:power_in:bottom,3:SDA:bidirectional,4:SCL:input,5:INT:output",
                    "--description", "Test chip", "--add-to-table", "mylib"])
        assert r["pins"] == 5
        info = rj(P + ["lib", "sym", "show", "mylib:MYCHIP"])
        assert sorted(p["number"] for p in info["pins"]) == ["1", "2", "3", "4", "5"]
        out = os.path.join(blink, "svg")
        sv = rj(P + ["lib", "sym", "export", "svg", os.path.join(blink, "mylib.kicad_sym"), "--output", out + os.sep])
        assert sv["outputs"] and sv["outputs"][0]["format"] == "svg"
        f = rj(P + ["lib", "fp", "new", os.path.join(blink, "my.pretty"), "SOIC8_TEST", "--kind", "soic",
                    "--pads", "8", "--pitch", "1.27", "--pad-size", "1.5,0.6", "--add-to-table", "my"])
        assert f["pads"] == 8
        fi = rj(P + ["lib", "fp", "show", "my:SOIC8_TEST"])
        assert fi["pad_count"] == 8 and fi["attr"] == ["smd"]
        fv = rj(P + ["lib", "fp", "export", "svg", os.path.join(blink, "my.pretty"), "--output",
                     os.path.join(blink, "fpsvg") + os.sep])
        assert fv["outputs"][0]["format"] == "svg"
        # place the new symbol + footprint in the design
        run(P + ["sch", "add", "mylib:MYCHIP", "--ref", "U2", "--at", "150,60", "--footprint", "my:SOIC8_TEST"])
        assert rj(P + ["sch", "show", "U2"])["footprint"] == "my:SOIC8_TEST"

    def test_footprint_wizard_qfn(self, blink):
        params = rj(["--project", blink, "lib", "fp", "wizard-params", "qfn"])
        assert params["params"]
        r = rj(["--project", blink, "lib", "fp", "wizard", "qfn", os.path.join(blink, "wiz.pretty")])
        assert r["pads"] > 4
        mods = glob.glob(os.path.join(blink, "wiz.pretty", "*.kicad_mod"))
        assert mods and sexpr.load(mods[0]).tag == "footprint"
        artifact(mods[0], "QFN wizard footprint")

    def test_library_check_and_link_check_catch_pin_map_bugs(self, blink):
        P = ["--project", blink]
        rj(P + ["lib", "fp", "new", os.path.join(blink, "parts.pretty"), "TDFN", "--kind", "custom",
                "--pad", "1:-0.75,0.5:0.5,0.4", "--pad", "2:0.75,0.5:0.5,0.4", "--pad", "9:0,0:0.7,1.2",
                "--pad", "3:-2.5,-2:0.5,0.4", "--pad", "4:-1.5,-2:0.5,0.4", "--pad", "5:-0.5,-2:0.5,0.4",
                "--pad", "6:0.5,-2:0.5,0.4", "--pad", "7:1.5,-2:0.5,0.4", "--pad", "8:2.5,-2:0.5,0.4",
                "--add-to-table", "partsfp"])
        rj(P + ["lib", "sym", "new", os.path.join(blink, "parts.kicad_sym"), "CHIP",
                "--pin", "1:P1,2:P2,3:P3,4:P4,5:P5,6:P6,7:P7,8:P8,9:P9,10:EP",
                "--footprint", "partsfp:TDFN", "--add-to-table", "parts"])
        chk = rj(P + ["lib", "fp", "check", "partsfp:TDFN", "--rules", "project"])
        assert chk["ok"] is False and chk["required_clearance"] == 0.2
        assert chk["pad_numbers"] == {"signal": [str(n) for n in range(1, 9)], "other": ["9"]}
        assert {(v["a"], v["b"], v["actual"], v["required"]) for v in chk["clearance_violations"]} == {
            ("1", "9", 0.15, 0.2), ("2", "9", 0.15, 0.2)}
        lnk = rj(P + ["lib", "link-check", "parts:CHIP"])
        assert lnk["fpid"] == "partsfp:TDFN" and lnk["ok"] is False
        assert lnk["pins_without_pad"] == ["10"] and lnk["pads_without_pin"] == []
        rj(P + ["sch", "add", "parts:CHIP", "--ref", "U9", "--at", "150,80", "--footprint", "partsfp:TDFN"])
        up = rj(P + ["pcb", "update", "--no-place"])
        assert {"ref": "U9", "pin": "10", "name": "EP"} in up["unmatched_pins"]

    def test_footprint_wizard_guards_metadata_and_history(self, blink):
        P = ["--project", blink]
        args = P + ["lib", "fp", "wizard", "qfn", os.path.join(blink, "wiz2.pretty"), "--name", "SAME",
                    "--description", "100-lead QFN", "--tags", "qfn test", "--add-to-table", "wiz"]
        r = rj(args)
        assert r["overwrote"] is False and r["dry_run"] is False and r["table"]
        info = rj(P + ["lib", "fp", "show", "wiz:SAME"])
        assert info["description"] == "100-lead QFN" and info["tags"] == "qfn test"
        assert info["pad_object_count"] > info["pad_count"] == 101
        paste = info["pads_by_role"]["paste_only"]
        assert paste and set(paste) <= set(info["pads_by_role"]["signal"])
        again = run(["--json", *args], check=False)
        assert again.returncode == 2
        err = json.loads(again.stderr)
        assert err["type"] == "FileExistsError" and err["category"] == "input"
        assert "exists" in err["error"] and "--overwrite" in err["error"]
        r2 = rj(args + ["--overwrite"])
        assert r2["overwrote"] is True
        hist = rj(P + ["session", "history"])
        assert any(h["description"] == "new footprint SAME" for h in hist["undo"])

    def test_library_model_travels_with_the_project(self, blink):
        P = ["--project", blink]
        step = Path(blink, "vendor", "BODY.step")
        step.parent.mkdir()
        step.write_text("ISO-10303-21;", encoding="utf-8")
        rj(P + ["lib", "fp", "new", os.path.join(blink, "mod.pretty"), "BODY", "--kind", "chip",
                "--pads", "2", "--pitch", "1.6", "--pad-size", "0.9,1.0", "--add-to-table", "mod"])
        rj(P + ["sch", "set-footprint", "R1", "mod:BODY"])
        rj(P + ["pcb", "update"])
        r = rj(P + ["lib", "fp", "model", "set", "mod:BODY", str(step), "--copy-into", "3d",
                    "--offset", "0,0,1.5", "--rotation", "0,0,90"])
        assert r["models"] == [{"file": "${KIPRJMOD}/3d/BODY.step",
                                "resolved_path": os.path.join(blink, "3d", "BODY.step"), "exists": True,
                                "offset": [0.0, 0.0, 1.5], "rotation": [0.0, 0.0, 90.0],
                                "scale": [1.0, 1.0, 1.0]}]
        assert rj(P + ["lib", "fp", "model", "list", "mod:BODY"])["models"] == r["models"]
        rj(P + ["pcb", "update-footprints", "R1", "--force", "--reset-models"])
        r1 = next(m for m in rj(P + ["pcb", "models"])["footprints"] if m["reference"] == "R1")
        assert [m["file"] for m in r1["models"]] == ["${KIPRJMOD}/3d/BODY.step"]
        rr = rj(P + ["lib", "fp", "model", "remove", "mod:BODY"])
        assert rr["removed"] == "${KIPRJMOD}/3d/BODY.step" and rr["models"] == []
        rj(P + ["pcb", "update-footprints", "R1", "--force", "--reset-models"])
        r1 = next(m for m in rj(P + ["pcb", "models"])["footprints"] if m["reference"] == "R1")
        assert r1["models"] == []


# ═════════════════════════════════════════════════════════════════════════════
# Board operations (pcbnew)
# ═════════════════════════════════════════════════════════════════════════════

class TestBoardOps:
    def test_place_move_rotate_flip_lock(self, blink):
        P = ["--project", blink]
        r = rj(P + ["pcb", "move", "R1", "--at", "120,120", "--rotation", "90"])
        assert (r["x"], r["y"], r["rotation"]) == (120.0, 120.0, 90.0)
        assert rj(P + ["pcb", "rotate", "R1", "--by", "90"])["rotation"] in (180.0, -180.0)
        assert rj(P + ["pcb", "flip", "R1"])["side"] == "bottom"
        assert rj(P + ["pcb", "flip", "R1", "--side", "top"])["side"] == "top"
        run(P + ["pcb", "lock", "R1"])
        r = run(["--json", *P, "pcb", "move", "R1", "--by", "1,0"], check=False)
        assert r.returncode == 2 and "locked" in r.stderr  # input: a locked item is the caller's to unlock
        run(P + ["pcb", "lock", "R1", "--unlock"])
        fp = rj(P + ["pcb", "footprint", "R1"])
        assert not fp["locked"] and {p["net"] for p in fp["pads"]} == {"/VCC", "/DIS"}

    def test_ripup_ratsnest_route(self, blink):
        P = ["--project", blink]
        run(P + ["pcb", "delete-tracks", "--net", "/OUT"])
        rn = rj(P + ["pcb", "ratsnest", "--net", "/OUT"])
        assert len(rn) == 1 and {rn[0]["from"], rn[0]["to"]} == {"U1:3", "R3:1"}
        con = rj(P + ["pcb", "connectivity"])
        assert con["unrouted_connections"] >= 1
        run(P + ["pcb", "autoroute", "--net", "/OUT"])
        assert rj(P + ["pcb", "connectivity"])["unrouted_connections"] == 0

    def test_tracks_vias_zones_outline(self, blink):
        P = ["--project", blink]
        run(P + ["pcb", "track", "102,128;110,128", "--layer", "B.Cu", "--width", "0.3", "--net", "/GND"])
        run(P + ["pcb", "via", "--at", "110,128", "--net", "/GND"])
        assert any(t["type"] == "via" and t["net"] == "/GND" for t in rj(P + ["pcb", "tracks", "--net", "/GND"]))
        run(P + ["pcb", "zone", "keepout", "--rect", "135,125,4,4"])
        zones = rj(P + ["pcb", "zones"])
        assert any(z["rule_area"] for z in zones) and any(z["net"] == "/GND" and z["filled"] for z in zones)
        run(P + ["project", "textvar", "set", "REV", "B"])
        run(P + ["pcb", "text", "REV ${REV}", "--at", "112,127", "--layer", "B.SilkS"])
        run(P + ["pcb", "graphic", "rect", "--start", "101,124", "--end", "104,127", "--layer", "F.SilkS"])
        assert len(rj(P + ["pcb", "drawings", "--layer", "F.SilkS"])) >= 1
        d = rj(P + ["check", "drc"])
        assert d["errors"] == 0, d["by_type"]

    def test_outline_circle_and_layers(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "c"), "--name", "c"])["project"]["pro"]
        P = ["--project", p]
        run(P + ["pcb", "outline", "circle", "--center", "50,50", "--radius", "20"])
        info = rj(P + ["pcb", "info"])
        assert abs(info["outline_bbox"]["width"] - 40) < 0.2
        run(P + ["pcb", "set-layers", "4"])
        assert rj(P + ["pcb", "info"])["copper_layers"] == 4
        assert len([l for l in rj(P + ["pcb", "layers"]) if l["copper"]]) == 4

    def test_dsn_export_and_raw_pcbnew(self, blink, tmp_dir):
        P = ["--project", blink]
        out = os.path.join(tmp_dir, "b.dsn")
        r = rj(P + ["pcb", "dsn", "--output", out])
        txt = read_text(out)
        assert r["bytes"] > 1000 and txt.lstrip().startswith("(pcb") and "(network" in txt
        artifact(out, "Specctra DSN")
        q = rj(P + ["raw", "pcbnew", "--readonly", "-c",
                    "result = sorted(f.GetReference() for f in board.GetFootprints())"])
        assert q["result"] == ["C1", "C2", "D1", "J1", "R1", "R2", "R3", "U1"]
        run(P + ["raw", "pcbnew", "-c", "board.FindFootprintByReference('C1').SetValue('22u')"])
        assert rj(P + ["pcb", "footprint", "C1"])["value"] == "22u"


# ═════════════════════════════════════════════════════════════════════════════
# Scenarios 2-5
# ═════════════════════════════════════════════════════════════════════════════

class TestModifyCompareDiagnose:
    def test_eco_dry_run_undo_redo_compare(self, tmp_dir):
        d = demo_copy("pic_programmer", tmp_dir)
        rev0 = os.path.join(tmp_dir, "rev0")
        shutil.copytree(d, rev0)
        P = ["--project", d]
        sch = os.path.join(d, "pic_programmer.kicad_sch")
        orig = read_text(sch)
        run(P + ["--dry-run", "sch", "set-value", "C2", "47uF"])
        assert read_text(sch) == orig
        run(P + ["sch", "set-value", "C2", "47uF"])
        run(P + ["sch", "set-field", "C2", "MPN", "UVR1H470MDD", "--hide"])
        run(P + ["pcb", "update"])
        cmp_ = rj(P + ["project", "compare", rev0])
        ch = {c["ref"]: c["changes"] for c in cmp_["schematic"]["components_changed"]}
        assert ch["C2"]["value"][1] == "47uF" and "field:MPN" in ch["C2"]
        assert cmp_["board"]["footprints_changed"][0]["ref"] == "C2"
        hist = rj(P + ["session", "history"])
        assert [h["description"] for h in hist["undo"][:3]] == ["update PCB from schematic", "C2.MPN=UVR1H470MDD",
                                                                 "C2 value=47uF"]
        rj(P + ["session", "undo", "--steps", "3"])
        assert read_text(sch) == orig
        assert rj(P + ["project", "compare", rev0])["identical"] is True
        rj(P + ["session", "redo"])
        assert "47uF" in read_text(sch)

    def test_compare_git_revisions(self, blink):
        subprocess.run(["git", "init", "-q", blink], check=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "-C", blink, "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", blink, "commit", "-qm", "rev A"], check=True, env=env)
        run(["--project", blink, "pcb", "move", "C1", "--by", "0,3"])
        run(["--project", blink, "sch", "set-value", "R2", "22k"])
        c = rj(["--project", blink, "project", "compare", "git:HEAD"])
        assert c["board"]["footprints_moved"][0]["ref"] == "C1"
        assert c["schematic"]["components_changed"][0]["changes"]["value"] == ["10k", "22k"]
        dfb = rj(["--project", blink, "preview", "diff", "--against", "git:HEAD"])
        m = load_json(dfb["manifest"])
        assert m["kind"] == "diff" and {"before", "after", "diff"} <= {a["role"] for a in m["artifacts"]}

    def test_diagnose_and_fix(self, blink):
        P = ["--project", blink]
        # break things: unconnected pin, undriven power, overlapping footprints
        items = rj(P + ["sch", "items", "--type", "label"])
        out_label = next(i for i in items if i["text"] == "OUT")
        run(P + ["sch", "delete-item", out_label["uuid"]])
        flg = [s for s in rj(P + ["sch", "symbols"]) if s["reference"].startswith("#FLG")]
        run(P + ["sch", "delete", flg[0]["reference"]])
        run(P + ["pcb", "move", "R2", "--at", "121.735,102.945"])  # onto R1
        dg = rj(P + ["check", "diagnose"])
        types = {(p["check"], p["type"]) for p in dg["plan"]}
        assert ("erc", "power_pin_not_driven") in types and ("erc", "label_dangling") in types or \
            ("erc", "pin_not_connected") in types, types
        assert any(c == "drc" for c, _ in types) and not dg["clean"]
        assert all(p["hint"] for p in dg["plan"] if p["type"] in ("power_pin_not_driven", "courtyards_overlap"))
        # apply the hinted fixes
        run(P + ["session", "undo", "--steps", "3"])
        assert rj(P + ["check", "diagnose"])["clean"] is True


class TestHierarchy:
    def test_hierarchical_sheet_connectivity(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "h"), "--name", "h"])["project"]["pro"]
        P = ["--project", p]
        s = rj(P + ["sch", "add-sheet", "Power", "--at", "50.8,50.8", "--size", "25.4,20.32"])
        assert s["created_file"]
        pin = rj(P + ["sch", "sheet-pin", "Power", "VOUT", "--shape", "output"])
        run(P + ["sch", "add", "Device:R", "--ref", "R1", "--at", "30.48,30.48", "--sheet", "Power.kicad_sch"])
        run(P + ["sch", "label-pin", "R1:1", "VOUT", "--kind", "hierarchical", "--sheet", "Power.kicad_sch"])
        run(P + ["sch", "add", "Device:R", "--ref", "R2", "--at", "127,63.5"])
        x, y = pin["at"]
        run(P + ["sch", "wire", f"{x},{y};{x + 5.08},{y}"])
        run(P + ["sch", "label", "VOUT", "--at", f"{x + 5.08},{y}"])
        run(P + ["sch", "label-pin", "R2:1", "VOUT"])
        h = rj(P + ["sch", "hierarchy"])
        assert [e["name"] for e in h] == ["/", "Power"]
        nets = {n["name"]: n["nodes"] for n in rj(P + ["sch", "nets"])["nets"]}
        vout = [k for k, v in nets.items() if "R1:1" in v]
        assert vout and "R2:1" in nets[vout[0]], nets


# ═════════════════════════════════════════════════════════════════════════════
# CLI subprocess contract
# ═════════════════════════════════════════════════════════════════════════════

class TestCLISubprocess:
    @pytest.mark.smoke
    def test_help(self):
        r = run(["--help"])
        for g in ("project", "sch", "pcb", "lib", "fab", "jobset", "preview", "session", "raw", "check", "info",
                  "skill"):
            assert g in r.stdout

    @pytest.mark.smoke
    def test_version(self):
        r = run(["--version"])
        assert r.stdout.strip().startswith(f"{COMMAND} (kicad-cli-extended) ")

    @pytest.mark.smoke
    def test_doctor_json(self):
        d = rj(["info", "doctor"])
        assert d["ok"] and d["supported"] and d["kicad_version"].startswith("10.")
        assert d["pcbnew_version"].startswith("10.")
        assert d["symbol_dir"] and d["footprint_dir"] and d["template_dir"]
        print(f"\n  doctor: kicad-cli via {d['kicad_cli_source']}, pcbnew {d['pcbnew_version']}, "
              f"{d['symbol_libraries']} symbol libs, {d['footprint_libraries']} footprint libs")

    @pytest.mark.smoke
    def test_json_error_contract(self, tmp_dir):
        r = run(["--json", "--project", os.path.join(tmp_dir, "nope.kicad_pro"), "project", "info"], check=False)
        assert r.returncode != 0
        p = rj(["project", "new", os.path.join(tmp_dir, "e"), "--name", "e"])["project"]["pro"]
        r = run(["--json", "--project", p, "sch", "show", "R99"], check=False)
        assert r.returncode == 2  # input: unknown reference
        err = json.loads(r.stderr)
        assert err["type"] == "KeyError" and "R99" in err["error"]
        assert err["hint"] == f"{COMMAND} sch symbols"  # every error names the next command

    @pytest.mark.smoke
    def test_commands_listing(self):
        cmds = rj(["info", "commands"])
        names = {c["command"] for c in cmds}
        assert len(cmds) > 180 and "pcb export gerbers" in names and "sch annotate" in names
        assert "skill install" in names
        print(f"\n  kce exposes {len(cmds)} commands")

    @pytest.mark.smoke
    def test_undo_across_processes(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "u"), "--name", "u"])["project"]["pro"]
        P = ["--project", p]
        sch = p.replace(".kicad_pro", ".kicad_sch")
        empty = read_text(sch)
        run(P + ["sch", "add", "Device:R", "--ref", "R1", "--at", "50,50"])
        run(P + ["sch", "add", "Device:C", "--ref", "C1", "--at", "60,50"])
        assert rj(P + ["session", "status"])["undo_depth"] == 2
        rj(P + ["session", "undo"])
        assert [s["reference"] for s in rj(P + ["sch", "symbols"])] == ["R1"]
        rj(P + ["session", "undo"])
        assert read_text(sch) == empty
        rj(P + ["session", "redo", "--steps", "2"])
        assert len(rj(P + ["sch", "symbols"])) == 2
        with open(sch, "rb") as fh:
            assert b"\r\n" not in fh.read()  # KiCad files are written with LF on every OS

    def test_repl_mode(self, blink):
        r = run(["--project", blink], input="sch set-value R1 4k7\nproject save\n--json sch show R1\nquit\n")
        assert '"value":"4k7"' in r.stdout.replace('": ', '":')  # compact JSON when piped
        assert "4k7" in read_text(os.path.join(blink, "blink.kicad_sch"))

    @pytest.mark.smoke
    def test_repl_piped_stdin(self, tmp_dir):
        p = rj(["project", "new", os.path.join(tmp_dir, "r"), "--name", "r"])["project"]["pro"]
        r = run(["--project", p], input="# comment\nsch add Device:R --ref R1 --at 50,50\nproject save\n"
                                         "--json sch symbols\nkce --json session status\nquit\n")
        lines = r.stdout
        assert "interactive shell" not in lines  # no banner when piped
        lines = lines.replace('": ', '":')  # compact JSON when piped
        assert '"reference":"R1"' in lines and '"undo_depth":1' in lines

    @pytest.mark.smoke
    def test_non_utf8_stdout_does_not_crash(self, tmp_dir):
        """A Windows console/pipe may be cp1252: output must stay decodable and the command must succeed."""
        p = rj(["project", "new", os.path.join(tmp_dir, "enc"), "--name", "enc"])["project"]["pro"]
        run(["--project", p, "sch", "add", "Device:R", "--ref", "R1", "--at", "50,50"])
        text = "저항 Ω µ ✓"
        run(["--project", p, "sch", "set-field", "R1", "Note", text])
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        r = subprocess.run(CLI + ["--json", "--project", p, "sch", "show", "R1"], capture_output=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=600)
        assert r.returncode == 0, r.stderr
        r.stdout.decode("ascii")  # non-UTF-8 stdout => ASCII-escaped JSON
        assert json.loads(r.stdout)["fields"]["Note"] == text
        r = subprocess.run(CLI + ["--project", p, "sch", "show", "R1"], capture_output=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=600)
        assert r.returncode == 0 and b"Traceback" not in r.stderr

    def test_full_workflow_subprocess(self, tmp_dir):
        """Scenario 1 entirely through the installed command, from an unrelated cwd."""
        p = build_blinker(tmp_dir)
        e = rj(["--project", p, "check", "erc"])
        d = rj(["--project", p, "check", "drc"])
        assert e["clean"] and d["clean"], (e["by_type"], d["by_type"])
        f = rj(["--project", p, "fab", "package", "--preset", "generic"])
        assert f["summary"]["gerbers"] >= 9 and os.path.getsize(opath(f, f["zip"])) > 5000
        artifact(opath(f, f["zip"]), "fab zip")
