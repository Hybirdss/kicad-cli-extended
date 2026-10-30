"""Machine-contract tests: connectivity safety, ``sch nets`` JSON, UUIDs, failure signals.

These run the real ``kce`` command against real KiCad 10, like ``test_e2e.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

import pytest

from kicad_cli_extended import COMMAND, ENV_PREFIX, STATE_DIR
from kicad_cli_extended.core.netlist import PIN_TYPES

pytestmark = pytest.mark.e2e

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


def kce(args, cwd=None, env=None, check=True):
    e = dict(os.environ)
    e.update(env or {})
    r = __import__("subprocess").run(CLI + [str(a) for a in args], capture_output=True, text=True, cwd=cwd,
                                     env=e, stdin=__import__("subprocess").DEVNULL, timeout=1800, **UTF8)
    if check and r.returncode != 0:
        raise AssertionError(f"{COMMAND} {' '.join(map(str, args))} failed ({r.returncode}):\n"
                             f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-3000:]}")
    return r


def kcej(args, cwd=None, env=None):
    return json.loads(kce(["--json", *args], cwd=cwd, env=env).stdout)


_ERROR_SCHEMA = None


def kce_err(args, cwd=None, env=None):
    global _ERROR_SCHEMA
    r = kce(["--json", *args], cwd=cwd, env=env, check=False)
    assert r.returncode != 0, f"expected failure, got 0: {r.stdout}"
    err = json.loads(r.stderr)
    if _ERROR_SCHEMA is None:
        import jsonschema
        with open(os.path.join(REPO, "schemas", "error.schema.json"), encoding="utf-8") as f:
            _ERROR_SCHEMA = jsonschema.Draft202012Validator(json.load(f))
    _ERROR_SCHEMA.validate(err)
    return r, err


@pytest.fixture
def tmp_dir():
    import tempfile
    d = tempfile.mkdtemp(prefix="kce-contract-")
    yield d
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def quickstart_master():
    import tempfile
    d = tempfile.mkdtemp(prefix="kce-contract-master-")
    yield build_quickstart(d)
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def blink(quickstart_master, tmp_dir):
    dst = os.path.join(tmp_dir, "blink")
    shutil.copytree(quickstart_master, dst, ignore=shutil.ignore_patterns(STATE_DIR))
    return dst


def build_quickstart(root):
    """The README quickstart circuit, command for command (J1 - R1 - D1 in a loop)."""
    p = os.path.join(root, "blink")
    kce(["project", "new", p, "--name", "blink"])
    kce(["lib", "init-global-tables"], cwd=p)
    kce(["sch", "add", "Device:LED", "--ref", "D1", "--at", "100,60",
         "--footprint", "LED_SMD:LED_0805_2012Metric"], cwd=p)
    kce(["sch", "add", "Device:R", "--ref", "R1", "--at", "80,60", "--value", "330",
         "--footprint", "Resistor_SMD:R_0805_2012Metric"], cwd=p)
    kce(["sch", "add", "Connector_Generic:Conn_01x02", "--ref", "J1", "--at", "60,60",
         "--footprint", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"], cwd=p)
    kce(["sch", "connect", "J1:1", "R1:1"], cwd=p)
    kce(["sch", "connect", "R1:2", "D1:2"], cwd=p)
    kce(["sch", "connect", "D1:1", "J1:2"], cwd=p)
    return p


def net_pin_sets(nets):
    """Each net as a set of REF:PIN strings (works with `pins` and with legacy `nodes`)."""
    out = []
    for n in nets:
        if "pins" in n:
            out.append({f"{p['ref']}:{p['pin']}" for p in n["pins"]})
        else:  # node strings carry the pin function in parentheses
            out.append({x.split("(")[0] for x in n["nodes"]})
    return out


def net_pins(nets):
    return sorted(sorted(s) for s in net_pin_sets(nets))


class TestConnectivity:
    def test_quickstart_routes_three_nets_without_shorting(self, blink):
        nets = kcej(["sch", "nets"], cwd=blink)["nets"]
        assert net_pins(nets) == [["D1:1", "J1:2"], ["D1:2", "R1:2"], ["J1:1", "R1:1"]]

    def test_connect_across_its_own_label_pin_stub(self, blink):
        # `label-pin` leaves a 2.54 mm stub with the label at its far end; stem and label are
        # on the pin's own net, so the connection must extend them, not be refused.
        kce(["sch", "add", "Device:R", "--ref", "R5", "--at", "60,90"], cwd=blink)
        kce(["sch", "add", "Device:R", "--ref", "R6", "--at", "60,104"], cwd=blink)
        kce(["sch", "label-pin", "R5:2", "N5"], cwd=blink)
        kce(["sch", "connect", "R5:2", "R6:1"], cwd=blink)
        assert {"R5:2", "R6:1"} in net_pin_sets(kcej(["sch", "nets"], cwd=blink)["nets"])

    @pytest.mark.smoke
    def test_connect_refuses_to_merge_unrequested_nets(self, blink):
        p = blink
        # moving D1 by (-16.51, +3.81) lands its K pin on R1's pin 2
        r, err = kce_err(["sch", "move", "D1", "--by", "-16.51,3.81"], cwd=p)
        assert err["type"] == "NetMergeError" and err["category"] == "design"
        assert r.returncode == 3
        assert err["details"]["merges"][0]["pins"]
        # nothing changed: the schematic still has three nets
        assert len(kcej(["sch", "nets"], cwd=p)["nets"]) == 3

    def test_wire_may_tap_the_middle_of_an_existing_wire(self, blink):
        # a pin wired to the middle of a wire joins that wire's net: requested, not a surprise
        kce(["sch", "add", "Device:R", "--ref", "R2", "--at", "67.31,50"], cwd=blink)
        kce(["sch", "connect", "R2:2", "67.31,59.69"], cwd=blink)
        assert {"J1:1", "R1:1", "R2:2"} in net_pin_sets(kcej(["sch", "nets"], cwd=blink)["nets"])

    def test_power_symbol_may_not_short_two_named_rails(self, blink):
        kce(["sch", "power", "GND", "--at", "D1:1"], cwd=blink)
        kce(["sch", "power", "+5V", "--at", "R1:1"], cwd=blink)
        for argv in (["sch", "power", "+5V", "--at", "D1:1"],
                     ["sch", "add", "power:+5V", "--at", "96.52,59.69"],
                     ["sch", "label-pin", "D1:1", "+5V"]):
            r, err = kce_err(argv, cwd=blink)
            assert (err["category"], err["type"]) == ("design", "NetMergeError"), argv
        # naming an auto-named net into a rail is still a plain `power`
        kce(["sch", "power", "GND", "--at", "D1:2"], cwd=blink)
        nets = {frozenset(s) for s in net_pin_sets(kcej(["sch", "nets"], cwd=blink)["nets"])}
        assert frozenset({"D1:1", "J1:2", "D1:2", "R1:2"}) in nets

    def test_junction_may_not_join_crossing_nets(self, blink):
        kce(["sch", "add", "Device:R", "--ref", "R2", "--at", "67.31,50"], cwd=blink)
        kce(["sch", "wire", "67.31,53.34;67.31,61", "--allow-merge"], cwd=blink)  # crosses y=59.69
        r, err = kce_err(["sch", "junction", "--at", "67.31,59.69"], cwd=blink)
        assert (err["category"], err["type"]) == ("design", "NetMergeError")

    def test_refused_edit_keeps_undo_and_redo_history(self, blink):
        import re
        script = "\n".join(["sch set-value R1 1k", "sch set-value R1 2k", "session undo",
                            "sch move D1 --by -16.51,3.81", "session status", "exit"]) + "\n"
        r = __import__("subprocess").run(CLI, input=script, capture_output=True, text=True, cwd=blink,
                                         timeout=600, **UTF8)
        depth = {k: int(v) for k, v in re.findall(r"(undo_depth|redo_depth): (\d+)", r.stdout)}
        assert "NetMergeError" in r.stderr or "merge nets" in r.stderr, r.stderr
        assert depth["redo_depth"] == 1  # the undone "2k" edit can still be redone

    def test_allow_merge_keeps_the_mutant(self, blink):
        p = blink
        kce(["sch", "move", "D1", "--by", "-16.51,3.81", "--allow-merge"], cwd=p)
        nets = kcej(["sch", "nets"], cwd=p)["nets"]
        assert {"D1:1", "R1:2"} in net_pin_sets(nets)


class TestRouting:
    def test_clean_route_matches_exhaustive_search_and_stays_fast(self):
        """The shortest clean candidate wins, found without re-scanning the sheet per candidate."""
        import time
        from kicad_cli_extended.core import schematic as sch_mod, sexpr
        demos = os.environ.get(ENV_PREFIX + "DEMOS") or os.path.join(REPO, ".fixtures", "kicad-src", "demos")
        path = os.path.join(demos, "vme-wren", "front-panel-io.kicad_sch")
        if not os.path.exists(path):
            pytest.skip("KiCad demo fixtures not fetched")
        with open(path, encoding="utf-8", newline="") as f:
            tree = sexpr.loads(f.read())
        for a, b in (((30.48, 40.64), (120.65, 100.33)), ((55.88, 45.72), (60.96, 45.72)),
                     ((100.33, 120.65), (35.56, 30.48))):
            cands = sch_mod.route_candidates(a, b)
            clean = [(i, p) for i, p in enumerate(cands) if not sch_mod.path_conflicts(tree, p, [a, b], [a, b])]
            started = time.time()
            try:
                got = sch_mod.clean_route(tree, a, b)
            except sch_mod.RouteBlockedError:
                got = None
            elapsed = time.time() - started
            want = min(clean, key=lambda c: (sch_mod._path_length(c[1]), c[0]))[1] if clean else None
            assert got == want
            assert elapsed < 2.0, f"clean_route took {elapsed:.1f}s on a 200-symbol sheet"


# ═══════════════════════════════════════════════════════ sch nets contract ═══

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO, "schemas", "sch-nets.schema.json")


def load_schema():
    import jsonschema
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


class TestNetsContract:
    @pytest.mark.smoke
    def test_output_matches_schema_and_orders_pins_naturally(self, blink):
        kce(["sch", "add", "Device:R", "--ref", "R10", "--at", "120,80"], cwd=blink)
        kce(["sch", "add", "Device:R", "--ref", "R2", "--at", "130,80"], cwd=blink)
        kce(["sch", "connect", "R2:2", "R10:1"], cwd=blink)
        data = kcej(["sch", "nets"], cwd=blink)
        load_schema().validate(data)
        net = next(n for n in data["nets"] if {p["ref"] for p in n["pins"]} == {"R2", "R10"})
        assert [p["ref"] for p in net["pins"]] == ["R2", "R10"]  # not R10, R2
        assert all(p["pin_type"] in PIN_TYPES for n in data["nets"] for p in n["pins"])
        assert net["sheet_path"] == "/"
        assert net["code"] is None or isinstance(net["code"], int)

    def test_same_project_gives_identical_bytes(self, blink):
        first = kce(["--json", "sch", "nets"], cwd=blink).stdout
        second = kce(["--json", "sch", "nets"], cwd=blink).stdout
        assert first == second
        kce(["--json", "sch", "set-value", "R1", "330"], cwd=blink)  # unrelated rewrite, same value
        assert kce(["--json", "sch", "nets"], cwd=blink).stdout == first

    def test_hierarchical_sheet_path_is_kicads_name(self, blink):
        kce(["sch", "add-sheet", "Power", "--at", "150,60", "--size", "40,30"], cwd=blink)
        kce(["sch", "add", "Device:R", "--ref", "R20", "--at", "160,60", "--sheet", "Power"], cwd=blink)
        kce(["sch", "add", "Device:R", "--ref", "R21", "--at", "170,60", "--sheet", "Power"], cwd=blink)
        kce(["sch", "connect", "R20:2", "R21:1", "--sheet", "Power"], cwd=blink)
        data = kcej(["sch", "nets"], cwd=blink)
        load_schema().validate(data)
        net = next(n for n in data["nets"] if {p["ref"] for p in n["pins"]} == {"R20", "R21"})
        assert net["sheet_path"] == "/Power/"

    def test_natural_key_is_total_and_never_raises(self):
        from kicad_cli_extended.core.netlist import natural_key
        names = ["R10", "R2", "R01", "R1", "GND", "gnd", "1\u00b2", "\u00b2", "Net-(R1-Pad1)", "", "9" * 40]
        ordered = sorted(names, key=natural_key)
        assert ordered == sorted(reversed(names), key=natural_key)  # input order never matters
        assert ordered.index("R1") < ordered.index("R2") < ordered.index("R10")

    def test_contract_nets_natural_key_is_lexicographic_safe(self):
        from kicad_cli_extended.core.netlist import contract_nets, natural_key
        assert natural_key("R2") < natural_key("R10")
        assert natural_key("2") < natural_key("10")
        nl = {"components": [{"ref": "R10", "lib": "Device", "part": "R", "sheet_names": "/"},
                             {"ref": "R2", "lib": "Device", "part": "R", "sheet_names": "/"}],
              "libpins": {("Device", "R", "1"): {"name": "", "type": "passive"}},
              "nets": [{"code": "1", "name": "N", "class": "Default",
                        "nodes": [{"ref": "R10", "pin": "1", "pinfunction": "", "pintype": "passive"},
                                  {"ref": "R2", "pin": "10", "pinfunction": "P_10", "pintype": "input"}]}]}
        net = contract_nets(nl)[0]
        assert [(p["ref"], p["pin"]) for p in net["pins"]] == [("R2", "10"), ("R10", "1")]
        assert net["pins"][0]["pin_name"] == "P" and net["pins"][0]["pin_type"] == "input"

    def test_no_connect_flag_is_a_field_not_part_of_the_pin_type(self, blink):
        kce(["sch", "add", "Device:R", "--ref", "R40", "--at", "120,100"], cwd=blink)
        kce(["sch", "no-connect", "R40:1"], cwd=blink)
        data = kcej(["sch", "nets"], cwd=blink)
        load_schema().validate(data)  # KiCad's own type string is "passive+no_connect"
        pins = {(p["ref"], p["pin"]): p for n in data["nets"] for p in n["pins"]}
        assert (pins[("R40", "1")]["pin_type"], pins[("R40", "1")]["no_connect"]) == ("passive", True)
        assert pins[("R40", "2")]["no_connect"] is False


# ══════════════════════════════════════════════════════ caller-set UUIDs ═══

STABLE_NS = "6ba7b811-9dad-11d1-80b4-00c04fd430c8"


class TestCallerUuids:
    def test_sch_add_uses_and_validates_the_caller_uuid(self, blink):
        r, err = kce_err(["sch", "add", "Device:R", "--ref", "R30", "--at", "120,100",
                          "--uuid", "not-a-uuid"], cwd=blink)
        assert r.returncode == 2 and err["category"] == "input"
        assert "not a usable UUID" in err["error"]
        uid = "6f1c2b9a-4d3e-5f60-8712-3456789abcde"
        kce(["sch", "add", "Device:R", "--ref", "R30", "--at", "120,100", "--uuid", uid], cwd=blink)
        assert kcej(["sch", "show", "R30"], cwd=blink)["uuid"] == uid
        r, err = kce_err(["sch", "add", "Device:R", "--ref", "R31", "--at", "130,100",
                          "--uuid", uid.upper()], cwd=blink)
        assert r.returncode == 2 and "already exists" in err["error"]

    def test_only_plain_non_nil_uuids_are_accepted(self):
        from kicad_cli_extended.core.schematic import normalize_uuid
        assert normalize_uuid(" 3F2B8C1E-9D4A-5B6C-8E7F-0123456789AB\n") == "3f2b8c1e-9d4a-5b6c-8e7f-0123456789ab"
        for bad in ("00000000-0000-0000-0000-000000000000", "{3f2b8c1e-9d4a-5b6c-8e7f-0123456789ab}",
                    "urn:uuid:3f2b8c1e-9d4a-5b6c-8e7f-0123456789ab", "3f2b8c1e9d4a5b6c8e7f0123456789ab",
                    "3f2b8c1e-9d4a-5b6c-8e7f-0123456789a", "\uff13f2b8c1e-9d4a-5b6c-8e7f-0123456789ab", ""):
            with pytest.raises(ValueError, match="not a usable UUID"):
                normalize_uuid(bad)

    def test_pcb_update_stable_uuids_are_uuid5_of_the_symbol_path(self, blink):
        import uuid as uuid_mod
        from kicad_cli_extended.core.netlist import symbol_uuid_path
        kce(["pcb", "update", "--stable-uuids", "--no-place"], cwd=blink)
        comps = [c for c in kcej(["sch", "components"], cwd=blink) if not c["ref"].startswith("#")]
        want = {c["ref"]: str(uuid_mod.uuid5(uuid_mod.UUID(STABLE_NS), symbol_uuid_path(c))) for c in comps}
        got = {f["reference"]: f["uuid"] for f in kcej(["pcb", "footprints"], cwd=blink)}
        assert {r: got[r] for r in want} == want
        # a fresh run (new board, fresh state) produces the same ids
        os.unlink(os.path.join(blink, "blink.kicad_pcb"))
        shutil.rmtree(os.path.join(blink, STATE_DIR), ignore_errors=True)
        kce(["pcb", "update", "--stable-uuids", "--no-place"], cwd=blink)
        again = {f["reference"]: f["uuid"] for f in kcej(["pcb", "footprints"], cwd=blink)}
        assert again == got


# ═══════════════════════════════════════════════════ failure signal table ═══

def fake_kicad_cli(tmp_dir, body):
    """A stand-in kicad-cli: answers `version`, runs ``body`` for anything else."""
    script = os.path.join(tmp_dir, "fake_kicad_cli.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write("import sys, time\n"
                "if len(sys.argv) > 1 and sys.argv[1] == 'version':\n"
                "    print('10.0.7')\n"
                "    sys.exit(0)\n" + body)
    if os.name == "nt":
        exe = os.path.join(tmp_dir, "fake_kicad_cli.cmd")
        with open(exe, "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "{script}" %*\n')
    else:
        exe = os.path.join(tmp_dir, "fake_kicad_cli")
        with open(exe, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        os.chmod(exe, 0o755)
    return exe


@pytest.mark.smoke
class TestFailureSignals:
    def test_input_bad_reference(self, blink):
        r, err = kce_err(["sch", "show", "R99"], cwd=blink)
        assert r.returncode == 2
        assert (err["category"], err["type"]) == ("input", "KeyError")
        assert "R99" in err["error"] and err["hint"]

    def test_design_unrequested_merge(self, blink):
        r, err = kce_err(["sch", "move", "D1", "--by", "-16.51,3.81"], cwd=blink)
        assert r.returncode == 3
        assert (err["category"], err["type"]) == ("design", "NetMergeError")
        assert err["details"]["merges"]

    def test_validation_kicad_rejects_saved_file(self, blink):
        sch = os.path.join(blink, "blink.kicad_sch")
        with open(sch, encoding="utf-8") as f:
            before = f.read()
        r, err = kce_err(["raw", "set", "blink.kicad_sch", "symbol[0]/lib_id", "bad:id:x", "--quoted"], cwd=blink)
        assert r.returncode == 4
        assert (err["category"], err["type"]) == ("validation", "ValidationError")
        assert "rolled back" in err["error"]
        with open(sch, encoding="utf-8") as f:
            assert f.read() == before

    def test_crash_kicad_cli_fails(self, blink, tmp_dir):
        exe = fake_kicad_cli(tmp_dir, "sys.stderr.write('simulated crash\\n')\nsys.exit(2)\n")
        r, err = kce_err(["sch", "nets"], cwd=blink, env={"KCE_KICAD_CLI": exe})
        assert r.returncode == 5
        assert (err["category"], err["type"]) == ("crash", "KicadCrash")

    def test_timeout_kicad_cli_hangs(self, blink, tmp_dir):
        exe = fake_kicad_cli(tmp_dir, "time.sleep(30)\n")
        r, err = kce_err(["sch", "nets"], cwd=blink,
                         env={"KCE_KICAD_CLI": exe, "KCE_TIMEOUT": "1"})
        assert r.returncode == 6
        assert (err["category"], err["type"]) == ("timeout", "KicadTimeout")

    def test_environment_kicad_missing(self, tmp_dir):
        r, err = kce_err(["info", "version"], cwd=tmp_dir,
                         env={"KCE_KICAD_CLI": os.path.join(tmp_dir, "no-such-kicad-cli")})
        assert r.returncode == 7
        assert (err["category"], err["type"]) == ("environment", "KicadNotFound")

    def test_caller_states_are_not_reported_as_kce_bugs(self):
        from kicad_cli_extended import cli as cli_mod
        from kicad_cli_extended.utils import kicad_backend as kb
        cases = [
            (RuntimeError("R1 is locked; unlock it first or pass --force"), "input", "ItemLocked"),
            # the pcbnew worker ships the same refusal back wrapped in a KicadError
            (kb.KicadError("pcbnew move: R1 is locked; unlock it first or pass --force"),
             "input", "ItemLocked"),
            (RuntimeError("Nothing to undo"), "input", "RuntimeError"),
            (RuntimeError("Project is locked by another kce process"), "input", "RuntimeError"),
            (RuntimeError("ERC failed with 2 error(s)"), "design", "RuntimeError"),
            (RuntimeError("Freerouting not found. Install Java 17+"), "environment", "RuntimeError"),
        ]
        for exc, category, name in cases:
            assert cli_mod.classify_error(exc) == (name, category), str(exc)
            assert cli_mod.EXIT_CODES[category] in (2, 3, 7)

    def test_usage_errors_are_json_on_stderr(self, blink):
        for argv in (["sch", "show"], ["sch", "bogus"], ["sch", "show", "R1", "--nope"]):
            r, err = kce_err(argv, cwd=blink)
            assert r.returncode == 2 and (err["category"], err["type"]) == ("input", "UsageError")
            assert err["hint"].endswith("--help")
        r = kce(["--help"], cwd=blink)
        assert r.returncode == 0 and "Usage:" in r.stdout

    def test_kicad_cli_rejecting_its_input_is_not_a_crash(self, blink):
        r, err = kce_err(["sch", "export", "pdf", "nonexistent.kicad_sch"], cwd=blink)
        assert r.returncode == 2
        assert (err["category"], err["type"]) == ("input", "KicadInputError")
        assert err["details"]["returncode"] == 3

    def test_pcbnew_worker_rejecting_a_reference_is_input(self, blink):
        r, err = kce_err(["pcb", "move", "R99", "--at", "1,1"], cwd=blink)
        assert r.returncode == 2
        assert (err["category"], err["type"]) == ("input", "KeyError")

    def test_kicad_exit_codes_and_external_tool_timeouts(self):
        import subprocess
        from kicad_cli_extended import cli as cli_mod
        from kicad_cli_extended.utils import kicad_backend as kb
        run = lambda code: kb.KicadError("kicad-cli x failed", {"command": ["kicad-cli"], "returncode": code})
        assert cli_mod.classify_error(run(1)) == ("KicadInputError", "input")
        assert cli_mod.classify_error(run(5)) == ("KicadCheckFailed", "design")
        assert cli_mod.classify_error(run(2)) == ("KicadCrash", "crash")
        assert cli_mod.classify_error(run(-11)) == ("KicadCrash", "crash")
        worker_crash = kb.KicadError("pcbnew worker 'x' crashed (exit 1).", {"returncode": 1})
        assert cli_mod.classify_error(worker_crash) == ("KicadCrash", "crash")
        assert cli_mod.classify_error(subprocess.TimeoutExpired(["java"], 5)) == ("ToolTimeout", "timeout")

    def test_unexpected_exception_is_one_json_object_with_a_traceback(self, monkeypatch, capsys):
        from kicad_cli_extended import cli as cli_mod
        monkeypatch.setattr(cli_mod, "_json_output", True)
        monkeypatch.setattr(cli_mod, "_repl_mode", False)

        @cli_mod.handle_error
        def boom():
            return 1 / 0

        with pytest.raises(SystemExit) as ei:
            boom()
        assert ei.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert (err["category"], err["type"]) == ("internal", "InternalError")
        assert "ZeroDivisionError" in err["details"]["traceback"]

    def test_internal_unexpected_error_is_a_kce_fault(self):
        from kicad_cli_extended import cli as cli_mod
        name, category = cli_mod.classify_error(RuntimeError("unexpected"))
        assert (category, name) == ("internal", "RuntimeError")
        assert cli_mod.EXIT_CODES[category] == 1
        assert cli_mod.classify_error(RuntimeError("something else broke"))[1] == "internal"

    def test_input_subclasses_report_their_documented_name(self):
        import json as json_mod
        from kicad_cli_extended import cli as cli_mod
        from kicad_cli_extended.core import sexpr
        assert cli_mod.classify_error(json_mod.JSONDecodeError("x", "{", 0)) == ("ValueError", "input")
        assert cli_mod.classify_error(sexpr.SexprError("bad")) == ("SexprError", "input")

    def test_every_error_type_and_exit_code_is_documented(self):
        from kicad_cli_extended import cli as cli_mod
        import jsonschema
        with open(os.path.join(REPO, "schemas", "error.schema.json"), encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)
        names = set(schema["$defs"]["type"]["enum"])
        for _, name, _ in cli_mod._ERROR_TABLE:
            if name:
                assert name in names
        assert schema["$defs"]["exitCodes"]["enum"] == [1, 2, 3, 4, 5, 6, 7]
        assert cli_mod.EXIT_CODES == {"internal": 1, "input": 2, "design": 3, "validation": 4,
                                      "crash": 5, "timeout": 6, "environment": 7}


def test_worker_faults_are_not_missing_installations():
    from kicad_cli_extended.cli import classify_error, error_payload
    from kicad_cli_extended.utils.kicad_backend import KicadError
    for exc in (AttributeError("missing method"), TypeError("bad call"), IndexError("bug"),
                KicadError("pcbnew route: AttributeError", {"traceback": "Traceback ..."})):
        assert classify_error(exc)[1] == "internal"
    assert classify_error(KicadError("worker crashed (exit -11)", {"returncode": -11})) == ("KicadCrash", "crash")
    assert classify_error(KicadError("points to a missing file: /nonexistent")) == ("KicadNotFound", "environment")
    assert "secret" not in error_payload(KicadError("bug", {"traceback": "secret"})).get("details", {}).get("traceback", "")


@pytest.mark.parametrize("args", [["set-value", "ZZ9", "1"], ["set-field", "ZZ9", "MPN", "x"],
    ["set-footprint", "ZZ9", "missing:missing"], ["remove-field", "ZZ9", "MPN"], ["set-attr", "ZZ9", "dnp", "yes"]])
def test_unknown_setter_reference_never_records_history(blink, args):
    from pathlib import Path
    path = Path(blink) / "blink.kicad_sch"
    before, history = path.read_bytes(), kcej(["session", "history"], cwd=blink)
    r, err = kce_err(["sch", *args], cwd=blink)
    assert r.returncode == 2 and err["category"] == "input" and err["type"] == "KeyError"
    assert "No symbol with reference 'ZZ9'" in err["error"] and "#PWR" not in err["error"]
    assert path.read_bytes() == before
    assert kcej(["session", "history"], cwd=blink) == history


def test_missing_models_warn_and_require_fails_environment(tmp_path):
    result = kce(['--json', 'info', 'doctor', '--require', 'step'],
        env={'KICAD10_3DMODEL_DIR': str(tmp_path / 'missing')}, check=False)
    data = json.loads(result.stdout)
    assert not data['capabilities']['step']
    assert data['warnings'][0]['id'] == '3dmodels_missing'
    assert result.returncode == 7 and json.loads(result.stderr)['category'] == 'environment'
