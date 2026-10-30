"""Unit tests (synthetic data, no KiCad installation needed)."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

from kicad_cli_extended import APP_NAME, COMMAND, ENV_PREFIX, STATE_DIR, __version__, env
from kicad_cli_extended import skill as skill_mod
from kicad_cli_extended.core import bundles, checks, fab, jobset, kicad_cli_cmds, library, project, schematic, sexpr
from kicad_cli_extended.core import pcb
from kicad_cli_extended.core import diff as diff_mod
from kicad_cli_extended.core.session import Session, _locked_save_json
from kicad_cli_extended.core.sexpr import Q, S
from kicad_cli_extended.utils import paths, verify


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wr(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def empty_global_lib_tables(tmp_path, monkeypatch):
    config = tmp_path / "kicad-config"
    version = config / "10.0"
    version.mkdir(parents=True)
    for kind in ("sym", "fp"):
        (version / f"{kind}-lib-table").write_text(
            f"({kind}_lib_table (version 7))\n", encoding="utf-8")
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(config))


def make_project(d, name="p"):
    pro = os.path.join(d, name + ".kicad_pro")
    with open(pro, "w", encoding="utf-8") as f:
        json.dump({"meta": {"filename": name + ".kicad_pro", "version": 1}, "net_settings": {"classes": []},
                   "text_variables": {}}, f)
    return project.KicadProject(pro)


def session_for(d, name="p"):
    s = Session()
    s.set_project(make_project(d, name))
    return s


# ═══════════════════════════════ sexpr ═══════════════════════════════

class TestSexpr:
    def test_parse_atoms_and_strings(self):
        t = sexpr.loads('(a 1 -2.5 yes "hello world" "q\\"uote")')
        assert t.tag == "a"
        assert [str(x) for x in t[1:]] == ["1", "-2.5", "yes", "hello world", 'q"uote']
        assert isinstance(t[4], sexpr.QStr)

    def test_raw_text_preserved(self):
        t = sexpr.loads('(a 1.270 "x\\\\y")')
        assert sexpr.compact(t) == '(a 1.270 "x\\\\y")'

    def test_quote_rules(self):
        assert sexpr.quote('a"b\\c\nd\re') == '"a\\"b\\\\c\\nd\\re"'
        assert str(sexpr.loads('(x "a\\nb")')[1]) == "a\nb"

    def test_new_string_is_requoted(self):
        t = S("p", Q('he said "hi"'))
        assert sexpr.compact(t) == '(p "he said \\"hi\\"")'

    def test_prettify_nesting(self):
        t = sexpr.loads("(root (a 1) (b (c 2) (d 3)))")
        assert sexpr.dumps(t) == "(root\n\t(a 1)\n\t(b\n\t\t(c 2)\n\t\t(d 3)\n\t)\n)\n"

    def test_prettify_xy_run(self):
        pts = " ".join(f"(xy {i} {i})" for i in range(3))
        out = sexpr.dumps(sexpr.loads(f"(r (pts {pts}))"))
        assert "\t\t(xy 0 0) (xy 1 1) (xy 2 2)\n" in out

    def test_prettify_xy_wraps_at_col_99(self):
        pts = " ".join(f"(xy {100 + i}.123456 {200 + i}.654321)" for i in range(12))
        out = sexpr.dumps(sexpr.loads(f"(r (pts {pts}))"))
        lines = [l for l in out.splitlines() if "(xy" in l]
        assert len(lines) > 1
        assert all(len(l.expandtabs(1)) < 99 + 40 for l in lines)

    def test_prettify_token_wrap_72(self):
        toks = " ".join(f'"tok{i:03d}"' for i in range(30))
        out = sexpr.dumps(sexpr.loads(f"(r (members {toks}))"))
        assert out.count("\n") > 4

    def test_library_table_mode(self):
        txt = '(sym_lib_table\n\t(version 7)\n\t(lib (name "a") (type "KiCad") (uri "x") (options "") (descr ""))\n)\n'
        assert sexpr.dumps(sexpr.loads(txt), "library_table") == txt

    def test_compact_text_mode_short_forms(self):
        out = sexpr.dumps(sexpr.loads("(r (effects (font (size 1 1))))"), "compact_text")
        assert "(font (size 1 1))" in out

    def test_roundtrip_synthetic(self):
        txt = ('(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(lib_symbols)\n'
               '\t(wire\n\t\t(pts\n\t\t\t(xy 1 2) (xy 3 4)\n\t\t)\n\t\t(uuid "u")\n\t)\n)\n')
        assert sexpr.dumps(sexpr.loads(txt)) == txt

    def test_fmt_num(self):
        assert sexpr.fmt_num(1.0) == "1"
        assert sexpr.fmt_num(1.27) == "1.27"
        assert sexpr.fmt_num(-0.0000001) == "0"
        assert sexpr.fmt_num(3) == "3"
        assert sexpr.fmt_num(True) == "yes"

    def test_builders_atomize(self):
        n = S("at", 1.5, 2, 0, "yes", "needs quote")
        assert sexpr.compact(n) == '(at 1.5 2 0 yes "needs quote")'

    def test_helpers_find_value_set_property(self):
        t = sexpr.loads('(s (at 1 2 90) (property "Reference" "R1") (property "Value" "10k"))')
        assert t.fvalue("at", index=2) == 90
        assert t.get_property("Value") == "10k"
        t.set("at", 5, 6, 0)
        assert t.value("at") == "5"
        assert t.property("Missing") is None

    def test_query_language(self):
        t = sexpr.loads('(r (a (b 1)) (a (b 2)) (c (a (b 3))) (p "X" 1) (p "Y" 2))')
        assert len(sexpr.query(t, "a")) == 2
        assert sexpr.query(t, "a[1]/b")[0].value() == "2"
        assert sexpr.query(t, "p[Y]")[0][2] == "2"
        assert len(sexpr.query(t, "//b")) == 3
        assert len(sexpr.query(t, "*")) == 5

    def test_loads_many_with_comments(self):
        nodes = sexpr.loads_many('# header\n(version 1)\n(rule "x" # c\n (condition "A.Name == \'#1\'"))\n')
        assert [n.tag for n in nodes] == ["version", "rule"]
        assert nodes[1].value("condition") == "A.Name == '#1'"

    @pytest.mark.parametrize("bad", ["(a (b)", "a)", '(a "x)', "", "(a)(b)"])
    def test_errors(self, bad):
        with pytest.raises(sexpr.SexprError):
            sexpr.loads(bad)

    def test_remove_and_walk(self):
        t = sexpr.loads("(r (x 1) (y (x 2)) (x 3))")
        assert t.remove_all("x") == 2
        assert len(list(sexpr.walk(t))) == 3

    def test_mode_for_path(self):
        assert sexpr.mode_for_path("/a/fp-lib-table") == "library_table"
        assert sexpr.mode_for_path("/a/b.kicad_pcb") == "normal"


# ═══════════════════════════════ schematic ═══════════════════════════════

def sym_node(x=100, y=50, angle=0, mirror=None):
    n = S("symbol", S("lib_id", Q("L:X")), S("at", x, y, angle))
    if mirror:
        n.append(S("mirror", mirror))
    return n


class TestSchematicGeometry:
    @pytest.mark.parametrize("angle,mirror,expected", [
        (0, None, (102.54, 50.0)), (90, None, (100.0, 47.46)), (180, None, (97.46, 50.0)),
        (270, None, (100.0, 52.54)), (0, "y", (97.46, 50.0)), (0, "x", (102.54, 50.0)),
        (90, "x", (100.0, 52.54)), (90, "y", (100.0, 47.46)),
    ])
    def test_transform_point_on_x_axis(self, angle, mirror, expected):
        # lib point (2.54, 0) — to the right of the origin
        assert schematic.lib_to_sch(sym_node(angle=angle, mirror=mirror), 2.54, 0) == expected

    def test_lib_y_up(self):
        assert schematic.lib_to_sch(sym_node(), 0, 3.81) == (100.0, 46.19)

    def test_bad_angle(self):
        with pytest.raises(ValueError):
            schematic.symbol_transform(45, None)

    def test_pin_outward(self):
        pin_top = {"orientation": 270}  # R pin 1: at top, body below
        assert schematic.pin_outward(sym_node(), pin_top) == (0, -1)
        assert schematic.pin_outward(sym_node(angle=90), pin_top) == (-1, 0)
        assert schematic.pin_outward(sym_node(), {"orientation": 0}) == (-1, 0)

    def test_snap(self):
        assert schematic.snap(10.0) == 10.16
        assert schematic.snap(1.27 * 3 + 0.1) == 3.81

    def test_route_points(self):
        assert schematic.route_points((0, 0), (10, 5)) == [(0, 0), (10, 0), (10, 5)]
        assert schematic.route_points((0, 0), (10, 5), "vh") == [(0, 0), (0, 5), (10, 5)]
        assert schematic.route_points((0, 0), (10, 5), "direct") == [(0, 0), (10, 5)]
        assert schematic.route_points((0, 0), (0, 5)) == [(0, 0), (0, 5)]

    def test_parse_helpers(self):
        assert schematic.parse_pin_ref("U1:VCC") == ("U1", "VCC")
        with pytest.raises(ValueError):
            schematic.parse_pin_ref("U1")
        assert schematic.parse_point("1.5,-2") == (1.5, -2.0)
        with pytest.raises(ValueError):
            schematic.parse_point("abc")

    @pytest.mark.parametrize("ref,prefix,num", [("R12", "R", 12), ("U?", "U", None), ("#PWR03", "#PWR", 3),
                                                ("SW1", "SW", 1)])
    def test_ref_parsing(self, ref, prefix, num):
        assert schematic.ref_prefix(ref) == prefix
        assert schematic.ref_number(ref) == num


def sch_tree_with(symbols):
    t = project.empty_schematic_tree()
    ru = t.value("uuid")
    for ref, x, y, unit in symbols:
        s = S("symbol", S("lib_id", Q("Device:R")), S("at", x, y, 0), S("unit", unit), S("uuid", Q(project.new_uuid())),
              S("property", Q("Reference"), Q(ref), S("at", x, y, 0)),
              S("instances", S("project", Q("p"), S("path", Q("/" + ru), S("reference", Q(ref)), S("unit", unit)))))
        schematic._insert_item(t, s)
    return t


def symbol_tree_with_pins(specs, ref="J1"):
    """Empty schematic with one placed ``TST:J1`` symbol whose lib cache has the given pins."""
    t = project.empty_schematic_tree()
    t.find("lib_symbols").append(library.make_box_symbol("TST:J1", library.parse_pin_specs(specs)))
    s = S("symbol", S("lib_id", Q("TST:J1")), S("at", 50, 50, 0), S("unit", 1),
          S("uuid", Q(project.new_uuid())),
          S("property", Q("Reference"), Q(ref), S("at", 55, 50, 0)),
          S("property", Q("Value"), Q("J1"), S("at", 55, 52, 0)))
    schematic._insert_item(t, s)
    return t, s


class TestSchematicEdit:
    def test_replacement_rewires_endpoints_and_labels_simultaneously(self):
        t, sym = symbol_tree_with_pins(["1:A:passive:left", "2:B:passive:right"])
        pins = {p["number"]: p for p in schematic.symbol_pins(t, sym)}
        for num, pin in pins.items():
            schematic.add_label(t, "N" + num, pin["x"], pin["y"])
        p = pins["1"]
        schematic.add_wire(t, [(p["x"] - 5.08, p["y"]), (p["x"], p["y"])])
        t.find("lib_symbols").append(library.make_box_symbol("TST:NEW",
            library.parse_pin_specs(["1:A:passive:right", "2:B:passive:left"])))
        report = schematic.replace_lib_symbol(t, None, "J1", "TST:NEW", rewire=True)
        assert report["moved_wires"] and report["dangling_wires"] == []
        restored = sexpr.loads(sexpr.dumps(t))
        for num in ("1", "2"):
            pin = schematic.find_pin(restored, "J1", num)[1]
            label = next(l for l in restored.find_all("label") if str(l[1]) == "N" + num)
            assert [float(x) for x in label.find("at")[1:3]] == [pin["x"], pin["y"]]

    def test_unconnected_pins_counts_every_connection(self):
        t, s = symbol_tree_with_pins(["1:P1", "2:P2", "3:P3", "4:P4", "5:P5"])
        pins = {p["number"]: p for p in schematic.symbol_pins(t, s)}
        a, b, c, d = (pins[n] for n in ("1", "2", "3", "4"))
        schematic.add_wire(t, [(a["x"], a["y"]), (a["x"] + 2.54, a["y"])])
        schematic.add_label(t, "N2", b["x"], b["y"])
        schematic.add_no_connect(t, c["x"], c["y"])
        schematic.add_wire(t, [(d["x"] - 2.54, d["y"]), (d["x"] + 2.54, d["y"])])  # through the pin
        assert [p["pin"] for p in schematic.unconnected_pins(t)] == ["5"]
        assert [p["pin"] for p in schematic.unconnected_pins(t, "J1")] == ["5"]
        with pytest.raises(KeyError):
            schematic.unconnected_pins(t, "J9")

    def test_unconnected_pins_skips_hidden_pins(self):
        t, s = symbol_tree_with_pins(["1:P1", "2:P2"])
        for pn in t.find("lib_symbols").find("symbol").deep_find_all("pin"):
            if pn.value("number") == "2":
                pn.append(S("hide", "yes"))
        assert [p["pin"] for p in schematic.unconnected_pins(t)] == ["1"]

    def test_annotate_unannotated(self):
        t = sch_tree_with([("R?", 50, 10, 1), ("R1", 10, 10, 1), ("R?", 20, 10, 1)])
        inst = [{"file": "/x.kicad_sch", "path": "/" + t.value("uuid")}]
        ch = schematic.annotate({"/x.kicad_sch": t}, inst)
        assert sorted(c["new"] for c in ch) == ["R2", "R3"]
        refs = [schematic.sym_ref(s) for s in schematic.placed_symbols(t)]
        assert refs == ["R3", "R1", "R2"]  # ordered by X
        assert schematic.instance_references(schematic.placed_symbols(t)[0])["/" + t.value("uuid")] == "R3"

    def test_annotate_all_and_multiunit(self):
        t = sch_tree_with([("U5", 30, 10, 1), ("U5", 40, 10, 2), ("U9", 10, 10, 1)])
        inst = [{"file": "/x", "path": "/" + t.value("uuid")}]
        schematic.annotate({"/x": t}, inst, mode="all")
        refs = [schematic.sym_ref(s) for s in schematic.placed_symbols(t)]
        assert refs == ["U2", "U2", "U1"]

    def test_annotate_sheet_offset(self):
        t1 = sch_tree_with([("R?", 10, 10, 1)])
        t2 = sch_tree_with([("R?", 10, 10, 1)])
        inst = [{"file": "/a", "path": "/" + t1.value("uuid")}, {"file": "/b", "path": "/" + t2.value("uuid")}]
        schematic.annotate({"/a": t1, "/b": t2}, inst, sheet_offset=100)
        assert schematic.sym_ref(schematic.placed_symbols(t2)[0]) == "R101"

    def test_clear_annotation(self):
        t = sch_tree_with([("R1", 10, 10, 1)])
        assert schematic.clear_annotation({"/a": t}) == 1
        assert schematic.sym_ref(schematic.placed_symbols(t)[0]) == "R?"

    def test_labels_and_items(self):
        t = project.empty_schematic_tree()
        schematic.add_label(t, "VCC", 10, 10, kind="global", shape="input")
        schematic.add_label(t, "N1", 20, 10)
        schematic.add_no_connect(t, 5, 5)
        items = schematic.list_items(t)
        assert {i["type"] for i in items} == {"global_label", "label", "no_connect"}
        gl = t.find("global_label")
        assert gl.value("shape") == "input" and gl.property("Intersheetrefs") is not None
        with pytest.raises(ValueError):
            schematic.add_label(t, "x", 0, 0, kind="nope")
        # items are inserted before sheet_instances
        tags = [c.tag for c in t.children()]
        assert tags.index("label") < tags.index("sheet_instances")

    def test_auto_junction_t(self):
        t = project.empty_schematic_tree()
        schematic.add_wire(t, [(0, 0), (10, 0)])
        schematic.add_wire(t, [(5, 0), (5, 5)])
        assert schematic.auto_junctions(t) == 1
        assert t.find("junction").fvalue("at") == 5

    def test_items_at_and_delete(self):
        t = project.empty_schematic_tree()
        w = schematic.add_wire(t, [(0, 0), (10, 0)])[0]
        found = schematic.items_at(t, 5, 0)
        assert found and found[0]["type"] == "wire"
        assert schematic.delete_item(t, w.value("uuid")) == "wire"
        assert not t.find_all("wire")
        with pytest.raises(KeyError):
            schematic.delete_item(t, "nope")

    def test_title_block_order(self):
        t = project.empty_schematic_tree()
        tb = schematic.set_title_block(t, {"rev": "B", "title": "T"}, {2: "c2", 1: "c1"})
        assert [c.tag for c in tb.children()] == ["title", "rev", "comment", "comment"]
        assert tb.find_all("comment")[0].value() == "1"
        with pytest.raises(KeyError):
            schematic.set_title_block(t, {"bogus": "x"}, {})

    def test_fields(self):
        t = sch_tree_with([("R1", 10, 10, 1)])
        s = schematic.placed_symbols(t)[0]
        schematic.set_field(s, "MPN", "RC0603", hide=True)
        assert s.get_property("MPN") == "RC0603" and schematic._prop_hidden(s.property("MPN"))
        schematic.set_field(s, "Reference", "R7")
        assert schematic.instance_references(s)["/" + t.value("uuid")] == "R7"
        with pytest.raises(ValueError):
            schematic.remove_field(s, "Value")
        schematic.remove_field(s, "MPN")
        assert s.property("MPN") is None

    def test_move_and_rotate_fields_follow(self):
        s = S("symbol", S("at", 10, 10, 0), S("property", Q("Reference"), Q("R1"), S("at", 12.54, 10, 0)))
        schematic.move_symbol(s, dx=2.54, dy=0)
        assert s.find("at").fvalue() == 12.7 and s.property("Reference").fvalue("at") == 15.24
        schematic.rotate_symbol(s, by=90)
        assert s.find("at").fvalue(index=2) == 90
        assert s.property("Reference").fvalue("at", index=1) == pytest.approx(10.16 - 2.54)


# ═══════════════════════════════ session ═══════════════════════════════

class TestSession:
    def test_locked_save_json_truncates(self, tmp_dir):
        p = os.path.join(tmp_dir, "s.json")
        _locked_save_json(p, {"a": "x" * 100})
        _locked_save_json(p, {"a": 1})
        assert json.loads(rd(p)) == {"a": 1}

    def test_snapshot_undo_redo(self, tmp_dir):
        s = session_for(tmp_dir)
        f = os.path.join(tmp_dir, "x.kicad_dru")
        wr(f, "(version 1)\n")
        s.snapshot("edit", [f])
        s.doc(f, "text").set_text("(version 1)\n(rule a)\n")
        s.save_session()
        assert "rule" in rd(f)
        s.undo()
        s.save_session()
        assert rd(f) == "(version 1)\n"
        s.redo()
        s.save_session()
        assert "rule" in rd(f)

    def test_history_persists_across_sessions(self, tmp_dir):
        s = session_for(tmp_dir)
        f = os.path.join(tmp_dir, "y.kicad_dru")
        wr(f, "A\n")
        s.snapshot("one", [f])
        s.doc(f, "text").set_text("B\n")
        s.save_session()
        s2 = Session()
        s2.set_project(project.KicadProject(s.project.pro_path))
        assert [h["description"] for h in s2.list_history()] == ["one"]
        s2.undo()
        s2.save_session()
        assert rd(f) == "A\n"

    def test_undo_of_created_file_deletes_it(self, tmp_dir):
        s = session_for(tmp_dir)
        f = os.path.join(tmp_dir, "new.kicad_dru")
        s.snapshot("create", [f])
        s.doc(f, "text").set_text("X\n")
        s.save_session()
        assert os.path.exists(f)
        s.undo()
        s.save_session()
        assert not os.path.exists(f)

    def test_blob_dedup(self, tmp_dir):
        s = session_for(tmp_dir)
        assert s._put_blob("same") == s._put_blob("same")
        assert len(os.listdir(os.path.join(s.state_dir, "blobs"))) == 1
        p = os.path.join(s.state_dir, "blobs", os.listdir(os.path.join(s.state_dir, "blobs"))[0])
        assert gzip.open(p).read() == b"same"

    def test_validator_failure_rolls_back(self, tmp_dir):
        s = session_for(tmp_dir)
        f1, f2 = os.path.join(tmp_dir, "a.kicad_dru"), os.path.join(tmp_dir, "b.kicad_dru")
        wr(f1, "1\n")
        s.snapshot("two files", [f1, f2])
        s.doc(f1, "text").set_text("changed\n")
        s.doc(f2, "text").set_text("created\n")

        def bad(paths):
            raise RuntimeError("kicad says no")
        with pytest.raises(RuntimeError, match="rolled back"):
            s.save_session(validator=bad)
        assert rd(f1) == "1\n" and not os.path.exists(f2)
        assert s.list_history() == []

    def test_discard_and_status(self, tmp_dir):
        s = session_for(tmp_dir)
        f = os.path.join(tmp_dir, "c.kicad_dru")
        s.snapshot("x", [f])
        s.doc(f, "text").set_text("x")
        st = s.status()
        assert st["modified"] and st["dirty_files"] == [f] and st["undo_depth"] == 1
        s.discard()
        assert not s.dirty_docs() and s._lock_fh is None

    def test_require_project(self):
        with pytest.raises(RuntimeError, match="--project"):
            Session().require_project()


# ═══════════════════════════════ project ═══════════════════════════════

class TestProject:
    def test_resolve_variants(self, tmp_dir):
        p = make_project(tmp_dir, "board")
        wr(os.path.join(tmp_dir, "board.kicad_sch"), "(kicad_sch)")
        assert project.resolve_project(tmp_dir).pro_path == p.pro_path
        assert project.resolve_project(os.path.join(tmp_dir, "board.kicad_sch")).pro_path == p.pro_path
        with pytest.raises(FileNotFoundError):
            project.resolve_project(os.path.join(tmp_dir, "nope.kicad_pro"))
        make_project(tmp_dir, "other")
        with pytest.raises(ValueError, match="Several"):
            project.resolve_project(tmp_dir)
        with pytest.raises(ValueError):
            project.resolve_project(os.path.join(tmp_dir, "x.txt"))

    def test_get_set_path(self):
        d = {"a": {"b": [1, {"c": 2}]}}
        assert project.get_path(d, "a.b.1.c") == 2
        project.set_path(d, "a.x.y", 5)
        assert d["a"]["x"]["y"] == 5
        with pytest.raises(KeyError):
            project.get_path(d, "a.zz")
        assert project.parse_value("0.2") == 0.2 and project.parse_value("abc") == "abc"
        assert project.parse_value("[1,2]") == [1, 2]

    def test_textvars(self, tmp_dir):
        s = session_for(tmp_dir)
        project.textvar_set(s, "REV", "B")
        assert project.textvar_list(s) == {"REV": "B"}
        project.textvar_delete(s, "REV")
        with pytest.raises(KeyError):
            project.textvar_delete(s, "REV")

    def test_netclasses(self, tmp_dir):
        s = session_for(tmp_dir)
        r = project.netclass_set(s, "PWR", {"track_width": "0.5", "clearance": 0.3})
        assert r["created"] and r["class"]["track_width"] == 0.5
        assert project.netclass_set(s, "PWR", {"via_diameter": 0.8})["created"] is False
        names = [c["name"] for c in project.netclass_list(s)["classes"]]
        assert names == ["Default", "PWR"]
        project.netclass_assign(s, "GND*", "PWR")
        assert project.netclass_list(s)["patterns"] == [{"netclass": "PWR", "pattern": "GND*"}]
        with pytest.raises(KeyError):
            project.netclass_assign(s, "X", "Nope")
        with pytest.raises(KeyError):
            project.netclass_set(s, "PWR", {"bogus": 1})
        with pytest.raises(ValueError):
            project.netclass_delete(s, "Default")
        project.netclass_delete(s, "PWR")
        assert project.netclass_list(s)["patterns"] == []

    def test_rules_and_severity_and_presets(self, tmp_dir):
        s = session_for(tmp_dir)
        project.rules_set(s, {"min_clearance": 0.15})
        assert project.rules_get(s)["rules"]["min_clearance"] == 0.15
        with pytest.raises(KeyError):
            project.rules_set(s, {"nope": 1})
        project.severity_set(s, "silk_overlap", "ignore")
        with pytest.raises(ValueError):
            project.severity_set(s, "x", "fatal")
        assert project.presets_add(s, "via", [0.8, 0.4])["list"][-1] == {"diameter": 0.8, "drill": 0.4}

    def test_custom_rules(self, tmp_dir):
        s = session_for(tmp_dir)
        project.dru_add_rule(s, "hv", ["clearance min 0.5mm"], condition="A.NetClass == 'HV'")
        project.dru_add_rule(s, "edge", ["(constraint edge_clearance (min 1mm))"], layer="outer")
        info = project.dru_read(s)
        assert [r["name"] for r in info["rules"]] == ["hv", "edge"]
        assert info["rules"][0]["constraints"] == ["(constraint clearance (min 0.5mm))"]
        project.dru_remove_rule(s, "hv")
        assert [r["name"] for r in project.dru_read(s)["rules"]] == ["edge"]

    def test_read_lib_table(self, tmp_dir):
        p = os.path.join(tmp_dir, "fp-lib-table")
        wr(p, '(fp_lib_table (version 7) (lib (name "X") (type "KiCad") (uri "${KIPRJMOD}/x.pretty") (options "") (descr "d")))')
        assert project.read_lib_table(p) == [{"name": "X", "type": "KiCad", "uri": "${KIPRJMOD}/x.pretty",
                                              "options": "", "descr": "d"}]


# ═══════════════════════════════ library ═══════════════════════════════

class TestLibrary:
    def test_pin_specs(self):
        pins = library.parse_pin_specs(["1:VCC:power_in:top,2:GND:power_in:bottom", "3:IN:input"])
        assert [p["number"] for p in pins] == ["1", "2", "3"] and pins[0]["side"] == "top"
        with pytest.raises(ValueError):
            library.parse_pin_specs(["1:A:bogus"])
        with pytest.raises(ValueError):
            library.parse_pin_specs(["1:A,1:B"])

    def test_box_symbol_pins_on_grid(self):
        pins = library.parse_pin_specs([f"{i}:P{i}" for i in range(1, 7)])
        sym = library.make_box_symbol("TST", pins)
        pl = library.pin_list(sym)
        assert len(pl) == 6
        for p in pl:
            assert round(p["x"] / 1.27, 6) % 1 == 0 and round(p["y"] / 1.27, 6) % 1 == 0
        assert {p["orientation"] for p in pl} == {0.0, 180.0}

    def test_flatten_extends(self):
        lib = sexpr.loads('(kicad_symbol_lib (symbol "BASE" (property "Reference" "U") (property "Value" "BASE")'
                          ' (symbol "BASE_1_1" (pin input line (at 0 0 0) (length 2.54) (name "A") (number "1"))))'
                          ' (symbol "CHILD" (extends "BASE") (property "Value" "CHILD") (property "MPN" "X1")))')
        f = library.flatten_symbol(lib, "CHILD")
        assert f.get_property("Value") == "CHILD" and f.get_property("Reference") == "U"
        assert f.get_property("MPN") == "X1" and f.find("extends") is None
        assert str(f.find_all("symbol")[0][1]) == "CHILD_1_1"
        with pytest.raises(KeyError):
            library.flatten_symbol(lib, "NOPE")

    @pytest.mark.parametrize("kind,count,first,last", [
        ("dip", 8, ("1", -3.81, -3.81), ("8", 3.81, -3.81)),
        ("soic", 8, ("1", -2.7, -1.905), ("8", 2.7, -1.905)),
        ("chip", 2, ("1", -0.75, 0), ("2", 0.75, 0)),
    ])
    def test_pad_generators(self, kind, count, first, last):
        pitch = {"dip": 2.54, "soic": 1.27, "chip": 1.5}[kind]
        pads = library.generate_pads(kind, count, pitch)
        assert len(pads) == count
        assert (pads[0]["number"], pads[0]["x"], pads[0]["y"]) == pytest.approx(first) or \
            (pads[0]["number"], pads[0]["x"], pads[0]["y"]) == first
        assert (pads[-1]["number"], pads[-1]["x"], pads[-1]["y"]) == last

    def test_header_and_bad_kind(self):
        pads = library.generate_pads("header", 6, 2.54, rows=2)
        assert [p["number"] for p in pads] == [str(i) for i in range(1, 7)] and pads[0]["shape"] == "rect"
        with pytest.raises(ValueError):
            library.generate_pads("dip", 7, 2.54)
        with pytest.raises(ValueError):
            library.generate_pads("bga", 4, 1)

    def test_make_footprint(self):
        fp = library.make_footprint("X", library.generate_pads("chip", 2, 1.6, pad_w=0.9, pad_h=1.0))
        assert fp.value("attr") == "smd" and len(fp.find_all("pad")) == 2
        layers = {g.value("layer") for g in fp.find_all("fp_rect")}
        assert {"F.CrtYd", "F.Fab", "F.SilkS"} <= layers
        with pytest.raises(ValueError):
            library.make_footprint("Y", [])

    def test_pad_roles(self):
        pads = [
            {"number": "1", "type": "smd", "layers": ["F.Cu", "F.Paste", "F.Mask"]},
            {"number": "2", "type": "smd", "layers": ["F.Cu", "F.Paste", "F.Mask"]},
            {"number": "", "type": "smd", "layers": ["F.Cu", "F.Mask"]},
            {"number": "", "type": "smd", "layers": ["F.Paste"]},
            {"number": "9", "type": "smd", "layers": ["F.Paste"]},
            {"number": "", "type": "np_thru_hole", "layers": ["*.Cu", "*.Mask"]},
        ]
        roles = library.pad_roles(pads)
        assert roles == {"signal": ["1", "2"], "thermal": [""], "paste_only": ["", "9"], "npth": [""]}

    def test_renumber_paste_only_to_the_containing_pad(self):
        fp = sexpr.loads(
            '(footprint "X"'
            ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste"))'
            ' (pad "9" smd rect (at 5 5) (size 2 2) (layers "F.Cu" "F.Paste"))'
            ' (pad "1" smd rect (at 5 5) (size 0.1 0.2) (layers "F.Paste"))'
            ' (pad "1" smd rect (at 9 9) (size 0.1 0.2) (layers "F.Paste")))')
        changes = library.renumber_paste_only(fp)
        assert changes == [{"from": "1", "to": "9"}, {"from": "1", "to": ""}]
        assert library.pad_roles(library._pad_records(fp))["paste_only"] == ["9", ""]
        assert library.renumber_paste_only(fp) == []

    def test_set_footprint_metadata(self):
        fp = sexpr.loads('(footprint "X" (layer "F.Cu")'
                         ' (property "Description" "" (at 0 0 0))'
                         ' (property "Datasheet" "" (at 0 0 0)))')
        library.set_footprint_metadata(fp, "A description", "tag1 tag2", "https://example.com/d.pdf")
        assert fp.value("descr") == "A description" and fp.value("tags") == "tag1 tag2"
        assert str(fp.property("Description")[2]) == "A description"
        assert str(fp.property("Datasheet")[2]) == "https://example.com/d.pdf"

    def test_library_footprint_model_set_replace_remove(self, tmp_dir):
        fp = library.make_footprint("X", library.generate_pads("chip", 2, 1.6, pad_w=0.9, pad_h=1.0))
        assert library.model_entries(None, fp) == []
        r = library.set_footprint_model(fp, "${KIPRJMOD}/3d/X.step", offset="1,2,3")
        assert r == {"index": 0, "created": True}
        models = library.model_entries(None, fp)
        assert models[0]["file"] == "${KIPRJMOD}/3d/X.step" and models[0]["offset"] == [1.0, 2.0, 3.0]
        assert models[0]["rotation"] == [0.0, 0.0, 0.0] and models[0]["scale"] == [1.0, 1.0, 1.0]
        # replacing keeps the offset that was not given again
        library.set_footprint_model(fp, "${KIPRJMOD}/3d/Y.step", rotation="0,0,90")
        models = library.model_entries(None, fp)
        assert len(models) == 1 and models[0]["file"].endswith("Y.step")
        assert models[0]["offset"] == [1.0, 2.0, 3.0] and models[0]["rotation"] == [0.0, 0.0, 90.0]
        assert library.set_footprint_model(fp, "${KIPRJMOD}/3d/Z.step", add=True) == {"index": 1, "created": True}
        assert [m["file"].rsplit("/", 1)[-1] for m in library.model_entries(None, fp)] == ["Y.step", "Z.step"]
        assert library.remove_footprint_model(fp) == {"index": 0, "removed": "${KIPRJMOD}/3d/Y.step"}
        assert library.remove_footprint_model(fp)["removed"].endswith("Z.step")
        with pytest.raises(KeyError):
            library.remove_footprint_model(fp)
        with pytest.raises(ValueError):
            library.set_footprint_model(fp, "${KIPRJMOD}/3d/Q.step", index=2)
        with pytest.raises(ValueError):
            library.set_footprint_model(fp, "${KIPRJMOD}/3d/Q.step", offset="1,2")

    def test_model_file_ref_copy_into_project(self, tmp_dir):
        pro = project.KicadProject(os.path.join(tmp_dir, "p.kicad_pro"))
        src = os.path.join(tmp_dir, "vendor.step")
        Path(src).write_text("ISO-10303-21;", encoding="utf-8")
        ref, dest = library.model_file_ref(pro, src)
        assert ref == "${KIPRJMOD}/vendor.step" and dest is None
        ref, dest = library.model_file_ref(pro, src, "3d")
        assert ref == "${KIPRJMOD}/3d/vendor.step"
        assert dest == os.path.join(tmp_dir, "3d", "vendor.step") and not os.path.exists(dest)
        outside = os.path.join(tempfile.gettempdir(), "kce-outside.step")
        Path(outside).write_text("ISO-10303-21;", encoding="utf-8")
        ref, dest = library.model_file_ref(pro, outside, "3d")
        assert ref == "${KIPRJMOD}/3d/kce-outside.step" and not os.path.exists(dest)
        with pytest.raises(FileNotFoundError):
            library.model_file_ref(pro, os.path.join(tmp_dir, "nope.step"), "3d")

    def _parts_project(self, tmp_dir):
        pro = make_project(tmp_dir, "partsproj")
        d = pro.dir
        pretty = os.path.join(d, "parts.pretty")
        os.makedirs(pretty)
        pads = [
            {"number": "1", "x": -0.75, "y": 0.5, "w": 0.5, "h": 0.4,
             "layers": ["F.Cu", "F.Paste", "F.Mask"]},
            {"number": "2", "x": 0.75, "y": 0.5, "w": 0.5, "h": 0.4,
             "layers": ["F.Cu", "F.Paste", "F.Mask"]},
            {"number": "9", "x": 0.0, "y": 0.0, "w": 0.7, "h": 1.2,
             "layers": ["F.Cu", "F.Paste", "F.Mask"]},
            {"number": "9", "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "layers": ["F.Paste"]},
        ]
        sexpr.dump(library.make_footprint("TDFN", pads), os.path.join(pretty, "TDFN.kicad_mod"))
        sym = S("symbol", Q("CHIP"), S("property", Q("Reference"), Q("U")),
                S("property", Q("Value"), Q("CHIP")), S("property", Q("Footprint"), Q("parts:TDFN")))
        sub = S("symbol", Q("CHIP_1_1"))
        for n in ("1", "2", "10"):
            sub.append(S("pin", "passive", "line", S("at", 0, 0, 0), S("length", 2.54),
                         S("name", Q(f"P{n}")), S("number", Q(n))))
        sym.append(sub)
        lib = library.new_symbol_library()
        library.add_symbol_to_lib(lib, sym)
        sexpr.dump(lib, os.path.join(d, "parts.kicad_sym"))
        wr(os.path.join(d, "fp-lib-table"),
           '(fp_lib_table (version 7) (lib (name "parts") (type "KiCad") '
           '(uri "${KIPRJMOD}/parts.pretty") (options "") (descr "")))')
        wr(os.path.join(d, "sym-lib-table"),
           '(sym_lib_table (version 7) (lib (name "parts") (type "KiCad") '
           '(uri "${KIPRJMOD}/parts.kicad_sym") (options "") (descr "")))')
        return session_for(d, "partsproj")

    def test_footprint_check_without_a_board(self, tmp_dir, empty_global_lib_tables):
        sess = self._parts_project(tmp_dir)
        r = library.footprint_check(sess, "parts:TDFN")
        assert r["ok"] is False
        assert r["required_clearance"] == 0.2
        assert {"a": "1", "b": "9", "actual": 0.15, "required": 0.2} in r["clearance_violations"]
        assert {"a": "2", "b": "9", "actual": 0.15, "required": 0.2} in r["clearance_violations"]
        assert r["pad_numbers"] == {"signal": ["1", "2"], "other": ["9"]}
        assert r["duplicate_numbers"] == []
        assert r["paste_only_pads_with_numbers"] == ["9"]
        assert r["courtyard"] == {"present": True, "size": [2.5, 1.8], "margin_to_pads": 0.25}
        assert r["missing_fab_layers"] == [] and r["models"] == []

    def test_link_check_reports_pin_without_pad(self, tmp_dir, empty_global_lib_tables):
        sess = self._parts_project(tmp_dir)
        r = library.link_check(sess.project, "parts:CHIP")
        assert r["fpid"] == "parts:TDFN" and r["ok"] is False
        assert r["pins_without_pad"] == ["10"]
        assert r["pads_without_pin"] == ["9"]
        assert r["mechanical_pads"] == []
        assert r["sym_pins"] == ["1", "2", "10"]

    def test_update_reports_pins_without_a_pad(self, tmp_dir, empty_global_lib_tables):
        sess = self._parts_project(tmp_dir)
        comps = [{"ref": "U1", "lib_id": "parts:CHIP", "footprint": "parts:TDFN"},
                 {"ref": "R1", "lib_id": "parts:CHIP", "footprint": ""}]
        assert pcb.unmatched_pins(sess.project, comps) == [{"ref": "U1", "pin": "10", "name": "P10"}]



    def test_score(self):
        f = {"name": "R_0603", "keywords": "resistor", "lib": "Resistor_SMD", "description": "Resistor 0603"}
        assert library._score(["0603"], f) > 0
        assert library._score(["capacitor"], f) == 0
        assert library._score(["R_*"], f) > 0
        assert library._parse_range("3-5") == (3, 5) and library._parse_range("10+")[0] == 10


# ═══════════════════════════════ kicad-cli wrappers ═══════════════════════════════

class TestKicadCliCmds:
    def test_spec_complete(self):
        spec = kicad_cli_cmds.load_spec()
        assert len(spec["commands"]) >= 43
        assert "pcb export gerbers" in spec["commands"] and "jobset run" in spec["commands"]

    def test_build_argv(self):
        spec = {"options": [{"long": "--output", "takes_value": True, "repeatable": False},
                            {"long": "--define-var", "takes_value": True, "repeatable": True},
                            {"long": "--no-x2", "takes_value": False, "repeatable": False}],
                "positionals": [{"name": "INPUT_FILE"}]}
        argv = kicad_cli_cmds.build_argv("pcb export gerbers", spec, {"--output": "o/", "--define-var": ("A=1", "B=2"),
                                                                      "--no-x2": True}, "b.kicad_pcb")
        assert argv == ["pcb", "export", "gerbers", "--output", "o/", "--define-var", "A=1", "--define-var", "B=2",
                        "--no-x2", "b.kicad_pcb"]

    def test_command_paths(self):
        assert kicad_cli_cmds.command_path("sym export svg") == ["lib", "sym", "export", "svg"]
        assert kicad_cli_cmds.command_path("pcb export step") == ["pcb", "export", "step"]
        assert kicad_cli_cmds.command_path("version") == ["info", "kicad-version"]

    def test_default_outputs(self, tmp_dir):
        p = make_project(tmp_dir, "b")
        spec = kicad_cli_cmds.load_spec()["commands"]
        assert kicad_cli_cmds.default_output(p, "pcb export gerbers", spec["pcb export gerbers"], {}).endswith(
            os.path.join("outputs", "pcb-gerbers") + os.sep)
        assert kicad_cli_cmds.default_output(p, "pcb export step", spec["pcb export step"], {}).endswith("b-pcb-step.step")
        assert kicad_cli_cmds.default_output(p, "pcb drc", spec["pcb drc"], {"--format": "json"}).endswith(".json")
        assert kicad_cli_cmds.default_output(p, "sch export netlist", spec["sch export netlist"],
                                             {"--format": "spice"}).endswith(".cir")
        assert kicad_cli_cmds.default_output(p, "jobset run", spec["jobset run"], {}) is None

    def test_output_is_dir(self):
        spec = kicad_cli_cmds.load_spec()["commands"]
        assert kicad_cli_cmds.output_is_dir(spec["pcb export gerbers"]) is True
        assert kicad_cli_cmds.output_is_dir(spec["pcb export step"]) is False
        assert kicad_cli_cmds.output_is_dir(spec["version"]) is None
        # kicad-cli 10.0.7 help says "Output directory" for gencad but it writes a file
        assert kicad_cli_cmds.output_is_dir(spec["pcb export gencad"], "pcb export gencad") is False


# ═══════════════════════════════ verify / checks / fab / jobset / diff ═══════════════════════════════

class TestMisc:
    @pytest.mark.parametrize("content,kind", [
        (b"%PDF-1.5", "pdf"), (b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "png"), (b"ISO-10303-21;", "step"),
        (b"PK\x03\x04", "zip"), (b"glTF", "glb"), (b"%TF.GenerationSoftware,KiCad*%\nG04 x", "gerber"),
        (b"M48\n", "excellon"), (b"<?xml version='1.0'?><svg>", "svg"), (b"#VRML V2.0", "vrml"),
        (b"solid x", "stl"), (b"ply\n", "ply"), (b"%!PS-Adobe", "postscript"), (b"\x1f\x8b", "gzip"),
        (b'{"a":1}', "json"), (b"(export (version E))", "kicad-netlist"),
    ])
    def test_sniff(self, tmp_dir, content, kind):
        p = os.path.join(tmp_dir, "f.bin")
        with open(p, "wb") as fh:
            fh.write(content)
        assert verify.sniff(p) == kind

    def test_gerber_info(self, tmp_dir):
        p = os.path.join(tmp_dir, "x.gbr")
        wr(p, "%TF.GenerationSoftware,KiCad,Pcbnew,10.0.7*%\n%TF.FileFunction,Copper,L1,Top*%\n")
        info = verify.gerber_info(p)
        assert info["FileFunction"] == "Copper,L1,Top" and info["GenerationSoftware"].startswith("KiCad")

    def test_summaries(self):
        erc = {"sheets": [{"path": "/", "violations": [
            {"type": "pin_not_connected", "severity": "error", "description": "d", "items": [{"description": "U1 pin 3"}]},
            {"type": "endpoint_off_grid", "severity": "warning", "description": "w", "items": []}]}]}
        s = checks.summarize(erc, "erc")
        assert s["errors"] == 1 and s["warnings"] == 1 and not s["clean"]
        assert "no-connect" in s["hints"]["pin_not_connected"]
        drc = {"violations": [], "unconnected_items": [{"type": "unconnected_items", "severity": "error"}],
               "schematic_parity": []}
        d = checks.summarize(drc, "drc")
        assert d["unconnected"] == 1 and d["by_type"] == {"unconnected_items": 1}
        pin_map = checks.summarize({"violations": [
            {"type": "net_conflict", "severity": "warning", "items": [],
             "description": "No pad found for pin 10 in schematic"},
            {"type": "footprint_symbol_field_mismatch", "severity": "warning", "items": [],
             "description": "Field 'Description' differs (PCB: '', Schematic: 'x')"}]}, "drc")
        assert "lib link-check" in pin_map["violations"][0]["hint"]
        assert "sch update-symbols" in pin_map["violations"][0]["hint"]
        assert "pcb update" in pin_map["violations"][1]["hint"]
        assert "lib link-check" in pin_map["hints"]["net_conflict"]
        assert "pcb update" in pin_map["hints"]["footprint_symbol_field_mismatch"]

    def test_cpl_conversion(self, tmp_dir):
        src = os.path.join(tmp_dir, "pos.csv")
        with open(src, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side"])
            w.writerow(["R1", "10k", "R_0603", "1.5", "-2.25", "90", "top"])
        out = os.path.join(tmp_dir, "cpl.csv")
        assert fab._jlc_cpl(src, out) == 1
        rows = list(csv.reader(io.StringIO(rd(out))))
        assert rows[0] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
        assert rows[1] == ["R1", "1.5000mm", "-2.2500mm", "Top", "90.00"]
        assert fab._pcbway_cpl(src, out) == 1 and list(csv.reader(io.StringIO(rd(out))))[1][4] == "T"

    def test_jobset_validation(self, tmp_dir):
        s = session_for(tmp_dir)
        path = jobset.new(s)["jobset"]
        with pytest.raises(KeyError):
            jobset.add_job(s, path, "pcb_export_gerbers", {"bogus": "1"})
        with pytest.raises(ValueError):
            jobset.add_job(s, path, "pcb_export_3d", {"format": "obj"})
        with pytest.raises(KeyError):
            jobset.add_job(s, path, "no_such_job", {})
        r = jobset.add_job(s, path, "pcb_render", {"side": "bottom"})
        assert r["settings"] == {"side": "bottom", "width": 1600, "height": 900}
        assert any(t["type"] == "pcb_export_gerbers" for t in jobset.job_types())

    def test_board_diff_synthetic(self, tmp_dir):
        def board(d, x):
            os.makedirs(d)
            wr(os.path.join(d, "b.kicad_pro"), "{}")
            sexpr.dump(S("kicad_pcb", S("footprint", Q("L:F"), S("layer", Q("F.Cu")), S("at", x, 10),
                                         S("property", Q("Reference"), Q("R1")), S("property", Q("Value"), Q("1k"))),
                         S("segment", S("start", 0, 0), S("end", 3, 4), S("net", Q("N")))),
                       os.path.join(d, "b.kicad_pcb"))
            return project.KicadProject(os.path.join(d, "b.kicad_pro"))
        a, b = board(os.path.join(tmp_dir, "a"), 10), board(os.path.join(tmp_dir, "b"), 12.5)
        r = diff_mod.board_diff(a, b)
        assert r["footprints_moved"][0]["distance"] == 2.5 and r["routing_changes"] == []
        assert r["totals"]["segments"] == [1, 1]


# ═══════════════════════════════ identity / packaging ═══════════════════════════════

class TestIdentity:
    def test_names_and_env(self, monkeypatch):
        assert (APP_NAME, COMMAND, ENV_PREFIX) == ("kicad-cli-extended", "kce", "KCE_")
        assert STATE_DIR.startswith(".")
        monkeypatch.setenv(ENV_PREFIX + "SOMETHING", "x")
        assert env("SOMETHING") == "x" and env("MISSING", "d") == "d"

    def test_version_option(self):
        from click.testing import CliRunner
        from kicad_cli_extended.cli import cli
        r = CliRunner().invoke(cli, ["--version"])
        assert r.exit_code == 0 and r.output.strip() == f"kce ({APP_NAME}) {__version__}"

    def test_packaged_data_present(self):
        assert kicad_cli_cmds.load_spec()["commands"]
        assert jobset.catalog()["jobs"]
        files = skill_mod.packaged_files()
        assert [f.name for f in files] == ["SKILL.md", "COMMANDS.md"]
        assert skill_mod.frontmatter_name(files[0]) == APP_NAME

    def test_packaged_data_does_not_shadow_the_module(self):
        """skill.py and a skill/ data dir side by side read as one namespace to type checkers/IDEs."""
        pkg = os.path.dirname(os.path.abspath(skill_mod.__file__))
        assert skill_mod.PACKAGED_DIR.name == "skill_data" and os.path.isdir(skill_mod.PACKAGED_DIR)
        assert not os.path.exists(os.path.join(pkg, "skill"))

    def test_readme_links_are_absolute(self):
        """PyPI renders README.md without the repository: relative links and images break there."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = rd(os.path.join(repo, "README.md"))
        relative = [m for m in re.findall(r"\]\(([^)\s]+)\)|(?:src|href)=\"([^\"]+)\"", text)
                    for m in m if m and not m.startswith(("https://", "http://", "#", "mailto:"))]
        assert not relative, f"make these absolute (PyPI cannot resolve them): {relative}"

    def test_repo_skill_copy_in_sync(self):
        repo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", APP_NAME)
        for f in skill_mod.packaged_files():
            assert rd(os.path.join(repo, f.name)) == rd(f), f"run scripts/gen_skill.py ({f.name} out of sync)"

    def test_committed_skill_files_match_generator(self):
        import subprocess
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, os.path.join(repo, "scripts", "gen_skill.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_commands_md_covers_every_command(self):
        from click.testing import CliRunner
        from kicad_cli_extended.cli import cli
        r = CliRunner().invoke(cli, ["--json", "info", "commands"])
        assert r.exit_code == 0, r.output
        md = rd(skill_mod.PACKAGED_DIR / "COMMANDS.md")
        missing = [c["command"] for c in json.loads(r.output) if f"`kce {c['command']}`" not in md]
        assert not missing, f"COMMANDS.md is missing {missing}; run scripts/gen_skill.py"


# ═══════════════════════════════ skill installer ═══════════════════════════════

class TestSkillInstall:
    def test_install_status_uninstall_in_dirs(self, tmp_dir):
        target = os.path.join(tmp_dir, "skills")
        r = skill_mod.install(dirs=[target], dry_run=True)
        assert r["results"][0]["action"] == "create" and not os.path.exists(target)
        r = skill_mod.install(dirs=[target])
        dest = os.path.join(target, APP_NAME)
        assert r["results"][0]["action"] == "create"
        assert sorted(os.listdir(dest)) == ["COMMANDS.md", "SKILL.md"]
        assert skill_mod.install(dirs=[target])["results"][0]["action"] == "unchanged"
        wr(os.path.join(dest, "SKILL.md"), "---\nname: kicad-cli-extended\n---\nold\n")
        assert skill_mod.install(dirs=[target])["results"][0]["action"] == "update"
        assert skill_mod.uninstall(dirs=[target])["results"][-1]["action"] == "remove"
        assert not os.path.exists(dest)

    def test_foreign_skill_needs_force(self, tmp_dir):
        dest = os.path.join(tmp_dir, APP_NAME)
        os.makedirs(dest)
        wr(os.path.join(dest, "SKILL.md"), "---\nname: something-else\n---\n")
        assert skill_mod.install(dirs=[tmp_dir])["results"][0]["action"] == "skipped"
        assert skill_mod.install(dirs=[tmp_dir], force=True)["results"][0]["action"] == "update"

    def test_targets_resolve_under_home(self, tmp_dir, monkeypatch):
        monkeypatch.setenv("HOME", tmp_dir)
        monkeypatch.setenv("USERPROFILE", tmp_dir)
        os.makedirs(os.path.join(tmp_dir, ".claude"))
        os.makedirs(os.path.join(tmp_dir, ".gemini", "config", "skills"))
        assert skill_mod.detected_targets() == ["agents", "claude", "gemini"]
        plan = skill_mod.install(dry_run=True)["results"]
        dests = {r["target"]: r["dest"] for r in plan}
        assert dests["claude"] == os.path.join(tmp_dir, ".claude", "skills", APP_NAME)
        assert dests["gemini"] == os.path.join(tmp_dir, ".gemini", "config", "skills", APP_NAME)
        with pytest.raises(ValueError):
            skill_mod.install(targets=["nope"], dry_run=True)

    def test_cli_skill_install_json(self, tmp_dir):
        from click.testing import CliRunner
        from kicad_cli_extended.cli import cli
        r = CliRunner().invoke(cli, ["--json", "skill", "install", "--dir", tmp_dir])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["results"][0]["action"] == "create"


# ═══════════════════════════════ cross-OS helpers ═══════════════════════════════

class TestPaths:
    def test_rel_inside_and_uri(self, tmp_dir):
        inner = os.path.join(tmp_dir, "lib", "my.pretty")
        assert paths.rel_inside(inner, tmp_dir) == os.path.join("lib", "my.pretty")
        assert paths.rel_inside(tmp_dir, inner) is None
        assert paths.kicad_uri(inner, tmp_dir) == "${KIPRJMOD}/lib/my.pretty"
        outside = os.path.dirname(tmp_dir)
        assert "\\" not in paths.kicad_uri(outside, tmp_dir)

    def test_dir_hint_and_same_path(self, tmp_dir):
        assert paths.is_dir_hint("out/") and paths.is_dir_hint("out" + os.sep) and not paths.is_dir_hint("out.pdf")
        assert paths.same_path(os.path.join(tmp_dir, "a", "..", "b"), os.path.join(tmp_dir, "b"))

    def test_mode_for_path_uses_basename(self):
        assert sexpr.mode_for_path(os.path.join("x", "y", "sym-lib-table")) == "library_table"

    def test_kicad_files_written_with_lf(self, tmp_dir):
        p = os.path.join(tmp_dir, "t.kicad_sch")
        sexpr.dump(sexpr.loads("(kicad_sch (version 1) (lib_symbols))"), p)
        with open(p, "rb") as fh:
            data = fh.read()
        assert b"\r" not in data and data.endswith(b")\n")

    def test_session_keys_normalised(self, tmp_dir):
        s = session_for(tmp_dir)
        f = os.path.join(tmp_dir, "a.kicad_dru")
        d1 = s.doc(f, "text")
        assert s.doc(os.path.join(tmp_dir, ".", "a.kicad_dru")) is d1 and s.has_doc(f)


class TestStdio:
    def test_ascii_json_on_non_utf8_stream(self, monkeypatch):
        import io as _io
        from kicad_cli_extended import cli as cli_mod
        buf = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr(cli_mod, "_json_output", True)
        cli_mod.output({"v": "저항 Ω"})
        buf.flush()
        raw = buf.buffer.getvalue()
        raw.decode("ascii")
        assert json.loads(raw)["v"] == "저항 Ω"

    def test_configure_stdio_makes_cp1252_safe(self, monkeypatch):
        import io as _io
        from kicad_cli_extended import cli as cli_mod
        buf = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
        monkeypatch.setattr(sys, "stdout", buf)
        cli_mod.configure_stdio()
        sys.stdout.write("✓ 저항\n")
        sys.stdout.flush()
        assert b"\\u2713" in buf.buffer.getvalue()


class TestBundles:
    def test_bundle_cache_latest_and_history(self, tmp_dir):
        root = Path(tmp_dir)
        key = bundles.fingerprint({"a": 1})
        assert key == bundles.fingerprint({"a": 1}) and key.startswith("sha256:")
        b = bundles.new_bundle(root, "quick", key)
        art = Path(b["artifacts_dir"]) / "x.svg"
        art.write_text("<svg/>", encoding="utf-8")
        rec = bundles.artifact(b["bundle_dir"], art, "x", "hero", "image", "X")
        assert rec["path"] == "artifacts/x.svg" and rec["media_type"] == "image/svg+xml"
        m = bundles.finalize(b, "capture", "quick", "ok", {"fingerprint": "f"}, [rec], {"headline": "h"}, "kce x")
        assert bundles.find_cached(root, "quick", key)["id"] == m["id"]
        assert bundles.find_cached(root, "quick", "sha256:other") is None
        assert bundles.find_latest(root)["id"] == m["id"]
        sdir = root / "live" / "quick"
        bundles.append_step(sdir, {"reason": "start", "bundle_id": m["id"]})
        bundles.append_step(sdir, {"reason": "push", "bundle_id": m["id"]})
        h = bundles.history_summary(bundles.load_history(sdir))
        assert h["steps"] == 2 and h["latest_reason"] == "push"


class TestShell:
    def test_piped_shell_runs_commands(self, monkeypatch, capsys):
        import io as _io
        from kicad_cli_extended.repl import Shell
        monkeypatch.setattr(sys, "stdin", _io.StringIO("# c\nfoo --x\nkce bar\n'unterminated\nquit\nignored\n"))
        ran = []
        Shell(ran.append, lambda: ("p", 0), {"g": "help"}).run()
        assert ran == [["foo", "--x"], ["bar"]]
        assert "interactive shell" not in capsys.readouterr().out


class TestPublicDiscovery:
    def test_every_leaf_has_effect_arguments_and_example(self):
        from kicad_cli_extended.cli import run_in_process
        commands = run_in_process(['info', 'commands', '--full'])
        assert {'run', 'serve', 'sch apply', 'sch verify', 'session checkpoint', 'session restore',
                'pcb score', 'pcb legalize', 'pcb place near'} <= {c['command'] for c in commands}
        for c in commands:
            assert c['effect'] in {'read', 'write', 'export', 'control'} and c['example'].startswith('kce ')
            assert c['returns'] and c['exit_codes']['design'] == 3
            for arg in c['args']:
                assert {'name', 'flags', 'type', 'required'} <= arg.keys()
        described = run_in_process(['info', 'describe', 'sch', 'no-connect'])
        assert described == next(c for c in commands if c['command'] == 'sch no-connect')

    def test_generator_check_never_writes_stale_or_missing_files(self, tmp_path, monkeypatch):
        import importlib.util
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('gen_skill', repo / 'scripts/gen_skill.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        existing, missing = tmp_path / 'existing', tmp_path / 'missing'
        existing.mkdir()
        stale = existing / 'SKILL.md'
        stale.write_text('stale', encoding='utf-8')
        stamp = stale.stat().st_mtime_ns
        monkeypatch.setattr(generator, 'SKILL_DIRS', (existing, missing))
        monkeypatch.setattr(sys, 'argv', ['gen_skill.py', '--check'])
        assert generator.main() == 1
        assert stale.read_text(encoding='utf-8') == 'stale' and stale.stat().st_mtime_ns == stamp
        assert not missing.exists() and not (existing / 'COMMANDS.md').exists()

    def test_reference_count_matches_live_registry(self):
        import re
        from kicad_cli_extended.cli import run_in_process
        md = rd(skill_mod.PACKAGED_DIR / 'COMMANDS.md')
        assert int(re.search(r'\((\d+) commands\)', md).group(1)) == len(run_in_process(['info', 'commands']))


class TestJobsetGate:
    def test_job_failure_keeps_stdout_and_exits_design(self, monkeypatch):
        from click.testing import CliRunner
        from kicad_cli_extended import cli as m
        from kicad_cli_extended.core.session import Session
        result = {'failed_jobs': 1, 'returncode': 9, 'outputs': [],
                  'failed': [{'job_id': 'a', 'type': 'pcb_export_3d', 'title': '3D', 'message': 'Job failed'}]}
        monkeypatch.setattr(m, '_session', Session())
        monkeypatch.setattr(m, '_had_error', False)
        monkeypatch.setattr(m.jobset_mod, 'jobset_path', lambda *a: '/unused')
        monkeypatch.setattr(m.jobset_mod, 'run', lambda *a: result)
        runner = CliRunner()
        r = runner.invoke(m.cli, ['--json', 'jobset', 'execute'])
        assert r.exit_code == 3
        assert json.loads(r.stdout)['failed_jobs'] == 1
        error = json.loads(r.stderr)
        assert (error['type'], error['category']) == ('JobsetFailed', 'design')
        assert error['details']['failed'][0]['type'] == 'pcb_export_3d'
        r = runner.invoke(m.cli, ['--json', 'jobset', 'execute', '--no-fail-on-job-error'])
        assert r.exit_code == 0 and json.loads(r.stdout)['returncode'] == 9


class TestSafetyBoundary:
    def test_read_only_rejects_before_command_or_project_access(self, monkeypatch):
        from click.testing import CliRunner
        from kicad_cli_extended.cli import cli
        from kicad_cli_extended.core import project
        def unexpected(*a):
            raise AssertionError('project discovery must follow the read-only gate')
        monkeypatch.setattr(project, 'resolve_project', unexpected)
        r = CliRunner().invoke(cli, ['--read-only', 'project', 'new', '/unused', '--json'])
        assert r.exit_code == 2 and json.loads(r.stderr)['category'] == 'input'
        assert 'Read-only mode refuses' in r.stderr

    def test_global_options_after_leaf_match_global_first(self):
        from kicad_cli_extended.cli import run_in_process
        assert run_in_process(['fab', 'presets', '--json']) == run_in_process(['--json', 'fab', 'presets'])

class TestSessionLocation:
    def test_first_write_hides_state_and_external_state_separates_projects(self, tmp_path, monkeypatch):
        from kicad_cli_extended.core.session import Session
        from kicad_cli_extended.core.project import KicadProject
        root = tmp_path / 'design'
        root.mkdir()
        pro = root / 'p.kicad_pro'
        pro.write_text('{}', encoding='utf-8')
        path = root / 'notes.txt'
        path.write_text('before', encoding='utf-8')
        session = Session()
        session.set_project(KicadProject(str(pro)))
        session.snapshot('edit', [str(path)])
        session.doc(str(path), 'text').set_text('after')
        session.save_session()
        assert (root / STATE_DIR / '.gitignore').read_text(encoding='utf-8') == '*\n'
        import shutil
        shutil.rmtree(root / STATE_DIR)
        monkeypatch.setenv('KCE_SESSION_DIR', str(tmp_path / 'state'))
        second = Session()
        second.set_project(KicadProject(str(pro)))
        second.snapshot('edit outside', [str(path)])
        second.doc(str(path), 'text').set_text('third')
        second.save_session()
        assert not (root / STATE_DIR).exists()
        assert os.path.isfile(second.session_file)


class TestCheckpointPublication:
    def test_failed_head_switch_keeps_previous_generation_restorable(self, tmp_path, monkeypatch):
        from kicad_cli_extended.core import plan, session
        project = tmp_path / 'p'
        project.mkdir()
        file = project / 'notes.txt'
        file.write_bytes(b'accepted')
        first = plan.save_checkpoint(str(project), 'best')
        file.write_bytes(b'candidate')
        write = session.atomic_write_bytes
        def interrupt_head(path, data):
            if path.endswith('head.json'):
                raise OSError('injected interruption before head publication')
            write(path, data)
        monkeypatch.setattr(session, 'atomic_write_bytes', interrupt_head)
        with pytest.raises(OSError):
            plan.save_checkpoint(str(project), 'best')
        recovered = plan.read_checkpoint(str(project), 'best')
        assert recovered['files']['notes.txt'] == b'accepted'
        assert recovered['manifest']['generation'] == first['generation']
        assert recovered['manifest']['content_sha256'] == first['content_sha256']


class TestCheckPolicy:
    def test_baseline_and_truncation_do_not_weaken_warning_gate(self, monkeypatch):
        from kicad_cli_extended.core import checks
        from kicad_cli_extended import cli as m
        from click.testing import CliRunner
        report = checks.summarize({'violations': [
            {'type': 'clearance', 'severity': 'error', 'items': [{'uuid': 'old'}]},
            {'type': 'width', 'severity': 'warning', 'items': [{'uuid': 'new'}]}]}, 'drc')
        old = dict(report, violations=report['violations'][:1])
        result, failed = checks.report_policy(report, baseline=old, fail_on='warning', max_violations=0)
        assert result['total'] == 2 and result['violations'] == [] and result['truncated']
        assert result['new_violations'] == 1 and failed[0]['type'] == 'width'
        result, failed = checks.report_policy(report, baseline=report, fail_on='warning', summary=True)
        assert not failed and result['new_violations'] == 0 and 'violations' not in result
        report['unconnected'] = report['parity'] = 0
        monkeypatch.setattr(m.checks_mod, 'run_drc', lambda *a, **kw: report)
        r = CliRunner().invoke(m.cli, ['--json', 'check', 'drc', '--fail-on', 'warning', '--max-violations', '0'])
        assert r.exit_code == 3 and json.loads(r.stdout)['truncated']
        assert json.loads(r.stderr)['category'] == 'design'
        r = CliRunner().invoke(m.cli, ['check', 'drc', '--json', '--fail-on', 'none', '--summary'])
        assert r.exit_code == 0 and 'violations' not in json.loads(r.stdout)


class TestErrorCatalog:
    def test_catalog_covers_schema_types_and_all_categories(self):
        from pathlib import Path
        from kicad_cli_extended.cli import run_in_process, EXIT_CODES
        catalog = run_in_process(['errors'])
        schema = json.loads((Path(__file__).resolve().parents[1] / 'schemas/error.schema.json').read_text(encoding='utf-8'))
        assert {i['type'] for i in catalog['items']} == set(schema['$defs']['type']['enum'])
        assert {i['category'] for i in catalog['items']} == set(EXIT_CODES)
        for item in catalog['items']:
            assert item['exit_code'] == EXIT_CODES[item['category']]
            assert item['meaning'] and item['next_command_pattern'].startswith('kce ')


class TestDoctorCapabilities:

    @pytest.mark.parametrize('reference', ['${KIPRJMOD}/body.step', '${KIPRJMOD}/models/../body.step'])
    def test_footprint_model_paths_and_details_survive_integration(self, tmp_dir, monkeypatch, reference):
        from kicad_cli_extended.cli import run_in_process
        sess = session_for(tmp_dir)
        model = Path(tmp_dir, 'body.step')
        model.write_bytes(b'model')
        Path(tmp_dir, 'models').mkdir()
        mod = Path(tmp_dir, 'BODY.kicad_mod')
        fp = library.make_footprint('BODY', library.generate_pads('chip', 2, 2))
        library.set_footprint_model(fp, reference, offset='1,2,3', rotation='0,0,90')
        mod.write_text(sexpr.dumps(fp), encoding="utf-8")
        monkeypatch.setattr(library.kb, 'kicad_env_vars', lambda *a: {'KIPRJMOD': tmp_dir})
        shown = run_in_process(['lib', 'fp', 'show', str(mod)], session=sess)
        assert shown['models'] == [reference]
        detail = shown['model_details'][0]
        assert detail['exists'] and detail['resolved_path'] == str(model)
        assert detail['offset'] == [1, 2, 3] and detail['rotation'] == [0, 0, 90]
        listed = run_in_process(['lib', 'fp', 'model', 'list', str(mod)], session=sess)
        assert listed['models'] == shown['model_details']
        assert run_in_process(['--read-only', 'lib', 'fp', 'model', 'list', str(mod)], session=sess) == listed

    def test_model_details_are_additive_and_resolve_project_variables(self, tmp_path, monkeypatch):
        from kicad_cli_extended.core import library
        class Project:
            dir = str(tmp_path)
        monkeypatch.setattr(library.kb, 'kicad_env_vars', lambda *a: {'KIPRJMOD': str(tmp_path)})
        actual = tmp_path / 'part.step'
        actual.write_bytes(b'model')
        details = library.model_details(Project(), ['${KIPRJMOD}/part.step', '${KIPRJMOD}/missing.step'], str(tmp_path))
        assert details[0]['exists'] and not details[1]['exists']
        assert details[0]['resolved_path'] == str(actual)


class TestReadOnlyShapes:
    @pytest.mark.parametrize('command', ['lib fp check', 'lib link-check', 'lib fp model list',
                                         'fab bom-check', 'fab parts check'])
    def test_integrated_inspection_commands_are_discovered_as_read_only(self, command):
        from kicad_cli_extended.cli import run_in_process
        assert run_in_process(['info', 'describe', *command.split()])['effect'] == 'read'

    def test_read_only_arrays_and_nets_keep_their_public_shapes(self, monkeypatch):
        from kicad_cli_extended import cli as m
        from kicad_cli_extended.core.session import Session
        monkeypatch.setattr(m, '_session', Session())
        normal = m.run_in_process(['lib', 'fp', 'wizards'])
        assert isinstance(normal, list)
        assert m.run_in_process(['--read-only', 'lib', 'fp', 'wizards']) == normal
        monkeypatch.setattr(m.pcb_mod, 'schematic_netlist', lambda *a: {'components': [], 'nets': []})
        normal = m.run_in_process(['sch', 'nets'])
        assert set(normal) == {'components', 'net_count', 'nets'}
        assert m.run_in_process(['--dry-run', 'sch', 'nets']) == normal


class TestPortableDocumentation:
    def test_powershell_and_ci_contracts_are_documented(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        template = (repo / 'scripts/skill_template.md').read_text(encoding='utf-8')
        readme = (repo / 'README.md').read_text(encoding='utf-8')
        assert 'kce @KCE' in template and '--rotate=-45,0,45' in template
        assert 'fetch-depth: 0' in readme and '.gitattributes' in readme
        for phrase in ('`input` | 2', '`design` | 3', '`validation` | 4', '`crash` | 5', '`timeout` | 6', '`environment` | 7', '`internal` | 1'):
            assert phrase in readme
        for name in ('hw-ci.yml', 'hw-release.yml'):
            workflow = (repo / 'docs/examples' / name).read_text(encoding='utf-8')
            assert 'fetch-depth: 0' in workflow and 'shell: bash' in workflow
            assert 'ubuntu-24.04' in workflow and 'windows-latest' in workflow
            assert 'REPLACE_WITH_REVIEWED_SHA' in workflow and '--version=10.0.7' in workflow


class TestScenarioRecipes:
    def test_installed_skill_exposes_twelve_triggered_chains_and_gates(self):
        import re
        skill = rd(skill_mod.PACKAGED_DIR / 'SKILL.md')
        recipes = dict((int(n), text) for n, text in re.findall(r'### R(\d+): ([\s\S]*?)(?=\n### R|\Z)', skill))
        assert set(recipes) == set(range(1, 13))
        assert all('Triggers:' in text for text in recipes.values())
        for number in (2, 4, 5, 6, 9):
            assert 'checkpoint' in recipes[number].lower()
        assert recipes[2].index('sch verify') < recipes[2].index('--dry-run pcb update')
        assert recipes[5].index('Checkpoint') < recipes[5].index('Preview')
        assert '--fail-on-drc' in recipes[7]
        assert 'commit_state' in recipes[9] and 'immutable' in recipes[9]
        assert 'not an editable schematic migration' in recipes[10]


class TestInstalledSchemas:
    def test_every_repository_schema_is_queryable_offline(self):
        from pathlib import Path
        from kicad_cli_extended.cli import run_in_process
        repo = Path(__file__).resolve().parents[1]
        names = run_in_process(['info', 'schema'])['schemas']
        assert {'plan', 'run-result', 'sch-apply', 'sch-verify', 'sch-nets', 'pcb-score', 'rpc', 'error'} <= set(names)
        for name in names:
            schema = run_in_process(['info', 'schema', name])
            assert schema == json.loads((repo / 'schemas' / (name + '.schema.json')).read_text(encoding='utf-8'))


class TestProgressProtocol:
    def test_progress_is_opt_in_and_separate_from_rpc_stdout(self):
        import io
        from kicad_cli_extended.core.serve import Server
        out, err = io.StringIO(), io.StringIO()
        server = Server(lambda *a: {'value': 1}, out=out, err=err, progress=True)
        server.serve_forever(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"run","params":{"args":["pcb","info"]}}\n'))
        assert json.loads(out.getvalue())['result'] == {'value': 1}
        lines = [json.loads(line) for line in err.getvalue().splitlines()]
        assert [line['progress'] for line in lines] == ['request_started', 'request_finished']
        assert all(line['request_id'] == 1 for line in lines)


class TestNativeJobsetFailureLog:
    def test_ordinal_failure_is_linked_to_selected_job_and_destination(self):
        from kicad_cli_extended.core.jobset import failures_from_log
        data = {'jobs': [{'id': 'step', 'type': 'pcb_export_3d'}, {'id': 'erc', 'type': 'sch_erc'}],
                'outputs': [{'id': 'folder', 'only': ['erc']}]}
        stdout = ('| Running jobs for destination folder\n| Running job 1: Perform ERC\n'
                  'Found 2 violations\nJob failed\n0 jobs succeeded, 1 job failed')
        result = failures_from_log(data, stdout, '')
        assert result[0]['job_id'] == 'erc' and result[0]['type'] == 'sch_erc'
        assert result[0]['title'] == 'Perform ERC' and 'Found 2 violations' in result[0]['message']


class TestWriterArrayContract:
    def test_batch_keeps_arrays_and_puts_preview_metadata_on_entries(self, tmp_path, monkeypatch):
        from kicad_cli_extended import cli as m
        from kicad_cli_extended.core.session import Session
        monkeypatch.setattr(m, '_session', Session())
        monkeypatch.setattr(m.pcb_mod, 'edit', lambda *a: [{'action': 'move', 'result': {'reference': 'R1'}}])
        file = tmp_path / 'actions.json'
        file.write_text('[{"action":"move"}]', encoding='utf-8')
        normal = m.run_in_process(['pcb', 'batch', str(file)])
        dry = m.run_in_process(['--dry-run', 'pcb', 'batch', str(file)])
        assert isinstance(normal, list) and isinstance(dry, list)
        assert normal[0]['dry_run'] is False and dry[0]['dry_run'] is True
        assert 'would_write' in dry[0] and normal[0]['result'] == dry[0]['result']

    def test_script_action_is_refused_before_transaction_access(self):
        from kicad_cli_extended.core.pcb import edit
        class Session:
            _defer_save = True
            def require_project(self):
                pytest.fail('script reached project mutation')
        with pytest.raises(ValueError, match='Arbitrary Python'):
            edit(Session(), [{'action': 'script', 'code': 'raise Exception()'}], 'unsafe')


class TestExternalExecutionBoundary:
    def test_dry_run_refuses_external_job_commands_before_dispatch(self, tmp_path, monkeypatch):
        from kicad_cli_extended import cli as m
        root = tmp_path / 'project'
        root.mkdir()
        (root / 'p.kicad_pro').write_text('{}', encoding='utf-8')
        job = root / 'unsafe.kicad_jobset'
        job.write_text(json.dumps({'jobs': [{'id': 'shell', 'type': 'special_execute', 'settings': {}}], 'outputs': []}), encoding='utf-8')
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        monkeypatch.setattr(m.jobset_mod, 'run', lambda *a: pytest.fail('external execution reached dispatch'))
        with pytest.raises(ValueError, match='external commands'):
            m.run_in_process(['--dry-run', 'jobset', 'execute', 'unsafe'], str(root))
        assert {p.name: p.read_bytes() for p in root.iterdir()} == before


class TestColdRequestDeadline:
    def test_discovery_probe_obeys_request_budget_and_reaps(self, monkeypatch):
        import subprocess
        import time
        from kicad_cli_extended.core import runtime
        from kicad_cli_extended.utils import kicad_backend as kb
        monkeypatch.setattr(kb, 'kicad_cli_command', lambda: [sys.executable, '-c', 'import time; time.sleep(5)'])
        kb.kicad_version.cache_clear()
        request = runtime.Request('cold', 50)
        started = time.monotonic()
        try:
            with runtime.scope(request), pytest.raises((subprocess.TimeoutExpired, kb.KicadError)):
                kb.kicad_version()
        finally:
            kb.kicad_version.cache_clear()
        assert time.monotonic() - started < 2
        assert request.process is None

    def test_non_backend_failure_keeps_request_identity(self):
        from kicad_cli_extended.core.serve import Server
        def fail(*a):
            raise ValueError('bad request')
        response = Server(fail).handle('{"jsonrpc":"2.0","id":7,"method":"run","params":{"args":["pcb","info"],"timeout_ms":123}}')
        details = response['error']['data']['details']
        assert details['request_id'] == 7 and details['timeout_ms'] == 123
        assert details['commit_state'] == 'unknown' and details['exit_code'] == 2


class TestUnpublishedCheckpoint:
    def test_first_interrupted_generation_is_not_listed(self, tmp_path, monkeypatch):
        from kicad_cli_extended.core import plan, session
        (tmp_path / 'notes.txt').write_bytes(b'accepted')
        write = session.atomic_write_bytes
        def interrupt(path, data):
            if path.endswith('head.json'):
                raise OSError('interrupted before first head')
            write(path, data)
        monkeypatch.setattr(session, 'atomic_write_bytes', interrupt)
        with pytest.raises(OSError):
            plan.save_checkpoint(str(tmp_path), 'unpublished')
        assert plan.list_checkpoints(str(tmp_path)) == []

    def test_custom_state_directory_cannot_recurse_into_project(self, tmp_path):
        from kicad_cli_extended.core.session import state_directory
        with pytest.raises(ValueError, match='outside the project'):
            state_directory(str(tmp_path), str(tmp_path / 'state'))


class TestRequestRetryState:
    def test_invalid_request_does_not_reserve_id_for_retry(self):
        import io
        from kicad_cli_extended.core.serve import Server
        out = io.StringIO()
        server = Server(lambda *a: {'ok': True}, out=out)
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'run', 'params': {'args': [42]}},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'run', 'params': {'args': ['pcb', 'info']}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'}]
        server.serve_forever(io.StringIO(''.join(json.dumps(r) + '\n' for r in requests)))
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        assert replies[0]['error']['code'] == -32602
        assert replies[1]['result'] == {'ok': True}
        assert server.requests == {}


class TestGeneratedBytes:
    def test_check_detects_crlf_without_normalizing_or_writing(self, tmp_path, monkeypatch):
        import importlib.util
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('gen_skill_bytes', repo / 'scripts/gen_skill.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        files = generator.render()
        for name, value in files.items():
            (tmp_path / name).write_bytes(value.encode('utf-8').replace(b'\n', b'\r\n'))
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
        monkeypatch.setattr(generator, 'SKILL_DIRS', (tmp_path,))
        stale = generator.stale_files(files)
        assert str(tmp_path / 'SKILL.md') in stale and str(tmp_path / 'COMMANDS.md') in stale
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


class TestArtifactMetadata:
    def test_archive_file_count_is_preserved_and_output_path_is_reported(self, tmp_path):
        from kicad_cli_extended.cli import run_in_process
        root = tmp_path / 'project'
        root.mkdir()
        (root / 'p.kicad_pro').write_text('{}', encoding='utf-8')
        output = tmp_path / 'archive.zip'
        result = run_in_process(['project', 'archive', '--output', str(output)], str(root))
        assert output.is_file() and result['files'] == 1
        assert result['dry_run'] is False and str(output) in result['would_write']

    def test_global_table_mapping_keeps_its_keys_and_entry_types(self, monkeypatch):
        from kicad_cli_extended import cli as m
        mapping = {name: {'path': '/config/' + name, 'action': 'installed'} for name in ('sym', 'fp', 'design_block')}
        monkeypatch.setattr(m.proj_mod, 'init_global_tables', lambda *a: mapping)
        result = m.run_in_process(['lib', 'init-global-tables'])
        assert {k: v['action'] for k, v in result.items()} == {k: 'installed' for k in mapping}
        assert all(v['dry_run'] is False and v['would_write'] for v in result.values())


class TestBenchDiscovery:
    def test_mcp_close_reaps_server_and_closes_descendant_pipes(self, monkeypatch):
        import subprocess
        from concurrent.futures import ThreadPoolExecutor
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bench'))
        from mcp_client import StdioClient
        code = """import json, os, subprocess, sys
if os.name == 'nt':
    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'capabilities': {}}}), flush=True)
"""
        client = StdioClient([sys.executable, '-u', '-c', code])
        try:
            client.start(timeout=5)
            client.close()
            assert client.proc.poll() == 0
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(client.proc.stdout.read).result(timeout=3) == ''
        finally:
            client.close()
            for stream in (client.proc.stdin, client.proc.stdout, client.proc.stderr):
                stream.close()

    def test_bench_tools_use_doctor_without_kicad_on_path(self, tmp_path, monkeypatch):
        import importlib.util
        import subprocess
        import types
        bench = Path(__file__).resolve().parents[1] / 'bench'
        monkeypatch.syspath_prepend(str(bench))
        monkeypatch.setitem(sys.modules, 'tiktoken', types.SimpleNamespace(
            get_encoding=lambda _: types.SimpleNamespace(encode=lambda *a, **kw: [])))

        def load(name):
            spec = importlib.util.spec_from_file_location(name, bench / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.HERE = tmp_path
            return module

        calls = []
        python = 'C:\\Program Files\\KiCad\\10.0\\bin\\python.exe'

        def run(cmd, **kw):
            calls.append(cmd)
            assert cmd[0] == 'kce.exe'
            stdout = json.dumps({'pcbnew_python': python, 'kicad_version': '10.0.7'}) if cmd[-1] == 'doctor' else 'kce 0.1.0'
            return subprocess.CompletedProcess(cmd, 0, stdout, '')

        monkeypatch.setattr(subprocess, 'run', run)
        monkeypatch.setattr(__import__('platform'), 'platform', lambda: 'test-platform')
        monkeypatch.setattr(__import__('shutil'), 'which', lambda name: 'kce.exe' if name == 'kce' else None)
        monkeypatch.delenv('KCE_PYTHON', raising=False)
        (tmp_path / 'servers.json').write_text(json.dumps({'servers': []}), encoding='utf-8')
        output = tmp_path / 'result.json'
        monkeypatch.setattr(sys, 'argv', ['run_bench.py', '--workdir', str(tmp_path), '--reps', '0', '--out', str(output)])
        assert load('run_bench').main() == 0
        assert json.loads(output.read_text(encoding='utf-8'))['kicad'] == '10.0.7'

        (tmp_path / 'server' / '.git').mkdir(parents=True)
        (tmp_path / 'servers.json').write_text(json.dumps({'servers': [
            {'id': 'server', 'kind': 'node+python', 'commit': 'abc'}]}), encoding='utf-8')
        fetch = load('fetch_servers')
        commands = []
        monkeypatch.setattr(fetch, 'sh', lambda cmd, **kw: commands.append(cmd))
        monkeypatch.setattr(sys, 'argv', ['fetch_servers.py', '--workdir', str(tmp_path)])
        assert fetch.main() == 0
        assert any(cmd[:3] == [python, '-m', 'venv'] for cmd in commands)
        assert sum(cmd[-1] == 'doctor' for cmd in calls) == 2


class TestExternalRequestCancellation:
    @pytest.mark.parametrize('scoped', [True, False])
    def test_backend_preserves_arguments_streams_and_exit_code(self, tmp_path, scoped):
        import contextlib
        from kicad_cli_extended.core import runtime
        code = "import os,sys; print(sys.argv[1]); print(os.getcwd()); print('stderr', file=sys.stderr); sys.exit(3)"
        scope = runtime.scope(runtime.Request('io', 5000)) if scoped else contextlib.nullcontext()
        with scope:
            result = runtime.execute([sys.executable, '-c', code, 'a space Ω "quoted" \\'],
                                     cwd=tmp_path, timeout=5, capture_output=True,
                                     text=True, encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        assert result.returncode == 3
        assert result.stdout.splitlines() == ['a space Ω "quoted" \\', str(tmp_path)]
        assert result.stderr.strip() == 'stderr'

    @pytest.mark.parametrize('scoped', [True, pytest.param(False, marks=pytest.mark.skipif(
        os.name != 'nt', reason='standalone process jobs are Windows-specific'))])
    def test_timeout_reaps_descendants_holding_output_pipes(self, scoped, tmp_path):
        import contextlib
        import subprocess
        import time
        from kicad_cli_extended.core import runtime
        from kicad_cli_extended.utils.kicad_backend import KicadError
        child = "import time; time.sleep(5)"
        code = ("import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
                "pathlib.Path(sys.argv[2]).write_bytes(b'ready'); time.sleep(5)")
        ready = tmp_path / 'child-started'
        scope = runtime.scope(runtime.Request('tree', 5000)) if scoped else contextlib.nullcontext()
        started = time.monotonic()
        with scope, pytest.raises((subprocess.TimeoutExpired, KicadError)):
            runtime.execute([sys.executable, '-c', code, child, str(ready)], timeout=1,
                            capture_output=True, text=True, encoding='utf-8')
        assert ready.read_bytes() == b'ready'
        assert time.monotonic() - started < 4

    @pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
    def test_process_exit_during_cleanup_preserves_timeout(self, monkeypatch):
        import signal
        import subprocess
        from kicad_cli_extended.core import runtime
        from kicad_cli_extended.cli import error_payload
        from kicad_cli_extended.utils.kicad_backend import KicadError

        request = runtime.Request('cleanup-race', 100)
        killpg = os.killpg

        def exit_before_signal(pid, sig):
            assert sig == signal.SIGKILL
            killpg(pid, sig)
            request.process.wait(timeout=5)
            killpg(pid, sig)

        monkeypatch.setattr(os, 'killpg', exit_before_signal)
        with runtime.scope(request), pytest.raises((KicadError, subprocess.TimeoutExpired)) as caught:
            runtime.execute([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=10,
                            capture_output=True, text=True, encoding='utf-8')
        payload = error_payload(caught.value)
        assert payload['category'] == 'timeout'
        assert payload['details']['request_id'] == 'cleanup-race'
        assert payload['details']['returncode'] == -signal.SIGKILL
        assert payload['details']['retryable'] is True
        assert request.process is None

    def test_external_engine_is_killed_reaped_and_reports_retry_state(self):
        import subprocess
        import threading
        from kicad_cli_extended.core import runtime
        from kicad_cli_extended.cli import error_payload
        from kicad_cli_extended.utils.kicad_backend import KicadError
        request = runtime.Request('engine', 1000)
        timer = threading.Timer(0.1, request.stop)
        timer.start()
        try:
            with runtime.scope(request), pytest.raises((KicadError, subprocess.TimeoutExpired)) as caught:
                runtime.execute([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=10,
                                capture_output=True, text=True, encoding='utf-8')
            payload = error_payload(caught.value)
            assert payload['category'] == 'timeout'
            assert payload['details']['request_id'] == 'engine'
            assert payload['details']['worker_pid'] and payload['details']['returncode'] is not None
            assert payload['details']['retryable'] and payload['details']['commit_state'] == 'unknown'
            assert request.process is None
        finally:
            timer.cancel()
            timer.join()


class TestTransactionScope:
    def test_reserved_metadata_paths_are_rejected_before_execution(self, tmp_path):
        from kicad_cli_extended.cli import cli
        from kicad_cli_extended.core.plan import validate_step
        for relative in ('.git/config', STATE_DIR + '/session.json'):
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('(root)', encoding='utf-8')
            with pytest.raises(ValueError, match='metadata'):
                validate_step(cli, ['raw', 'set', str(target), 'root', 'x'], str(tmp_path))

    def test_validation_rollback_preserves_external_history_bytes(self, tmp_path):
        from pathlib import Path
        from kicad_cli_extended.core.session import Session, ValidationError
        from kicad_cli_extended.core.project import KicadProject
        root = tmp_path / 'design'
        root.mkdir()
        pro = root / 'p.kicad_pro'
        pro.write_text('{}', encoding='utf-8')
        notes = root / 'notes.txt'
        notes.write_text('baseline', encoding='utf-8')
        session = Session()
        session.session_dir = str(tmp_path / 'state')
        session.set_project(KicadProject(str(pro)))
        session.snapshot('accepted', [str(notes)])
        session.doc(str(notes), 'text').set_text('accepted')
        session.save_session()
        history = Path(session.session_file).read_bytes()
        def reject(paths):
            raise ValueError('injected validation failure')
        with pytest.raises(ValidationError):
            with session.transaction(reject):
                session.snapshot('candidate', [str(notes)])
                session.doc(str(notes), 'text').set_text('candidate')
        assert notes.read_text(encoding='utf-8') == 'accepted'
        assert Path(session.session_file).read_bytes() == history

    def test_global_transaction_options_cannot_hide_after_step_commands(self):
        from kicad_cli_extended.core.plan import validate_plan
        for options in (['--project', '/elsewhere'], ['--session-dir=/elsewhere'], ['--dry-run'], ['--no-validate']):
            with pytest.raises(ValueError, match='global transaction options'):
                validate_plan({'version': 1, 'steps': [{'args': ['sch', 'set-value', 'R1', '470', *options]}]})

    def test_rollback_io_failure_never_claims_a_successful_restore(self, tmp_path, monkeypatch):
        from kicad_cli_extended import cli as m
        from kicad_cli_extended.core import plan
        root = tmp_path / 'design'
        root.mkdir()
        (root / 'p.kicad_pro').write_text('{}', encoding='utf-8')
        file = tmp_path / 'plan.json'
        file.write_text(json.dumps({'version': 1, 'steps': [
            {'args': ['project', 'set', 'text_variables.X', '1']},
            {'args': ['info', 'describe', 'not-a-command']}]}), encoding='utf-8')
        def fail(*a, **kw):
            raise OSError('injected restore I/O failure')
        monkeypatch.setattr(plan, 'restore_files', fail)
        with pytest.raises(plan.PlanError) as caught:
            m.run_in_process(['--no-validate', 'run', str(file)], str(root))
        details = m.error_payload(caught.value)['details']
        assert details['rolled_back'] is False and details['commit_state'] == 'unknown'
        assert 'Unknown command' in details['original_error']

    def test_preexisting_external_dirty_documents_cannot_escape_scope(self, tmp_path):
        from kicad_cli_extended.core.session import Session
        from kicad_cli_extended.core.project import KicadProject
        root = tmp_path / 'design'
        root.mkdir()
        pro = root / 'p.kicad_pro'
        pro.write_text('{}', encoding='utf-8')
        outside = tmp_path / 'library.txt'
        outside.write_text('accepted', encoding='utf-8')
        session = Session()
        session.set_project(KicadProject(str(pro)))
        session.doc(str(outside), 'text').set_text('pending')
        with pytest.raises(ValueError, match='outside the project'):
            with session.transaction():
                pytest.fail('external document reached transaction execution')
        assert outside.read_text(encoding='utf-8') == 'accepted'

    def test_session_project_identity_cannot_change_inside_transaction(self, tmp_path):
        from kicad_cli_extended.core.session import Session
        from kicad_cli_extended.core.project import KicadProject
        project = tmp_path / 'p.kicad_pro'
        project.write_text('{}', encoding='utf-8')
        other = tmp_path / 'other.kicad_pro'
        other.write_text('{}', encoding='utf-8')
        session = Session()
        original = KicadProject(str(project))
        session.set_project(original)
        with pytest.raises(ValueError, match='Cannot switch project'):
            with session.transaction():
                session.set_project(KicadProject(str(other)))
        assert session.project is original


class TestPlanProgressIdentity:
    def test_step_progress_carries_request_id_and_keeps_stdout_separate(self, tmp_path, capsys):
        from kicad_cli_extended import cli as m
        from kicad_cli_extended.core.runtime import Request, scope
        root = tmp_path / 'design'
        root.mkdir()
        (root / 'p.kicad_pro').write_text('{}', encoding='utf-8')
        file = tmp_path / 'plan.json'
        file.write_text(json.dumps({'version': 1, 'steps': [{'args': ['info', 'schema', 'error']}]}), encoding='utf-8')
        with scope(Request('trial', 10000)):
            result = m.run_in_process(['--progress', 'run', str(file)], str(root))
        captured = capsys.readouterr()
        events = [json.loads(line) for line in captured.err.splitlines()]
        assert [e['progress'] for e in events] == ['step_started', 'step_finished']
        assert all(e['request_id'] == 'trial' and e['step'] == 1 for e in events)
        assert captured.out == '' and result['steps'][0]['ok']


class TestSimulationWarnings:
    def test_ground_and_ac_stimulus_are_checked_independently(self):
        from kicad_cli_extended.core import sim
        text = ".title RC\nV1 /IN GND DC 0\nR1 /IN /OUT 1.6k\nC1 /OUT GND 100n\n.ac dec 100 10 100k\n.end\n"
        assert [w["code"] for w in sim.netlist_warnings(text)] == ["no_ground_node", "ac_source_missing"]
        assert [w["code"] for w in sim.netlist_warnings(text.replace("GND", "0"))] == ["ac_source_missing"]
        assert sim.netlist_warnings(text.replace("GND", "0").replace("DC 0", "DC 0 AC 1")) == []
        assert sim.netlist_warnings(text.replace("DC 0", "DC 0 AC 0"))[1]["code"] == "ac_source_missing"


class TestUnregisteredLibraries:
    def test_show_and_list_files_without_registering_tables(self, tmp_path):
        lib = library.new_symbol_library()
        lib.append(library.make_box_symbol("ONE", library.parse_pin_specs(["1:A,2:B"])))
        path = tmp_path / "vendor.kicad_sym"
        path.write_text(sexpr.dumps(lib), encoding="utf-8")
        info = library.symbol_info(None, str(path))
        assert info["pin_count"] == 2 and info["pins"][0]["number"] == "1"
        assert library.symbol_info(None, str(path) + ":ONE")["pins"] == info["pins"]
        assert library.list_library(None, "sym", str(path))[0]["name"] == "ONE"
        lib.append(library.make_box_symbol("TWO", library.parse_pin_specs(["1:X"])))
        path.write_text(sexpr.dumps(lib), encoding="utf-8")
        with pytest.raises(ValueError, match="FILE.kicad_sym:NAME"):
            library.symbol_info(None, str(path))
        assert library.symbol_info(None, str(path) + ":TWO")["pin_count"] == 1
        pretty = tmp_path / "parts.pretty"
        pretty.mkdir()
        fp = library.make_footprint("BODY", library.generate_pads("chip", 2, 1.0))
        mod = pretty / "BODY.kicad_mod"
        mod.write_text(sexpr.dumps(fp), encoding="utf-8")
        assert library.footprint_info(None, str(mod))["pad_count"] == 2
        assert library.list_library(None, "fp", str(pretty))[0]["name"] == "BODY"


class TestLibrarySearchTerms:
    def test_punctuation_normalization_preserves_explicit_globs(self):
        assert library.search_terms("usb-c receptacle") == ["usb", "c", "receptacle"]
        assert library.search_terms("usb_c receptacle") == ["usb", "c", "receptacle"]
        terms = library.search_terms("R_0603_*")
        assert terms == ["r_0603_*"]
        assert library._score(terms, {"name": "R_0603_1608Metric"}) > 0
        assert library._score(terms, {"name": "R_0201_0603Metric"}) == 0
        assert library._score(["dfn", "2x2"], {"name": "VSON-8_2x2mm"}) > 0


class TestLibraryVerificationCoverage:
    def test_rotated_pad_boxes_enclose_arbitrary_angles(self):
        import math
        p = {"x": 0, "y": 0, "size": [2, 1], "angle": 45}
        a, b, c, d = library._pad_box(p)
        assert c == pytest.approx(3 / (2 * math.sqrt(2)))
        assert d == pytest.approx(c) and a == -c and b == -d

    def test_unsupported_ipc_claim_cannot_return_ok(self, tmp_dir):
        sess = session_for(tmp_dir)
        pretty = Path(tmp_dir, "P.pretty")
        pretty.mkdir()
        mod = pretty / "X.kicad_mod"
        mod.write_text(sexpr.dumps(library.make_footprint("X", library.generate_pads("chip", 2, 2))), encoding="utf-8")
        report = library.footprint_check(sess, str(mod), ipc_class="B")
        assert not report["ok"]
        assert report["unsupported"][0]["code"] == "ipc_class_not_evaluated"
        assert report["coverage"]["source_equivalence"] is False
        tree = sexpr.load(str(mod))
        tree.find("pad")[3] = "trapezoid"
        mod.write_text(sexpr.dumps(tree), encoding="utf-8")
        report = library.footprint_check(sess, str(mod))
        assert not report["ok"]
        assert report["unsupported"][0]["code"] == "unsupported_pad_geometry"
        assert report["unsupported"][0]["shape"] == "trapezoid"


class TestModelCopyTransactions:
    @pytest.mark.parametrize('registered', [False, True])
    @pytest.mark.parametrize('external_copy', [False, True])
    def test_dry_run_isolates_library_identifiers_and_model_destinations(self, tmp_path, monkeypatch,
                                                                       registered, external_copy,
                                                                       empty_global_lib_tables):
        from kicad_cli_extended import cli as m
        root = tmp_path / 'project'
        root.mkdir()
        sess = session_for(str(root))
        pretty = tmp_path / 'vendor.pretty'
        pretty.mkdir()
        mod = pretty / 'BODY.kicad_mod'
        mod.write_text(sexpr.dumps(library.make_footprint('BODY', library.generate_pads('chip', 2, 2))), encoding="utf-8")
        (root / 'fp-lib-table').write_text(sexpr.dumps(S('fp_lib_table', S('version', 7),
            S('lib', S('name', Q('vendor')), S('type', Q('KiCad')), S('uri', Q(str(pretty))),
              S('options', Q('')), S('descr', Q(''))))), encoding="utf-8")
        source = tmp_path / 'vendor.step'
        source.write_bytes(b'model bytes')
        destination = tmp_path / 'exported' if external_copy else root / '3d'
        before = mod.read_bytes()
        monkeypatch.setattr(m, 'validate_files', lambda paths: [])
        result = m.run_in_process(['--dry-run', 'lib', 'fp', 'model', 'set',
            'vendor:BODY' if registered else str(mod), str(source), '--copy-into',
            str(destination) if external_copy else '3d'], session=sess)
        assert result['dry_run'] is True
        assert mod.read_bytes() == before and not destination.exists()
        assert str(mod) in result['would_write']
        assert str(destination / source.name) in result['would_write']

class TestProjectlessSafety:
    def test_import_preview_keeps_existing_target_overwrite_guard(self, tmp_path, monkeypatch):
        from kicad_cli_extended.cli import run_in_process
        from kicad_cli_extended.core import importer
        cwd = tmp_path / 'cwd'
        cwd.mkdir()
        source = cwd / 'source.PcbDoc'
        source.write_bytes(b'source')
        target = tmp_path / 'accepted'
        target.mkdir()
        (target / 'board.kicad_pro').write_bytes(b'accepted')
        before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        monkeypatch.chdir(cwd)
        def unexpected_import(*args, **kwargs):
            pytest.fail('Import bypassed the existing target overwrite guard')
        monkeypatch.setattr(importer, 'import_into', unexpected_import)
        for mode in ([], ['--dry-run']):
            with pytest.raises(FileExistsError, match='already holds project'):
                run_in_process([*mode, 'project', 'import', str(source), '--dir', str(target), '--name', 'board'])
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before

    @pytest.mark.parametrize('mode', ['--read-only', '--dry-run'])
    @pytest.mark.parametrize('name', ['SKILL.md', 'COMMANDS.md'])
    def test_skill_choices_survive_safety_modes(self, tmp_path, monkeypatch, mode, name):
        from kicad_cli_extended.cli import run_in_process
        monkeypatch.chdir(tmp_path)
        for selection in ([], ['--file', name]):
            normal = run_in_process(['skill', 'show', *selection])
            safe = run_in_process([mode, 'skill', 'show', *selection])
            assert safe == normal

    @pytest.mark.parametrize('outside', [False, True])
    @pytest.mark.parametrize('absolute', [False, True])
    def test_read_only_copies_explicit_inputs_without_a_project(self, tmp_path, monkeypatch, outside, absolute):
        from kicad_cli_extended.cli import run_in_process
        cwd = tmp_path / 'cwd'
        cwd.mkdir()
        source = (tmp_path if outside else cwd) / 'part.kicad_mod'
        source.write_text('(footprint "part")\n', encoding='utf-8')
        monkeypatch.chdir(cwd)
        before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        value = str(source) if absolute else os.path.relpath(source, cwd)
        normal = run_in_process(['raw', 'check', value])
        assert run_in_process(['--read-only', 'raw', 'check', value]) == normal
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


class TestFabDiscoveryBudget:
    @pytest.mark.parametrize('cancel', [False, True])
    def test_serve_reaps_delayed_git_and_recovers(self, tmp_path, monkeypatch, cancel):
        import subprocess
        import threading
        from kicad_cli_extended.core.serve import Server
        from kicad_cli_extended.core import runtime
        popen = subprocess.Popen
        process_command = runtime.process_command
        started = threading.Event()
        processes = []

        def delayed_command(cmd):
            if cmd[0] == 'git':
                cmd = [sys.executable, '-c', 'import time; time.sleep(1.5); print("commit")']
            return process_command(cmd)

        def track_process(cmd, **kwargs):
            proc = popen(cmd, **kwargs)
            processes.append(proc)
            started.set()
            return proc

        monkeypatch.setattr(runtime, 'process_command', delayed_command)
        monkeypatch.setattr(subprocess, 'Popen', track_process)
        def dispatch(args, project):
            if args == ['fab', 'package']:
                return fab._source_revision(str(tmp_path))
            return {'recovered': True}

        def request(rid, method='run', **params):
            return json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}) + '\n'

        def stream():
            yield request(1, args=['fab', 'package'], timeout_ms=5000 if cancel else 100)
            if cancel:
                assert started.wait(5), 'Git subprocess did not start'
                yield request(2, method='cancel', request_id=1)
            yield request(3, args=['info', 'version'])
            yield request(4, method='shutdown')

        out = io.StringIO()
        server = Server(dispatch, out=out, err=io.StringIO())
        server.serve_forever(stream())
        replies = {r['id']: r for r in map(json.loads, out.getvalue().splitlines())}
        assert 'error' in replies[1], replies[1]
        error = replies[1]['error']['data']
        assert error['category'] == 'timeout' and error['details']['exit_code'] == 6
        assert len(processes) == 1, 'Git discovery continued after the request ended'
        assert processes[0].poll() is not None
        assert processes[0].returncode != 0, 'Git slept to completion instead of being stopped'
        assert 1 not in server.requests
        assert replies[3]['result'] == {'recovered': True}
        if cancel:
            assert replies[2]['result']['cancelled'] is True
        assert replies[4]['result']['shutdown'] is True


class TestInputBoundaryRegressions:
    @staticmethod
    def assert_input_failure(result):
        import jsonschema
        assert result.exit_code == 2, result.output
        error = json.loads(result.stderr)
        schema = json.loads((Path(__file__).parents[1] / 'schemas/error.schema.json').read_text(encoding='utf-8'))
        jsonschema.Draft202012Validator(schema).validate(error)
        assert error['category'] == 'input'
        assert result.stdout == ''

    @pytest.mark.parametrize('op', [[], {}, None, True, 17])
    def test_invalid_predicate_type_preserves_files_and_history(self, tmp_path, monkeypatch, op):
        from click.testing import CliRunner
        from kicad_cli_extended import cli as m
        sess = session_for(str(tmp_path))
        monkeypatch.setattr(m, '_session', sess)
        plan = tmp_path / 'invalid.json'
        plan.write_text(json.dumps({'version': 1, 'steps': [
            {'args': ['info', 'version']},
            {'args': ['info', 'version'], 'expect': [{'path': 'version', 'op': op}]}]}), encoding='utf-8')
        before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        history = sess.list_history()
        result = CliRunner().invoke(m.cli, ['--json', 'run', str(plan)])
        self.assert_input_failure(result)
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
        assert sess.list_history() == history

    @pytest.mark.parametrize('action', ['set', 'remove'])
    @pytest.mark.parametrize('index', [-1, 2])
    @pytest.mark.parametrize('with_models', [False, True])
    def test_model_index_preserves_files_and_history(self, tmp_path, monkeypatch, action, index, with_models):
        from click.testing import CliRunner
        from kicad_cli_extended import cli as m
        sess = session_for(str(tmp_path))
        monkeypatch.setattr(m, '_session', sess)
        footprint = tmp_path / 'X.kicad_mod'
        footprint.write_text('(footprint "X"' + (' (model "original.step")' if with_models else '') + ')', encoding='utf-8')
        before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        history = sess.list_history()
        args = ['--json', 'lib', 'fp', 'model', action, str(footprint)]
        if action == 'set':
            args += ['replacement.step', '--no-check']
        result = CliRunner().invoke(m.cli, [*args, '--index', str(index)])
        self.assert_input_failure(result)
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
        assert sess.list_history() == history


class TestFabDrcRejection:
    def test_cli_returns_drc_failure_preserves_accepted_and_cleans_stage(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from kicad_cli_extended import cli as m
        root = tmp_path / 'project'
        root.mkdir()
        sess = session_for(str(root))
        (root / 'p.kicad_pcb').write_text('(kicad_pcb)', encoding='utf-8')
        accepted = tmp_path / 'accepted'
        accepted.mkdir()
        (accepted / 'approved.zip').write_bytes(b'accepted')
        monkeypatch.setattr(m, '_session', sess)
        monkeypatch.setattr(fab.bom_mod, 'components', lambda *a: [])
        drc = {'total': 1, 'errors': 1, 'warnings': 0, 'unconnected': 0,
               'clean': False, 'by_type': {'shorting_items': 1}}
        def rejected_drc(sess, output):
            Path(output).write_text(json.dumps(drc), encoding='utf-8')
            return drc
        monkeypatch.setattr(fab.checks_mod, 'run_drc', rejected_drc)
        def unexpected_export(*a, **kw):
            pytest.fail('A DRC rejection must stop before exporting')
        monkeypatch.setattr(fab.kb, 'run_kicad_cli', unexpected_export)
        before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        result = CliRunner().invoke(m.cli, ['--json', 'fab', 'package', '--preset', 'jlcpcb',
                                           '--fail-on-drc', '-o', str(accepted)])
        assert result.exit_code == 3, result.output
        assert result.stdout, 'DRC rejection discarded the incomplete result'
        data = json.loads(result.stdout)
        assert data['complete'] is False and data['failed_steps'] == ['drc']
        assert data['files'] == [] and data['output_dir'] == str(accepted)
        assert data['drc'] == dict(drc, rules_preset=None)
        assert 'kce check diagnose' in data['next'][0]
        error = json.loads(result.stderr)
        assert error['category'] == 'design' and error['hint'] == 'kce check diagnose'
        assert 'omit --fail-on-drc' not in result.stderr
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
        assert not list(tmp_path.glob('.accepted-stage-*'))
