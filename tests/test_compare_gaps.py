from unittest.mock import patch

from kicad_cli_extended.core import diff, netlist


def test_compare_reports_new_and_removed_endpoints():
    def nl(extra):
        return {'components': [], 'nets': [dict(name=n, code=i, nodes=[dict(ref=r, pin=p, pinfunction='', pintype='passive')
                  for r, p in pins]) for i, (n, pins) in enumerate(extra.items())]}
    before = nl({'VCC': [('U2', '14')], 'GND': [('U2', '7')]})
    after = nl({'VCC': [('U2', '14'), ('C10', '1')], 'GND': [('U2', '7'), ('C10', '2')]})
    with patch.object(diff, '_netlist', side_effect=[before, after]):
        result = diff.schematic_diff(object(), object())
    assert result['net_changes'] == [dict(net='GND', pins_added=['C10:2'], pins_removed=[]),
                                     dict(net='VCC', pins_added=['C10:1'], pins_removed=[])]
    assert {c['pin'] for c in result['connection_changes']} == {'C10:1', 'C10:2'}
    assert all(c['before'] is None for c in result['connection_changes'])


import pytest
from test_sync_gaps import sync_board, sync_action


@pytest.mark.e2e
def test_compare_reads_replaced_pad_geometry(sync_board, tmp_path):
    import shutil
    from pathlib import Path
    from kicad_cli_extended.core.project import KicadProject
    from kicad_cli_extended.utils import kicad_backend as kb
    board, lib = sync_board
    baseline = tmp_path / 'baseline'
    baseline.mkdir()
    shutil.copyfile(board, baseline / 'p.kicad_pcb')
    (baseline / 'p.kicad_pro').write_text('{}')
    Path(board).with_suffix('.kicad_pro').write_text('{}')
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [sync_action(lib)]})
    result = diff.board_diff(KicadProject(str(baseline / 'p.kicad_pro')), KicadProject(str(Path(board).with_suffix('.kicad_pro'))))
    assert result['footprints_changed'][0]['pads_moved'][0]['dist_mm'] == 21.5


def test_eco_gate_detects_equal_count_geometry_and_project_rules(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from click.testing import CliRunner
    from kicad_cli_extended import cli
    from kicad_cli_extended.core.project import KicadProject
    from kicad_cli_extended.core.session import Session
    def project(name, x=10, track_width=0.25, edge=0, rule=0.2):
        root = tmp_path / name
        root.mkdir()
        (root / 'p.kicad_pro').write_text(json.dumps({'board': {'design_settings': {'rules': {'min_clearance': rule}}}}))
        (root / 'p.kicad_pcb').write_text(f'''(kicad_pcb (footprint "R:R" (layer "F.Cu") (at {x} 10)
          (property "Reference" "R3") (property "Value" "1k"))
          (net 1 "OTHER") (segment (start 0 0) (end 3 4) (width {track_width}) (layer "F.Cu") (net 1))
          (gr_rect (start {edge} 0) (end 20 20) (layer "Edge.Cuts")))''')
        return KicadProject(str(root / 'p.kicad_pro'))
    before, moved = project('before'), project('moved', x=11)
    result = diff.compare(before.pro_path, moved.pro_path, allow=['C2', 'C10', 'net:VCC', 'net:GND'])
    assert not result['ok'] and result['unexpected'] == [{'kind': 'footprint_moved', 'ref': 'R3'}]
    changed = project('changed', track_width=0.4, edge=1, rule=0.3)
    result = diff.compare(before.pro_path, changed.pro_path, allow=[])
    assert {c['ref'] for c in result['unexpected']} == {'net:OTHER', 'outline', 'project:p.kicad_pro'}
    assert not result['identical']
    sess = Session()
    sess.set_project(moved)
    monkeypatch.setattr(cli, '_session', sess)
    output = CliRunner().invoke(cli.cli, ['--json', 'project', 'compare', before.pro_path,
                                         '--allow', 'C2,C10,net:VCC,net:GND', '--fail-on-unexpected'])
    assert output.exit_code == 4, output.output
    assert json.loads(output.stdout)['unexpected'] == [{'kind': 'footprint_moved', 'ref': 'R3'}]
    assert json.loads(output.stderr)['category'] == 'validation'


def test_compare_identifies_anonymous_footprints_by_uuid(tmp_path):
    from kicad_cli_extended.core.project import KicadProject
    root = tmp_path / 'anonymous'
    root.mkdir()
    (root / 'p.kicad_pro').write_text('{}')
    (root / 'p.kicad_pcb').write_text('''(kicad_pcb
      (footprint "L:F" (layer "F.Cu") (at 1 1) (property "Reference" "") (uuid "one"))
      (footprint "L:F" (layer "F.Cu") (at 2 2) (property "Reference" "") (uuid "two")))''')
    project = KicadProject(str(root / 'p.kicad_pro'))
    result = diff.board_diff(project, project)
    assert result['totals']['footprints'] == [2, 2]
    assert not result['footprints_changed'] and not result['footprints_moved']
