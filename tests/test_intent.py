"""Netlist intent: parsing (unit), and verify/apply against real KiCad (e2e)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from kicad_cli_extended import COMMAND, ENV_PREFIX, STATE_DIR
from kicad_cli_extended.core import intent as intent_mod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO, "schemas", "netlist-intent.schema.json")

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


def kce(args, cwd=None, check=True):
    r = subprocess.run(CLI + [str(a) for a in args], capture_output=True, text=True, cwd=cwd,
                       stdin=subprocess.DEVNULL, timeout=1800, **UTF8)
    if check and r.returncode != 0:
        raise AssertionError(f"{COMMAND} {' '.join(map(str, args))} failed ({r.returncode}):\n"
                             f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-3000:]}")
    return r


def kcej(args, cwd=None):
    return json.loads(kce(["--json", *args], cwd=cwd).stdout)


def kce_err(args, cwd=None):
    r = kce(["--json", *args], cwd=cwd, check=False)
    assert r.returncode != 0, f"expected failure, got 0: {r.stdout}"
    return r, json.loads(r.stderr)

QUICKSTART_INTENT = {
    "version": 1,
    "components": [
        {"ref": "J1", "lib_id": "Connector_Generic:Conn_01x02",
         "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
         "value": "PWR", "at": [60, 60]},
        {"ref": "R1", "lib_id": "Device:R", "footprint": "Resistor_SMD:R_0805_2012Metric",
         "value": "330", "at": [80, 60]},
        {"ref": "D1", "lib_id": "Device:LED", "footprint": "LED_SMD:LED_0805_2012Metric",
         "value": "RED", "at": [100, 60]},
    ],
    "nets": [
        {"name": "VCC", "pins": ["J1:1", "R1:1"]},
        {"name": "LED_A", "pins": ["R1:2", "D1:2"]},
        {"name": "GND", "pins": ["D1:1", "J1:2"]},
    ],
}


@pytest.fixture
def tmp_dir():
    import tempfile
    d = tempfile.mkdtemp(prefix="kce-intent-")
    yield d
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


def intent_file(tmp_dir, data=None, name="intent.json"):
    path = os.path.join(tmp_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(QUICKSTART_INTENT if data is None else data, f)
    return path


class TestSchema:
    def test_schema_is_valid_draft_2020_12(self):
        import jsonschema
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_quickstart_intent_matches_schema(self):
        import jsonschema
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            validator = jsonschema.Draft202012Validator(json.load(f))
        validator.validate(QUICKSTART_INTENT)


class TestParse:
    def test_parses_components_and_nets_into_frozen_records(self, tmp_dir):
        it = intent_mod.parse_intent(intent_file(tmp_dir))
        assert [c.ref for c in it.components] == ["J1", "R1", "D1"]
        assert it.components[1].at == (80.0, 60.0)
        assert it.nets[0].pins == ("J1:1", "R1:1")
        assert it.nets[0].kind == "local"
        with pytest.raises(Exception):
            it.components[0].ref = "X"
        assert it.to_json()["nets"][0] == {"name": "VCC", "pins": ["J1:1", "R1:1"]}

    def test_unknown_key_names_its_json_path(self, tmp_dir):
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        data["components"][0]["colour"] = "red"
        with pytest.raises(intent_mod.IntentError) as e:
            intent_mod.parse_intent(intent_file(tmp_dir, data))
        assert "components[0].colour" in str(e.value)
        assert "unknown key" in str(e.value)

    def test_unknown_top_level_key_is_rejected(self, tmp_dir):
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        data["netclass"] = {}
        with pytest.raises(intent_mod.IntentError) as e:
            intent_mod.parse_intent(intent_file(tmp_dir, data))
        assert "netclass" in str(e.value)

    @pytest.mark.parametrize("mutate,path", [
        (lambda d: d["components"][0].pop("lib_id"), "components[0].lib_id"),
        (lambda d: d["components"][0].__setitem__("lib_id", "NoColon"), "components[0].lib_id"),
        (lambda d: d["components"][0].__setitem__("at", [1]), "components[0].at"),
        (lambda d: d["components"][0].__setitem__("at", [1, "2"]), "components[0].at[1]"),
        (lambda d: d["components"][0].__setitem__("angle", 45), "components[0].angle"),
        (lambda d: d["components"][0].__setitem__("unit", 0), "components[0].unit"),
        (lambda d: d["components"][0].__setitem__("uuid", "not-a-uuid"), "components[0].uuid"),
        (lambda d: d["nets"][0].__setitem__("pins", []), "nets[0].pins"),
        (lambda d: d["nets"][0].__setitem__("pins", ["R1"]), "nets[0].pins[0]"),
        (lambda d: d["nets"][0].__setitem__("kind", "hier"), "nets[0].kind"),
        (lambda d: d["components"][1].__setitem__("ref", "J1"), "components[1].ref"),
        (lambda d: d["nets"][1].__setitem__("pins", ["J1:1", "D1:2"]), "nets[1].pins[0]"),
        (lambda d: d["nets"][0].__setitem__("pins", ["R99:1", "R1:1"]), "nets[0].pins[0]"),
        (lambda d: d.__setitem__("version", 2), "version"),
    ])
    def test_bad_values_are_rejected_with_their_path(self, tmp_dir, mutate, path):
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        mutate(data)
        with pytest.raises(intent_mod.IntentError) as e:
            intent_mod.parse_intent(intent_file(tmp_dir, data))
        assert path in str(e.value), str(e.value)

    def test_missing_file_is_a_file_not_found(self, tmp_dir):
        with pytest.raises(FileNotFoundError):
            intent_mod.parse_intent(os.path.join(tmp_dir, "nope.json"))

    def test_broken_json_is_an_intent_error_naming_the_line(self, tmp_dir):
        path = os.path.join(tmp_dir, "broken.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"version": 1,')
        with pytest.raises(intent_mod.IntentError) as e:
            intent_mod.parse_intent(path)
        assert "line 1" in str(e.value)

    def _pin_level(self):
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        data["components"].append({"ref": "U9", "lib_id": "Device:R", "dnp": True,
                                   "in_bom": False, "on_board": True})
        data["no_connect"] = ["U9:1"]
        data["power_flags"] = [{"pin": "U9:2"}]
        data["remove"] = ["R99"]
        return data

    def test_pin_level_keys_parse_and_roundtrip(self, tmp_dir):
        it = intent_mod.parse_intent(intent_file(tmp_dir, self._pin_level()))
        assert it.no_connect == ("U9:1",)
        assert [f.pin for f in it.power_flags] == ["U9:2"]
        assert it.remove == ("R99",)
        u9 = it.component("U9")
        assert (u9.dnp, u9.in_bom, u9.on_board) == (True, False, True)
        doc = it.to_json()
        assert doc["no_connect"] == ["U9:1"]
        assert doc["power_flags"] == [{"pin": "U9:2"}]
        assert doc["remove"] == ["R99"]
        assert doc["components"][-1]["dnp"] is True

    @pytest.mark.parametrize("mutate,path", [
        (lambda d: d.__setitem__("no_connect", ["R99:1"]), "no_connect[0]"),
        (lambda d: d.__setitem__("no_connect", ["R1:1"]), "no_connect[0]"),
        (lambda d: d.__setitem__("no_connect", ["U9:1", "U9:1"]), "no_connect[1]"),
        (lambda d: d.__setitem__("no_connect", ["U9"]), "no_connect[0]"),
        (lambda d: d.__setitem__("power_flags", [{"pin": "R99:1"}]), "power_flags[0].pin"),
        (lambda d: d.__setitem__("power_flags", [{"pin": "U9:1"}]), "power_flags[0].pin"),
        (lambda d: d.__setitem__("power_flags", [{"pin": "U9:2"}, {"pin": "U9:2"}]), "power_flags[1].pin"),
        (lambda d: d.__setitem__("power_flags", [{"name": "U9:2"}]), "power_flags[0].name"),
        (lambda d: d.__setitem__("power_flags", [{"pin": "U9:2", "net": "X"}]), "power_flags[0].net"),
        (lambda d: d.__setitem__("remove", ["R1"]), "remove[0]"),
        (lambda d: d.__setitem__("remove", ["R99", "R99"]), "remove[1]"),
        (lambda d: d.__setitem__("remove", "R99"), "remove"),
        (lambda d: d["components"][0].__setitem__("dnp", "yes"), "components[0].dnp"),
    ])
    def test_pin_level_keys_are_validated(self, tmp_dir, mutate, path):
        data = self._pin_level()
        mutate(data)
        with pytest.raises(intent_mod.IntentError) as e:
            intent_mod.parse_intent(intent_file(tmp_dir, data))
        assert path in str(e.value), str(e.value)


# ---------------------------------------------------------------------------
# Comparison against a netlist (unit: synthetic netlists, no KiCad)
# ---------------------------------------------------------------------------

LIBPINS = {("Device", "R", "1"): {"name": "~", "type": "passive"},
           ("Device", "R", "2"): {"name": "~", "type": "passive"}}


def comp(ref, lib="Device", part="R", **kw):
    d = {"ref": ref, "lib": lib, "part": part, "footprint": "", "value": "", "fields": {},
         "properties": {}}
    d.update(kw)
    return d


def net(name, *pins, libpins=None):
    return {"name": name, "nodes": [{"ref": r, "pin": p} for r, p in pins]}


def netlist(comps, nets, libpins=None):
    return {"components": comps, "nets": nets, "libpins": LIBPINS if libpins is None else libpins}


def intent(components, nets, **kw):
    return intent_mod.parse_intent_data({"version": 1, "components": components, "nets": nets, **kw})


class TestCompare:
    def test_subset_projects_partitions_but_keeps_physical_connectivity(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1", "R2:1"]}])
        nl = netlist([comp("R1"), comp("R2"), comp("R3")],
                     [net("/A", ("R1", "1"), ("R2", "1"), ("R3", "1"))])
        assert intent_mod.compare(it, nl, subset=True)["match"]
        assert not intent_mod.compare(it, nl)["match"]
        nl["nets"] = [net("/A", ("R1", "1"), ("R3", "1")), net("/B", ("R2", "1"))]
        split = intent_mod.compare(it, nl, subset=True)
        assert not split["match"] and split["split"]
        one = intent([{"ref": "R1", "lib_id": "Device:R"}], [{"name": "A", "pins": ["R1:1"]}])
        assert intent_mod.compare(one, nl, subset=True)["match"]  # R3 supplies a physical peer

    def test_optional_identity_and_net_name_checks(self):
        uid = "fd8d23f8-614b-4b1c-bc3d-894072d9dd77"
        it = intent([{"ref": "R1", "lib_id": "Device:R", "uuid": uid},
                     {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1", "R2:1"]}])
        nl = netlist([comp("R1", part="C", tstamps=["different"]), comp("R2")],
                     [net("/A", ("R1", "1"), ("R2", "1"))])
        assert intent_mod.compare(it, nl)["match"]
        out = intent_mod.compare(it, nl, check_lib_id=True, check_uuid=True, check_net_names=True)
        assert out["match"] is False
        assert out["symbol_mismatches"] == [{"ref": "R1", "expected": "Device:R", "actual": "Device:C"}]
        assert out["identity_mismatches"][0]["expected"] == uid
        assert out["name_mismatches"] == out["renamed"]
        nl["components"][0].update(part="R", tstamps=[uid])
        nl["nets"][0]["name"] = "A"
        assert intent_mod.compare(it, nl, check_lib_id=True, check_uuid=True, check_net_names=True)["match"]

    def test_match_keeps_kicads_auto_names_as_renamed(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1", "R2:1"]}])
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R2")], [net("/A", ("R1", "1"), ("R2", "1"))]))
        assert out["match"] is True
        assert out["renamed"] == [{"name": "A", "actual": "/A", "pins": ["R1:1", "R2:1"]}]

    def test_missing_and_extra_components_are_natural_order(self):
        it = intent([{"ref": "R2", "lib_id": "Device:R"}, {"ref": "R10", "lib_id": "Device:R"}], [])
        out = intent_mod.compare(it, netlist([comp("R10"), comp("R3"), comp("R1")], []))
        assert out["missing_components"] == ["R2"]
        assert out["extra_components"] == ["R1", "R3"]
        assert out["match"] is False

    def test_field_mismatches_cover_footprint_value_and_extra_fields(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R", "value": "1k", "footprint": "F:1",
                      "fields": {"MPN": "ABC"}}], [])
        actual = comp("R1", value="2k", fields={"MPN": "XYZ"})
        out = intent_mod.compare(it, netlist([actual], []))
        assert out["field_mismatches"] == [
            {"ref": "R1", "field": "MPN", "expected": "ABC", "actual": "XYZ"},
            {"ref": "R1", "field": "footprint", "expected": "F:1", "actual": ""},
            {"ref": "R1", "field": "value", "expected": "1k", "actual": "2k"},
        ]

    def test_missing_and_extra_nets(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "N1", "pins": ["R1:1", "R2:1"]}])
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R2")],
                                             [net("/X", ("R1", "1"), ("R1", "2"))]))
        assert out["missing_nets"] == [{"name": "N1", "pins": ["R1:1", "R2:1"]}]
        assert out["extra_nets"] == [{"name": "/X", "pins": ["R1:1", "R1:2"]}]
        assert out["merged"] == [] and out["split"] == []

    def test_merged_names_the_intended_nets_in_one_actual_net(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"},
                     {"ref": "R3", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1", "R2:1"]}, {"name": "B", "pins": ["R2:2", "R3:1"]}])
        actual = net("/AB", ("R1", "1"), ("R2", "1"), ("R2", "2"), ("R3", "1"))
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R2"), comp("R3")], [actual]))
        assert out["merged"] == [{"actual": "/AB", "nets": ["A", "B"],
                                  "pins": ["R1:1", "R2:1", "R2:2", "R3:1"]}]
        assert out["match"] is False

    def test_split_reports_the_actual_nets_holding_one_intended_net(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1", "R2:1"]}])
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R2")],
                                             [net("/A1", ("R1", "1")), net("/A2", ("R2", "1"))]))
        assert out["split"] == [{"name": "A", "nets": ["/A1", "/A2"], "pins": ["R1:1", "R2:1"]}]

    def test_unconnected_single_pin_nets_and_missing_pins(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["R1:1"]}, {"name": "B", "pins": ["R2:1"]}])
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R2")], [net("A", ("R1", "1"))]))
        assert out["unconnected_pins"] == ["R1:1", "R2:1"]
        assert out["missing_nets"] == [{"name": "B", "pins": ["R2:1"]}]

    def test_intent_pin_names_resolve_to_kicad_pin_numbers(self):
        libpins = {("Device", "LED", "1"): {"name": "K", "type": "passive"}}
        comps = [comp("D2", part="LED"), comp("R1")]
        it = intent([{"ref": "D2", "lib_id": "Device:LED"}, {"ref": "R1", "lib_id": "Device:R"}],
                    [{"name": "A", "pins": ["D2:K", "R1:1"]}])
        out = intent_mod.compare(it, netlist(comps, [net("A", ("D2", "1"), ("R1", "1"))], libpins))
        assert out["match"] is True

    def test_every_list_is_deterministic(self):
        it = intent([{"ref": "R10", "lib_id": "Device:R"}, {"ref": "R2", "lib_id": "Device:R"}],
                    [{"name": "N10", "pins": ["R10:1"]}, {"name": "N2", "pins": ["R2:1"]}])
        actual = netlist([comp("R10"), comp("R2"), comp("R1")],
                         [net("/Z", ("R10", "1"), ("R2", "1")), net("/Y", ("R1", "1"))])
        first = intent_mod.compare(it, actual)
        assert json.dumps(first, sort_keys=True) == json.dumps(intent_mod.compare(it, actual), sort_keys=True)
        assert [n["name"] for n in first["missing_nets"]] == ["N2", "N10"]
        assert [n["name"] for n in first["extra_nets"]] == ["/Y", "/Z"]

    def test_no_connect_pins_are_read_from_the_netlist_pin_type(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}], [], no_connect=["R1:2"])
        nc = {"name": "unconnected-(R1-Pad2)",
              "nodes": [{"ref": "R1", "pin": "2", "pintype": "passive+no_connect"}]}
        out = intent_mod.compare(it, netlist([comp("R1")], [nc]))
        assert out["no_connects_missing"] == [] and out["no_connects_extra"] == []
        assert out["extra_nets"] == []  # the no-connect pin's own net is expected
        assert out["match"] is True

    def test_a_missing_no_connect_flag_is_reported(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}], [], no_connect=["R1:2"])
        plain = net("unconnected-(R1-Pad2)", ("R1", "2"))
        out = intent_mod.compare(it, netlist([comp("R1")], [plain]))
        assert out["no_connects_missing"] == ["R1:2"]
        assert out["match"] is False

    def test_an_unlisted_no_connect_flag_on_a_listed_part_is_extra(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}], [])
        nc = {"name": "unconnected-(R1-Pad2)",
              "nodes": [{"ref": "R1", "pin": "2", "pintype": "passive+no_connect"}]}
        out = intent_mod.compare(it, netlist([comp("R1")], [nc]))
        assert out["no_connects_extra"] == ["R1:2"]
        assert out["match"] is False

    def test_no_connect_extras_on_unlisted_parts_do_not_count(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}], [])
        nc = {"name": "unconnected-(R9-Pad2)",
              "nodes": [{"ref": "R9", "pin": "2", "pintype": "passive+no_connect"}]}
        out = intent_mod.compare(it, netlist([comp("R1"), comp("R9")], [nc]))
        assert out["no_connects_extra"] == []
        assert out["extra_components"] == ["R9"]

    def test_component_attributes_and_removals(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R", "dnp": True, "in_bom": False,
                      "on_board": True}], [], remove=["R9"])
        actual = comp("R1", dnp=False, exclude_from_bom=True, exclude_from_board=True)
        out = intent_mod.compare(it, netlist([actual, comp("R9")], []))
        assert out["attribute_mismatches"] == [
            {"ref": "R1", "field": "dnp", "expected": True, "actual": False},
            {"ref": "R1", "field": "on_board", "expected": True, "actual": False},
        ]
        assert out["remove_present"] == ["R9"]
        assert out["extra_components"] == []  # a listed removal is not an unlisted extra
        assert out["match"] is False

    def test_subset_checks_only_the_listed_components_and_nets(self):
        it = intent([{"ref": "R1", "lib_id": "Device:R"}], [])
        actual = netlist([comp("R1"), comp("R2"), comp("R9")],
                         [net("/X", ("R1", "1"), ("R2", "1"))])
        full = intent_mod.compare(it, actual)
        assert full["extra_components"] == ["R2", "R9"]
        assert full["extra_nets"] == [{"name": "/X", "pins": ["R1:1", "R2:1"]}]
        assert full["match"] is False
        sub = intent_mod.compare(it, actual, subset=True)
        assert sub["extra_components"] == [] and sub["extra_nets"] == []
        assert sub["match"] is True


# ---------------------------------------------------------------------------
# sch verify against real KiCad
# ---------------------------------------------------------------------------


def build_labelled_quickstart(root):
    """The README blink circuit built with net labels (adds, label-pin)."""
    p = os.path.join(root, "blink")
    kce(["project", "new", p, "--name", "blink"])
    kce(["lib", "init-global-tables"], cwd=p)
    kce(["sch", "add", "Device:LED", "--ref", "D1", "--at", "100,60", "--value", "RED",
         "--footprint", "LED_SMD:LED_0805_2012Metric"], cwd=p)
    kce(["sch", "add", "Device:R", "--ref", "R1", "--at", "80,60", "--value", "330",
         "--footprint", "Resistor_SMD:R_0805_2012Metric"], cwd=p)
    kce(["sch", "add", "Connector_Generic:Conn_01x02", "--ref", "J1", "--at", "60,60", "--value", "PWR",
         "--footprint", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"], cwd=p)
    for pin, name in (("J1:1", "VCC"), ("R1:1", "VCC"), ("R1:2", "LED_A"), ("D1:2", "LED_A"),
                      ("D1:1", "GND"), ("J1:2", "GND")):
        kce(["sch", "label-pin", pin, name], cwd=p)
    return p


@pytest.fixture(scope="module")
def verify_master():
    import tempfile
    d = tempfile.mkdtemp(prefix="kce-intent-master-")
    yield build_labelled_quickstart(d)
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def verified_project(verify_master, tmp_dir):
    dst = os.path.join(tmp_dir, "blink")
    shutil.copytree(verify_master, dst, ignore=shutil.ignore_patterns(STATE_DIR))
    return dst


@pytest.mark.e2e
class TestVerify:
    def test_optional_library_check_has_a_strict_design_gate(self, verified_project):
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        data["components"][1]["lib_id"] = "Device:R_Small"
        path = intent_file(verified_project, data)
        assert kcej(["sch", "verify", "--expect", path, "--strict"], cwd=verified_project)["match"]
        result = kcej(["sch", "verify", "--expect", path, "--check-lib-id"], cwd=verified_project)
        assert result["symbol_mismatches"] == [{"ref": "R1", "expected": "Device:R_Small", "actual": "Device:R"}]
        r, err = kce_err(["sch", "verify", "--expect", path, "--check-lib-id", "--strict"], cwd=verified_project)
        assert r.returncode == 3 and err["category"] == "design" and "symbol mismatches" in err["error"]

    @pytest.mark.smoke
    def test_matching_schematic_matches_and_reports_renames(self, verified_project):
        p = verified_project
        path = intent_file(p)
        data = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert data["match"] is True
        assert data["missing_components"] == [] and data["extra_components"] == []
        assert data["field_mismatches"] == [] and data["merged"] == []
        assert [r["name"] for r in data["renamed"]] == ["GND", "LED_A", "VCC"]
        assert data["renamed"][0]["actual"] == "/GND"
        kce(["sch", "verify", "--expect", path, "--strict"], cwd=p)  # exits 0

    def test_verify_output_is_stable(self, verified_project):
        p = verified_project
        path = intent_file(p)
        first = kce(["--json", "sch", "verify", "--expect", path], cwd=p).stdout
        assert kce(["--json", "sch", "verify", "--expect", path], cwd=p).stdout == first

    @pytest.mark.smoke
    def test_merge_is_reported_and_strict_exits_design(self, verified_project):
        p = verified_project
        path = intent_file(p)
        kce(["sch", "connect", "R1:1", "R1:2", "--allow-merge"], cwd=p)
        data = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert data["match"] is False
        assert len(data["merged"]) == 1
        assert data["merged"][0]["nets"] == ["LED_A", "VCC"]
        assert {"R1:1", "R1:2"} <= set(data["merged"][0]["pins"])
        r, err = kce_err(["sch", "verify", "--expect", path, "--strict"], cwd=p)
        assert err["type"] == "DesignMismatch"
        assert r.returncode == 3
        assert err["category"] == "design" and err["type"] == "DesignMismatch"
        assert "merged" in err["error"]
        assert err["hint"] == f"{COMMAND} sch apply {path}"

    def test_component_and_field_differences(self, verified_project):
        p = verified_project
        data = json.loads(json.dumps(QUICKSTART_INTENT))
        data["components"][1]["value"] = "470"
        data["components"].append({"ref": "R9", "lib_id": "Device:R", "at": [140, 60]})
        path = intent_file(p, data, name="changed.json")
        out = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert out["missing_components"] == ["R9"]
        assert out["field_mismatches"] == [{"ref": "R1", "field": "value", "expected": "470", "actual": "330"}]

    def test_help_and_bad_intent_is_an_input_error(self, verified_project, tmp_dir):
        p = verified_project
        assert "Intent file to compare" in kce(["sch", "verify", "--help"]).stdout
        bad = os.path.join(tmp_dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"version": 1, "components": [{"ref": "R1"}], "nets": []}')
        r, err = kce_err(["sch", "verify", "--expect", bad], cwd=p)
        assert r.returncode == 2 and err["category"] == "input"
        assert "components[0].lib_id" in err["error"]


# ---------------------------------------------------------------------------
# Free-grid placement (unit)
# ---------------------------------------------------------------------------


class TestPlacementGrid:
    def test_grid_is_deterministic_and_skips_occupied_points(self):
        first = intent_mod._free_position([])
        assert first == (25.4, 25.4)
        second = intent_mod._free_position([first])
        assert second == (50.8, 25.4)
        assert intent_mod._free_position([first, second]) == (76.2, 25.4)
        assert intent_mod._free_position([(0, 0)]) == first


# ---------------------------------------------------------------------------
# sch apply against real KiCad
# ---------------------------------------------------------------------------


def empty_project(root, name="board"):
    p = os.path.join(root, name)
    kce(["project", "new", p, "--name", name])
    kce(["lib", "init-global-tables"], cwd=p)
    return p


@pytest.mark.e2e
class TestApply:
    @pytest.mark.smoke
    def test_quickstart_apply_verify_is_idempotent_and_merge_is_reported(self, tmp_dir):
        p = empty_project(tmp_dir)
        path = intent_file(p)
        data = kcej(["sch", "apply", path], cwd=p)
        assert [c["ref"] for c in data["placed"]] == ["J1", "R1", "D1"]
        assert data["match"] is True and data["merged"] == []
        assert kcej(["sch", "verify", "--expect", path], cwd=p)["match"] is True

        sch = os.path.join(p, "board.kicad_sch")
        with open(sch, "rb") as f:
            before = f.read()
        again = kcej(["sch", "apply", path], cwd=p)
        assert again["placed"] == [] and again["labels_added"] == [] and again["changed_files"] == []
        with open(sch, "rb") as f:
            assert f.read() == before  # applying the same file twice changes no byte

        # --dry-run reports the edit but writes nothing
        changed = json.loads(json.dumps(QUICKSTART_INTENT))
        changed["components"][1]["value"] = "470"
        dry = intent_file(p, changed, name="dry.json")
        out = kcej(["--dry-run", "sch", "apply", dry], cwd=p)
        assert out["updated"] == [{"ref": "R1", "fields": ["value"]}]
        with open(sch, "rb") as f:
            assert f.read() == before

        # a bad pin fails as an input error and leaves the file untouched
        bad = json.loads(json.dumps(QUICKSTART_INTENT))
        bad["nets"][0]["pins"][0] = "J1:99"
        r, err = kce_err(["sch", "apply", intent_file(p, bad, name="bad.json")], cwd=p)
        assert r.returncode == 2 and err["category"] == "input"
        with open(sch, "rb") as f:
            assert f.read() == before

        # brief e2e: change one connection behind the guard, then verify reports the merge
        kce(["sch", "connect", "R1:1", "R1:2", "--allow-merge"], cwd=p)
        after = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert after["match"] is False
        assert [m["nets"] for m in after["merged"]] == [["LED_A", "VCC"]]
        r, err = kce_err(["sch", "verify", "--expect", path, "--strict"], cwd=p)
        assert r.returncode == 3 and err["category"] == "design"

    def test_apply_lists_extras_and_prune_removes_them(self, tmp_dir):
        p = empty_project(tmp_dir, "prune")
        path = intent_file(p)
        kce(["sch", "apply", path], cwd=p)
        kce(["sch", "add", "Device:R", "--ref", "R9", "--at", "140,60"], cwd=p)
        kce(["sch", "label", "JUNK", "--at", "160,60"], cwd=p)

        listed = kcej(["sch", "apply", path], cwd=p)
        assert listed["extras"]["symbols"] == [{"ref": "R9", "sheet": "/"}]
        assert [x["text"] for x in listed["extras"]["labels"]] == ["JUNK"]
        assert listed["pruned"]["symbols"] == []

        pruned = kcej(["sch", "apply", path, "--prune"], cwd=p)
        assert pruned["pruned"]["symbols"] == [{"ref": "R9", "sheet": "/"}]
        assert [x["text"] for x in pruned["pruned"]["labels"]] == ["JUNK"]
        assert pruned["match"] is True
        assert "R9" not in [s["reference"] for s in kcej(["sch", "symbols"], cwd=p)]

    def test_apply_power_flags_only_where_erc_needs_one(self, tmp_dir):
        p = empty_project(tmp_dir, "power")
        data = {
            "version": 1,
            "components": [
                {"ref": "J1", "lib_id": "Connector_Generic:Conn_01x02", "value": "IN"},
                {"ref": "U1", "lib_id": "Regulator_Linear:L7805", "value": "5V"},
                {"ref": "R1", "lib_id": "Device:R", "value": "1k"},
            ],
            "nets": [
                {"name": "VIN", "pins": ["J1:1", "U1:1"], "kind": "global"},
                {"name": "GND", "pins": ["J1:2", "U1:2", "R1:2"], "kind": "global"},
                {"name": "OUT", "pins": ["U1:3", "R1:1"], "kind": "global"},
            ],
        }
        path = intent_file(p, data, name="power.json")
        out = kcej(["sch", "apply", path, "--power-flags"], cwd=p)
        assert [f["net"] for f in out["power_flags"]] == ["VIN", "GND"]  # OUT is driven
        assert out["match"] is True
        assert [f["ref"] for f in out["placed"]] == ["J1", "U1", "R1"]
        assert out["placed"][0]["at"] == [25.4, 25.4]  # deterministic free grid
        again = kcej(["sch", "apply", path, "--power-flags"], cwd=p)
        assert again["power_flags"] == [] and again["changed_files"] == []

    def test_apply_help(self):
        out = kce(["sch", "apply", "--help"]).stdout
        assert "PWR_FLAG" in out and "--prune" in out and "--patch" in out
        assert "--subset" in kce(["sch", "verify", "--help"]).stdout

    @pytest.mark.smoke
    def test_no_connect_and_power_flags_are_idempotent_and_erc_is_clean(self, tmp_dir):
        p = empty_project(tmp_dir, "flags")
        data = {
            "version": 1,
            "components": [
                {"ref": "J1", "lib_id": "Connector_Generic:Conn_01x04", "value": "HDR", "at": [60, 60]},
                {"ref": "R1", "lib_id": "Device:R", "value": "1k", "at": [90, 60]},
            ],
            "nets": [
                {"name": "VCC", "pins": ["J1:1", "R1:1"], "kind": "global"},
                {"name": "GND", "pins": ["J1:2", "R1:2"], "kind": "global"},
            ],
            "no_connect": ["J1:3", "J1:4"],
            "power_flags": [{"pin": "J1:1"}, {"pin": "J1:2"}],
        }
        path = intent_file(p, data, name="flags.json")
        out = kcej(["sch", "apply", path], cwd=p)
        assert out["match"] is True and out["changes"] == 10
        assert [x["pin"] for x in out["no_connects_added"]] == ["J1:3", "J1:4"]
        assert [f["pin"] for f in out["power_flags"]] == ["J1:1", "J1:2"]
        assert out["power_flags_missing"] == [] and out["no_connects_missing"] == []

        sch = os.path.join(p, "flags.kicad_sch")
        with open(sch, "rb") as f:
            before = f.read()
        again = kcej(["sch", "apply", path], cwd=p)
        assert again["changes"] == 0 and again["changed_files"] == []
        assert again["no_connects_added"] == [] and again["power_flags"] == []
        with open(sch, "rb") as f:
            assert f.read() == before

        # ERC sees no unconnected pin and no undriven power input
        erc = kcej(["check", "erc"], cwd=p)
        assert erc["errors"] == 0, erc["by_type"]

        # remove the evidence: verify names what is missing, apply restores it
        flags = [s["reference"] for s in kcej(["sch", "symbols"], cwd=p)
                 if s["reference"].startswith("#FLG")]
        kce(["sch", "delete", flags[0]], cwd=p)
        nc_uuid = kcej(["sch", "items", "--type", "no_connect"], cwd=p)[0]["uuid"]
        kce(["sch", "delete-item", nc_uuid], cwd=p)
        missing = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert missing["match"] is False
        assert missing["no_connects_missing"] == ["J1:3"]
        assert [f["pin"] for f in missing["power_flags_missing"]] == ["J1:1"]
        repaired = kcej(["sch", "apply", path], cwd=p)
        assert repaired["match"] is True
        assert [x["pin"] for x in repaired["no_connects_added"]] == ["J1:3"]
        assert [f["pin"] for f in repaired["power_flags"]] == ["J1:1"]

    def test_patch_never_deletes_and_subset_ignores_unlisted_parts(self, tmp_dir):
        p = empty_project(tmp_dir, "eco")
        kce(["sch", "apply", intent_file(p)], cwd=p)
        kce(["sch", "add", "Device:R", "--ref", "R9", "--at", "140,60"], cwd=p)
        eco = {"version": 1,
               "components": [{"ref": "C10", "lib_id": "Device:C", "value": "100nF", "at": [120, 60]}],
               "nets": [], "remove": ["R9"]}
        path = intent_file(p, eco, name="eco.json")
        out = kcej(["sch", "apply", path, "--patch"], cwd=p)
        assert [c["ref"] for c in out["placed"]] == ["C10"]
        assert out["removed"] == [] and out["remove_present"] == ["R9"]
        assert "R9" in [s["reference"] for s in kcej(["sch", "symbols"], cwd=p)]
        assert [s["ref"] for s in out["extras"]["symbols"]] == ["D1", "J1", "R1"]
        assert "R9" not in [s["ref"] for s in out["extras"]["symbols"]]  # a listed removal is not an extra

        sub = kcej(["sch", "verify", "--expect", path, "--subset"], cwd=p)
        assert sub["extra_components"] == [] and sub["extra_nets"] == []
        assert sub["remove_present"] == ["R9"]
        full = kcej(["sch", "verify", "--expect", path], cwd=p)
        assert full["extra_components"] == ["D1", "J1", "R1"]

        r, err = kce_err(["sch", "apply", path, "--patch", "--prune"], cwd=p)
        assert r.returncode == 2 and err["category"] == "input"
        assert "--prune" in err["error"]

        done = kcej(["sch", "apply", path], cwd=p)
        assert done["removed"] == [{"ref": "R9", "sheet": "/"}]
        assert done["remove_present"] == []
        assert "R9" not in [s["reference"] for s in kcej(["sch", "symbols"], cwd=p)]
