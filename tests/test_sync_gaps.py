"""ECO and imported-board sync read-back against KiCad's real PCB API."""
import uuid
from pathlib import Path

import pytest

from kicad_cli_extended.utils import kicad_backend as kb
from kicad_cli_extended.core import sexpr

pytestmark = pytest.mark.e2e


@pytest.fixture
def sync_board(tmp_path):
    lib = tmp_path / 'parts.pretty'
    lib.mkdir()
    for name, gap in [('Old', 2.54), ('New', 24.04)]:
        (lib / (name + '.kicad_mod')).write_text(f'''(footprint "{name}" (version 20241229) (generator "pcbnew") (layer "F.Cu")
        (pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))
        (pad "2" thru_hole circle (at {gap} 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask")))''', encoding='utf-8')
    board = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': board})
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'add_fp', 'lib_path': str(lib), 'name': 'Old', 'lib_nickname': 'parts', 'ref': 'C2', 'x': 10, 'y': 10,
         'pad_nets': {'1': 'VCC', '2': 'GND'}},
        {'action': 'add_track', 'points': [[12.54, 10], [20, 10]], 'net': 'GND', 'layer': 'F.Cu', 'width': 0.25}]})
    return board, str(lib)


def sync_action(lib, lock=False):
    return {'action': 'update_from_netlist', 'components': [
        {'ref': 'C2', 'value': '220u', 'footprint': 'parts:New', 'lib_path': lib, 'fp_name': 'New',
         'path': '/' + str(uuid.UUID(int=1)), 'fields': {}}],
        'nets': [{'name': 'VCC', 'nodes': [{'ref': 'C2', 'pin': '1'}]},
                 {'name': 'GND', 'nodes': [{'ref': 'C2', 'pin': '2'}, {'ref': 'C2', 'pin': '3'}]}],
        'lock_existing': lock, 'delete_extra': False}


def test_sync_reports_displaced_pads_adoption_and_unmatched_pins(sync_board):
    board, lib = sync_board
    before = kb.run_pcbnew('footprint', {'path': board, 'ref': 'C2'})
    tracks = [sexpr.compact(x) for x in sexpr.load(board).find_all('segment')]
    result = kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [sync_action(lib)]})['results'][0]['result']
    assert result['existing_moved'] == 0 and result['existing_unchanged'] == 1
    assert result['adopted'][0]['ref'] == 'C2'
    assert result['unmatched_pins'] == ['C2:3']
    pad = result['replaced'][0]['pads_moved'][0]
    assert pad == {'pad': '2', 'net': 'GND', 'from': [12.54, 10.0], 'to': [34.04, 10.0], 'dist_mm': 21.5}
    assert result['tracks_ending_on_old_pads'][0]['pad'] == '2'
    assert 'Imported board' in result['hint']
    after = kb.run_pcbnew('footprint', {'path': board, 'ref': 'C2'})
    assert after['uuid'] == before['uuid']
    assert [sexpr.compact(x) for x in sexpr.load(board).find_all('segment')] == tracks
    again = kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [sync_action(lib)]})['results'][0]['result']
    assert again['replaced'] == [] and again['adopted'] == [] and again['existing_moved'] == 0


def test_lock_existing_refuses_pad_displacement_without_writing(sync_board, tmp_path):
    board, lib = sync_board
    before = Path(board).read_bytes()
    output = tmp_path / 'attempt.kicad_pcb'
    with pytest.raises(kb.KicadError, match='is locked;'):
        kb.run_pcbnew('edit', {'path': board, 'out': str(output), 'actions': [sync_action(lib, True)]})
    assert Path(board).read_bytes() == before
    assert not output.exists()


def test_sync_counts_anonymous_imported_footprints_individually(sync_board):
    board, lib = sync_board
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'add_fp', 'lib_path': lib, 'name': 'Old', 'ref': '', 'x': x, 'y': 30} for x in (10, 20)]})
    result = kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [sync_action(lib)]})['results'][0]['result']
    assert result['existing_unchanged'] == 3 and result['existing_moved'] == 0
    assert len(kb.run_pcbnew('footprints', {'path': board})) == 3
