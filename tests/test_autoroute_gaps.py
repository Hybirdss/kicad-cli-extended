import importlib.util
import json
import math
import os
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import pytest

from test_e2e import read_text, rj, run, tmp_dir  # noqa: F401


def test_auto_grid_refines_only_until_success(monkeypatch):
    # Load the worker without KiCad to exercise the orchestration and real return contract.
    monkeypatch.setitem(sys.modules, 'pcbnew', SimpleNamespace(FromMM=lambda x: x))
    path = Path(__file__).parents[1] / 'src/kicad_cli_extended/utils/pcbnew_worker.py'
    spec = importlib.util.spec_from_file_location('routing_worker', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    implementation = worker._route
    calls = []
    def step(board, action):
        calls.append(action['grid'])
        return {'routed': [{'net': str(action['grid'])}], 'failed': [] if action['grid'] == 0.1 else [{'net': 'N'}],
                'unrouted_after': 0 if action['grid'] == 0.1 else 1, 'grid': action['grid']}
    with patch.object(worker, '_route', side_effect=step):
        result = implementation(object(), {'grid': 'auto'})
    assert calls == result['grid_tried'] == [0.25, 0.15, 0.1]
    assert result['failed'] == [] and len(result['routed']) == 3


def test_auto_grid_routes_real_board(tmp_path):
    from kicad_cli_extended.utils import kicad_backend as kb
    path = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': path})
    setup = '''
net = pcbnew.NETINFO_ITEM(board, 'SIGNAL')
board.Add(net)
for i, x in enumerate((5, 15)):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('J' + str(i + 1))
    board.Add(fp)
    fp.SetPosition(vec(x, 10))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber('1')
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetSize(vec(1.6, 1.6))
    pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, 10))
    pad.SetNet(net)
'''
    result = kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'script', 'code': setup},
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 20, 'height': 20},
        {'action': 'autoroute_builtin', 'grid': 'auto'}]})['results'][-1]['result']
    assert result['grid_tried'] == [0.25]
    assert result['routed'] and result['failed'] == [] and result['unrouted_after'] == 0
    assert kb.run_pcbnew('connectivity', {'path': path})['fully_routed']


def legacy_board_with_a_known_clearance_error():
    """A board as KiCad 5 saved it, without pad UUIDs: an SOIC-8 rotated 15 degrees whose GND pads 1 and
    5 are joined by a straight track that passes too close to pads 2 and 6, and two SIG pads below."""
    offsets = [(-2.475, -1.905), (-2.475, -0.635), (-2.475, 0.635), (-2.475, 1.905),
               (2.475, 1.905), (2.475, 0.635), (2.475, -0.635), (2.475, -1.905)]
    pads = "".join(f"    (pad {n} smd roundrect (at {x} {y} 15) (size 1.95 0.6) (layers F.Cu F.Paste F.Mask) "
                   f"(roundrect_rratio 0.25){' (net 1 GND)' if n in (1, 5) else ''})\n"
                   for n, (x, y) in enumerate(offsets, 1))
    a = math.radians(15)
    (x0, y0), (x1, y1) = [(30 + x * math.cos(a) + y * math.sin(a), 15 - x * math.sin(a) + y * math.cos(a))
                          for x, y in (offsets[0], offsets[4])]
    sig = "".join(f"""  (module TP (layer F.Cu) (tedit 5A0F774F) (tstamp 5C7D{k:04X})
    (at 30 {y})
    (fp_text reference S{k + 1} (at 0 -2.5) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
    (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers *.Cu *.Mask) (net 2 SIG))
  )
""" for k, y in enumerate((19.5, 27.5)))
    edges = "".join(f"  (gr_line (start {a} {b}) (end {c} {d}) (layer Edge.Cuts) (width 0.05))\n"
                    for a, b, c, d in [(0, 0, 60, 0), (60, 0, 60, 40), (60, 40, 0, 40), (0, 40, 0, 0)])
    return f"""(kicad_pcb (version 20171130) (host pcbnew "(5.1.12)-1")
  (general (thickness 1.6))
  (page A4)
  (layers (0 F.Cu signal) (31 B.Cu signal) (35 F.Paste user) (37 F.SilkS user) (39 F.Mask user)
    (44 Edge.Cuts user) (49 F.Fab user))
  (setup (last_trace_width 0.25) (trace_clearance 0.2) (zone_clearance 0.508) (trace_min 0.2)
    (via_size 0.8) (via_drill 0.4) (via_min_size 0.4) (via_min_drill 0.3) (edge_width 0.05))
  (net 0 "") (net 1 GND) (net 2 SIG)
  (net_class Default "" (clearance 0.2) (trace_width 0.25) (via_dia 0.8) (via_drill 0.4) (add_net GND) (add_net SIG))
  (module Package_SO:SOIC-8_3.9x4.9mm_P1.27mm (layer F.Cu) (tedit 5D9F72B1) (tstamp 5C7E0001)
    (at 30 15 15)
    (fp_text reference U1 (at 0 -3.4 15) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
{pads}  )
{sig}{edges}  (segment (start {x0:.6f} {y0:.6f}) (end {x1:.6f} {y1:.6f}) (width 0.25) (layer F.Cu) (net 1))
)
"""


def test_autoroute_does_not_blame_a_route_for_an_old_clearance_error(tmp_dir):
    # The GND track already violates clearance; routing SIG far from it adds nothing. KiCad reports
    # one error per track by default, naming pad 2 or pad 6 by item order, and the pads get new UUIDs
    # on every load, so the old finding looked new after the save and the route was refused.
    p = os.path.join(tmp_dir, 'legacy')
    run(['project', 'new', p, '--name', 'legacy'])
    os.remove(os.path.join(p, 'legacy.kicad_sch'))
    with open(os.path.join(p, 'legacy.kicad_pcb'), 'w', encoding='utf-8') as f:
        f.write(legacy_board_with_a_known_clearance_error())
    before = json.loads(run(['--json', '--project', p, 'check', 'drc']).stdout)
    assert before['by_type'].get('clearance'), before['by_type']
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'],
            check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    assert [c['net'] for c in json.loads(r.stdout)['routed']] == ['SIG']


def test_placement_does_not_blame_a_move_for_an_old_clearance_error(tmp_dir):
    # The same board: moving S1 next to S2 adds no error, but the old one names pads and a track that
    # KiCad 5 saved without UUIDs, so each load gave them new ones and the old error looked new.
    p = os.path.join(tmp_dir, 'legacy')
    run(['project', 'new', p, '--name', 'legacy'])
    os.remove(os.path.join(p, 'legacy.kicad_sch'))
    with open(os.path.join(p, 'legacy.kicad_pcb'), 'w', encoding='utf-8') as f:
        f.write(legacy_board_with_a_known_clearance_error())
    r = run(['--json', '--project', p, 'pcb', 'place', 'near', 'S1', '--to', 'S2'], check=False)
    assert r.returncode == 0, r.stderr[-2000:]


def test_auto_grid_stops_before_a_grid_too_large_for_the_board(tmp_path):
    from kicad_cli_extended.utils import kicad_backend as kb
    path = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': path})
    setup = '''
nets = {name: pcbnew.NETINFO_ITEM(board, name) for name in ('SIGNAL', 'ROUTED')}
for net in nets.values():
    board.Add(net)
for i, (name, x, y) in enumerate((('SIGNAL', 10, 10), ('SIGNAL', 90, 70), ('ROUTED', 40, 40), ('ROUTED', 50, 40))):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('J' + str(i + 1))
    board.Add(fp)
    fp.SetPosition(vec(x, y))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber('1')
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetSize(vec(1.6, 1.6))
    pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, y))
    pad.SetNet(nets[name])
'''
    # keepouts around both SIGNAL pads make that connection unroutable at every grid
    keepouts = [{'action': 'add_zone', 'keepout': True, 'layers': ['F.Cu', 'B.Cu'], 'rect': [x - 2, y - 2, 4, 4]}
                for x, y in ((10, 10), (90, 70))]
    result = kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'script', 'code': setup},
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 100, 'height': 80},
        *keepouts,
        {'action': 'autoroute_builtin', 'grid': 'auto'}]})['results'][-1]['result']
    # a 0.05 mm grid over 100 x 80 mm on two layers is 6.4 million cells, over the router's limit
    assert result['grid_tried'] == [0.25, 0.15, 0.1]
    assert [c['net'] for c in result['routed']] == ['ROUTED']
    assert [c['net'] for c in result['failed']] == ['SIGNAL']
    assert '0.05 mm' in result['warning']
    assert [n['net'] for n in kb.run_pcbnew('connectivity', {'path': path})['nets']] == ['SIGNAL']


POUR_SETUP = """
nets = {name: pcbnew.NETINFO_ITEM(board, name) for name in ('GND', 'SIG', 'SIG3')}
for net in nets.values():
    board.Add(net)
footprints = {}
for ref, number, name, x, y in params['pads']:  # pads with one reference share a footprint
    fp = footprints.get(ref)
    if fp is None:
        fp = footprints[ref] = pcbnew.FOOTPRINT(board)
        fp.SetReference(ref)
        board.Add(fp)
        fp.SetPosition(vec(x, y))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber(number)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetSize(vec(1.6, 1.6))
    pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, y))
    pad.SetNet(nets[name])
for name, points in params.get('shapes', []):  # copper polygons that carry a net
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_POLY)
    shape.SetFilled(True)
    shape.SetLayer(pcbnew.F_Cu)
    shape.SetPolyPoints([vec(x, y) for x, y in points])
    shape.SetWidth(FromMM(0.1))
    shape.SetNet(nets[name])
    board.Add(shape)
for name, start, mid, end in params.get('arcs', []):
    arc = pcbnew.PCB_ARC(board)
    arc.SetStart(vec(*start))
    arc.SetMid(vec(*mid))
    arc.SetEnd(vec(*end))
    arc.SetWidth(FromMM(0.25))
    arc.SetLayer(pcbnew.F_Cu)
    arc.SetNet(nets[name])
    board.Add(arc)
"""
# GND pours two lobes joined by a 3 mm neck between S1 and S2, so SIG's route must cross the neck
NECK = [[4, 12], [22, 12], [22, 18.5], [38, 18.5], [38, 12], [56, 12], [56, 28], [38, 28],
        [38, 21.5], [22, 21.5], [22, 28], [4, 28]]
SIG = [['S1', '1', 'SIG', 30, 14], ['S2', '1', 'SIG', 30, 26]]
LOBES = [['GP1', '1', 'GND', 10, 20], ['GP2', '1', 'GND', 50, 20]]
LEFT = [['GP1', '1', 'GND', 10, 20], ['GP2', '1', 'GND', 10, 25]]


def routed_board(tmp_dir, pads, actions, project_commands=(), **params):
    p = os.path.join(tmp_dir, 'board')
    run(['project', 'new', p, '--name', 'board'])
    os.remove(os.path.join(p, 'board.kicad_sch'))  # footprints without symbols would add parity findings
    for command in project_commands:
        run(['--project', p, *command])
    path = os.path.join(tmp_dir, 'actions.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump([{'action': 'script', 'code': POUR_SETUP, 'params': dict(params, pads=pads)},
                   {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 60, 'height': 40}, *actions], f)
    run(['--project', p, 'pcb', 'batch', path])
    run(['--project', p, 'pcb', 'zone', 'fill'])
    return p, os.path.join(p, 'board.kicad_pcb')


@pytest.mark.parametrize("pads, actions, params", [
    # SIG3's route also joins a track stub with no pad, so one route removes two unrouted connections
    (LOBES + [['A1', '1', 'SIG3', 6, 4], ['B1', '1', 'SIG3', 18, 4]],
     [{'action': 'add_track', 'net': 'SIG3', 'layer': 'F.Cu', 'points': [[12, 4], [12, 9]], 'width': 0.25}], {}),
    # GP3 lies outside the pour, so GND gains a route while it loses the neck
    (LOBES + [['GP3', '1', 'GND', 58, 36]], [], {}),
    # one footprint with two pads numbered MH, one in each lobe
    ([['J1', 'MH', 'GND', 10, 20], ['J1', 'MH', 'GND', 50, 20]], [], {}),
    # both GND pads in the left lobe; the right lobe holds only a GND via, or only a GND copper polygon
    (LEFT, [{'action': 'add_via', 'net': 'GND', 'x': 50, 'y': 20, 'diameter': 0.8, 'drill': 0.4}], {}),
    (LEFT, [], {'shapes': [['GND', [[46, 16], [52, 16], [52, 24], [46, 24]]]]}),
    # GP2 sits below the pour and reaches the right lobe through an arc track
    ([['GP1', '1', 'GND', 10, 20], ['GP2', '1', 'GND', 50, 35]], [],
     {'arcs': [['GND', [50, 35], [52.5, 31], [50, 26]]]}),
], ids=["stub-on-another-route", "gnd-routed-too", "pads-share-a-number", "via-island", "copper-shape", "arc"])
def test_autoroute_refuses_a_route_that_cuts_a_pour(tmp_dir, pads, actions, params):
    p, board = routed_board(tmp_dir, pads + SIG, [{'action': 'add_zone', 'net': 'GND', 'layers': ['F.Cu'],
                                                   'points': NECK}, *actions], **params)
    before = read_text(board)
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--layers', 'F.Cu', '--grid', '0.25'], check=False)
    assert r.returncode == 3, r.stdout[-2000:]
    lost = json.loads(r.stderr)['details']['lost_connections']
    assert [c['net'] for c in lost] == ['GND'], lost
    assert read_text(board) == before


def test_autoroute_keeps_pads_that_share_a_number_and_stay_connected(tmp_dir):
    # no pour: J1's first MH pad is tracked to C1 and its second waits for GND to be poured later
    pads = [['J1', 'MH', 'GND', 10, 20], ['C1', '1', 'GND', 14, 20], ['J1', 'MH', 'GND', 50, 30],
            ['S1', '1', 'SIG', 30, 6], ['S2', '1', 'SIG', 30, 14]]
    p, _ = routed_board(tmp_dir, pads, [{'action': 'add_track', 'net': 'GND', 'layer': 'F.Cu',
                                         'points': [[10, 20], [14, 20]], 'width': 0.25}])
    r = rj(['--project', p, 'pcb', 'autoroute', '--skip-net', 'GND', '--layers', 'F.Cu', '--grid', '0.25'])
    assert [c['net'] for c in r['routed']] == ['SIG']


def test_autoroute_leaves_the_fill_of_a_stale_pour_as_it_was(tmp_dir):
    # A GND pour that keeps its islands, made stale by a track added after the fill. Refilling the
    # routed board itself to judge the route once marked that fill as an island in the saved file,
    # and the ECO gate then reported the zone as changed.
    p, _ = routed_board(tmp_dir, [['GP1', '1', 'GND', 30, 15], ['GP2', '1', 'GND', 30, 25],
                                  ['S1', '1', 'SIG', 47, 2], ['S2', '1', 'SIG', 47, 38]],
                        [{'action': 'add_zone', 'net': 'GND', 'layers': ['F.Cu'], 'rect': [4, 4, 52, 32],
                          'island_removal': 'never', 'fill': False}, {'action': 'fill_zones'}])
    P = ['--project', p]
    run(P + ['pcb', 'track', '8,1;8,39', '--layer', 'F.Cu', '--width', '0.25', '--net', 'SIG3'])
    checkpoint = os.path.join(tmp_dir, 'checkpoint')
    shutil.copytree(p, checkpoint)
    assert rj(P + ['pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'])['lost_connections'] == []
    run(P + ['project', 'compare', checkpoint, '--allow', 'net:SIG', '--fail-on-unexpected'])


@pytest.mark.parametrize("rules, neck, zone_clearance, refused", [
    # SIG's net class asks for 1.0 mm, so its route along y=20 clears away a neck 0.35 mm above it
    ([['project', 'netclass', 'set', 'HV', '--clearance', '1.0', '--track-width', '0.25'],
      ['project', 'netclass', 'assign', 'SIG', 'HV']], (20.35, 21.15), None, True),
    # with 0.1 mm clearances a neck 0.2 mm above the route survives
    ([['project', 'netclass', 'set', 'Default', '--clearance', '0.1']], (20.2, 20.525), 0.1, False),
], ids=["net-class-clearance", "tight-clearance"])
def test_autoroute_judges_a_pour_by_the_projects_rules(tmp_dir, rules, neck, zone_clearance, refused):
    lo, hi = neck
    zone = {'action': 'add_zone', 'net': 'GND', 'layers': ['F.Cu'],
            'points': [[4, lo], [56, lo], [56, 36], [38, 36], [38, hi], [22, hi], [22, 36], [4, 36]]}
    if zone_clearance:
        zone['clearance'] = zone_clearance
    pads = [['GP1', '1', 'GND', 12, 30], ['GP2', '1', 'GND', 48, 30], ['S1', '1', 'SIG', 6, 20], ['S2', '1', 'SIG', 54, 20]]
    p, board = routed_board(tmp_dir, pads, [zone], project_commands=rules)
    before = read_text(board)
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'],
            check=False)
    if refused:
        assert r.returncode == 3, r.stdout[-2000:]
        assert [c['net'] for c in json.loads(r.stderr)['details']['lost_connections']] == ['GND']
        assert read_text(board) == before
    else:
        assert r.returncode == 0, r.stderr[-2000:]
        assert json.loads(r.stdout)['lost_connections'] == []


def kicad5_board(pads, pour, tracks):
    """A board as KiCad 5 (version 20171130) saved it, with the neck pour filled. KiCad 5 stored no pad UUIDs."""
    nets = {"GND": 1, "SIG": 2}
    modules = "".join(f"""  (module TP (layer F.Cu) (tedit 5A0F774F) (tstamp 5C7E{k:04X})
    (at {x} {y})
    (fp_text reference {ref} (at 0 -2.5) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
    (fp_text value TP (at 0 2.5) (layer F.Fab) (effects (font (size 1 1) (thickness 0.15))))
    (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers *.Cu *.Mask) (net {nets[name]} {name}))
  )
""" for k, (ref, _, name, x, y) in enumerate(pads))
    edges = "".join(f"  (gr_line (start {a} {b}) (end {c} {d}) (layer Edge.Cuts) (width 0.05))\n"
                    for a, b, c, d in [(0, 0, 60, 0), (60, 0, 60, 40), (60, 40, 0, 40), (0, 40, 0, 0)])
    segments = "".join(f"  (segment (start {a} {b}) (end {c} {d}) (width 0.25) (layer F.Cu) (net 1) (tstamp 5C7E03{k:02X}))\n"
                       for k, (a, b, c, d) in enumerate(tracks))
    points = " ".join(f"(xy {x} {y})" for x, y in NECK)
    zone = f"""  (zone (net 1) (net_name GND) (layer F.Cu) (hatch edge 0.508) (connect_pads (clearance 0.508))
    (min_thickness 0.254) (fill yes (arc_segments 32) (thermal_gap 0.508) (thermal_bridge_width 0.508))
    (polygon (pts {points}))
    (filled_polygon (pts {points})))
""" if pour else ""
    return f"""(kicad_pcb (version 20171130) (host pcbnew "(5.1.12)-1")
  (general (thickness 1.6))
  (page A4)
  (layers (0 F.Cu signal) (31 B.Cu signal) (37 F.SilkS user) (38 B.Mask user) (39 F.Mask user)
    (44 Edge.Cuts user) (49 F.Fab user))
  (setup (last_trace_width 0.25) (trace_clearance 0.2) (zone_clearance 0.508) (trace_min 0.2)
    (via_size 0.8) (via_drill 0.4) (via_min_size 0.4) (via_min_drill 0.3) (edge_width 0.05))
  (net 0 "") (net 1 GND) (net 2 SIG)
  (net_class Default "" (clearance 0.2) (trace_width 0.25) (via_dia 0.8) (via_drill 0.4) (add_net GND) (add_net SIG))
{modules}{edges}{segments}{zone})
"""


@pytest.mark.parametrize("pads, pour, tracks, refused", [
    (LOBES, True, [], True),
    # no pour: a GND track joins GP1 and GP2, and SIG routes past it
    ([['GP1', '1', 'GND', 10, 30], ['GP2', '1', 'GND', 20, 30]], False, [(10, 30, 20, 30)], False),
], ids=["cut-pour", "untouched-track"])
def test_autoroute_judges_a_kicad5_board_by_its_copper(tmp_dir, pads, pour, tracks, refused):
    # pcbnew gives a KiCad 5 board new pad UUIDs on every load, so the gate must not match copper by UUID
    p = os.path.join(tmp_dir, 'legacy')
    run(['project', 'new', p, '--name', 'legacy'])
    os.remove(os.path.join(p, 'legacy.kicad_sch'))
    board = os.path.join(p, 'legacy.kicad_pcb')
    with open(board, 'w', encoding='utf-8') as f:
        f.write(kicad5_board(pads + SIG, pour, tracks))
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--layers', 'F.Cu', '--grid', '0.25'], check=False)
    if refused:
        assert r.returncode == 3, r.stdout[-2000:]
        assert [c['net'] for c in json.loads(r.stderr)['details']['lost_connections']] == ['GND']
    else:
        assert r.returncode == 0, r.stderr[-2000:]
        assert [c['net'] for c in json.loads(r.stdout)['routed']] == ['SIG']


def rotated_soic_board(gnd_track):
    """A KiCad 5 board with an SOIC-8 rotated 15 degrees whose pads 1 and 5 are GND. pcbnew's first
    save moves pad 1 by 1 nm, so the copper must not be matched across loads by position."""
    offsets = [(-2.475, -1.905), (-2.475, -0.635), (-2.475, 0.635), (-2.475, 1.905),
               (2.475, 1.905), (2.475, 0.635), (2.475, -0.635), (2.475, -1.905)]
    pads = "".join(f"    (pad {n} smd roundrect (at {x} {y} 15) (size 1.95 0.6) (layers F.Cu F.Paste F.Mask) "
                   f"(roundrect_rratio 0.25){' (net 1 GND)' if n in (1, 5) else ''})\n"
                   for n, (x, y) in enumerate(offsets, 1))
    sig = "".join(f"""  (module TP (layer F.Cu) (tedit 5A0F774F) (tstamp 5C7D{k:04X})
    (at 30 {y})
    (fp_text reference S{k + 1} (at 0 -2.5) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
    (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers *.Cu *.Mask) (net 2 SIG))
  )
""" for k, y in enumerate((19.5, 27.5)))
    if gnd_track:  # pads 1 and 5 joined by a track that runs around the package; no pour
        a = math.radians(15)
        pad1, pad5 = [(30 + x * math.cos(a) + y * math.sin(a), 15 - x * math.sin(a) + y * math.cos(a))
                      for x, y in (offsets[0], offsets[4])]
        path = [pad1, (25.5, 11), (35.5, 11), (35.5, pad5[1]), pad5]
        copper = "".join(f"  (segment (start {x0:.6f} {y0:.6f}) (end {x1:.6f} {y1:.6f}) (width 0.25) (layer F.Cu) (net 1))\n"
                         for (x0, y0), (x1, y1) in zip(path, path[1:]))
    else:  # pads 1 and 5 joined only by a pour whose neck lies across SIG's path
        neck = [(22, 10), (29, 10), (29, 22), (31, 22), (31, 10), (38, 10), (38, 25), (31, 25), (31, 23.5),
                (29, 23.5), (29, 25), (22, 25)]
        copper = ("  (zone (net 1) (net_name GND) (layer F.Cu) (hatch edge 0.508) (connect_pads (clearance 0.508))\n"
                  "    (min_thickness 0.254) (fill yes (arc_segments 32) (thermal_gap 0.508) (thermal_bridge_width 0.508))\n"
                  "    (polygon (pts " + " ".join(f"(xy {x} {y})" for x, y in neck) + ")))\n")
    edges = "".join(f"  (gr_line (start {a} {b}) (end {c} {d}) (layer Edge.Cuts) (width 0.05))\n"
                    for a, b, c, d in [(0, 0, 60, 0), (60, 0, 60, 40), (60, 40, 0, 40), (0, 40, 0, 0)])
    return f"""(kicad_pcb (version 20171130) (host pcbnew "(5.1.12)-1")
  (general (thickness 1.6))
  (page A4)
  (layers (0 F.Cu signal) (31 B.Cu signal) (35 F.Paste user) (37 F.SilkS user) (39 F.Mask user)
    (44 Edge.Cuts user) (49 F.Fab user))
  (setup (last_trace_width 0.25) (trace_clearance 0.2) (zone_clearance 0.508) (trace_min 0.2)
    (via_size 0.8) (via_drill 0.4) (via_min_size 0.4) (via_min_drill 0.3) (edge_width 0.05))
  (net 0 "") (net 1 GND) (net 2 SIG)
  (net_class Default "" (clearance 0.2) (trace_width 0.25) (via_dia 0.8) (via_drill 0.4) (add_net GND) (add_net SIG))
  (module Package_SO:SOIC-8_3.9x4.9mm_P1.27mm (layer F.Cu) (tedit 5D9F72B1) (tstamp 5C7E0001)
    (at 30 15 15)
    (fp_text reference U1 (at 0 -3.4 15) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
{pads}  )
{sig}{edges}{copper})
"""


@pytest.mark.parametrize("gnd_track, refused", [(False, True), (True, False)], ids=["cut-pour", "untouched-track"])
def test_autoroute_judges_a_rotated_footprint_on_a_kicad5_board(tmp_dir, gnd_track, refused):
    p = os.path.join(tmp_dir, 'soic')
    run(['project', 'new', p, '--name', 'soic'])
    os.remove(os.path.join(p, 'soic.kicad_sch'))
    with open(os.path.join(p, 'soic.kicad_pcb'), 'w', encoding='utf-8') as f:
        f.write(rotated_soic_board(gnd_track))
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'],
            check=False)
    if refused:
        assert r.returncode == 3, r.stdout[-2000:]
        assert [c['net'] for c in json.loads(r.stderr)['details']['lost_connections']] == ['GND']
    else:
        assert r.returncode == 0, r.stderr[-2000:]
        assert json.loads(r.stdout)['lost_connections'] == []
