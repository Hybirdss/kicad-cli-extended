import importlib.util
import json
import math
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from test_e2e import run, tmp_dir  # noqa: F401


def test_auto_grid_refines_only_until_success(monkeypatch):
    # Load the worker without KiCad to exercise the orchestration and real return contract.
    monkeypatch.setitem(sys.modules, 'pcbnew', SimpleNamespace(FromMM=lambda x: x))
    path = Path(__file__).parents[1] / 'src/kicad_cli_extended/utils/pcbnew_worker.py'
    spec = importlib.util.spec_from_file_location('routing_worker', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    implementation = worker.a_autoroute_builtin
    calls = []
    def step(board, action):
        calls.append(action['grid'])
        return {'routed': [{'net': str(action['grid'])}], 'failed': [] if action['grid'] == 0.1 else [{'net': 'N'}],
                'unrouted_after': 0 if action['grid'] == 0.1 else 1, 'grid': action['grid']}
    with patch.object(worker, 'a_autoroute_builtin', side_effect=step):
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
