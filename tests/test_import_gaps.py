"""Source-preserving intake, including a read-back of the selected target board."""
import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from kicad_cli_extended.core import importer, kicad_cli_cmds
from kicad_cli_extended.utils import kicad_backend as kb


@pytest.mark.parametrize('produce,rc', [(False, 0), (True, 1), (True, 0)])
def test_import_requires_fresh_readable_output(tmp_path, produce, rc):
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    source = source_dir / 'board.PcbDoc'
    source.write_bytes(b'original')
    target = tmp_path / 'target' / 'p.kicad_pcb'
    target.parent.mkdir()
    target.write_bytes(b'accepted')
    def fake(args, **kw):
        if produce:
            Path(args[args.index('-o') + 1]).write_bytes(b'(kicad_pcb)')
        return dict(returncode=rc, stdout='', stderr='failure' if rc else '')
    with patch.object(kb, 'run_kicad_cli', side_effect=fake), patch.object(kb, 'run_pcbnew', return_value={'footprints': 6}):
        if produce and not rc:
            _, report = importer.import_into(['pcb', 'import', '-o', str(target), str(source)], str(source), str(target))
            assert report['path'] == str(target) and report['footprints'] == 6
            assert report['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
            assert report['source_unchanged']
        else:
            with pytest.raises(kb.KicadError):
                importer.import_into(['pcb', 'import', '-o', str(target), str(source)], str(source), str(target))
            assert target.read_bytes() == b'accepted'
    assert list(source_dir.iterdir()) == [source]
    assert source.read_bytes() == b'original'
    assert not list(target.parent.glob('.kce-import-*'))


def test_import_rejects_source_membership_change(tmp_path):
    source = tmp_path / 'source' / 'board.PcbDoc'
    source.parent.mkdir()
    source.write_text('source', encoding='utf-8')
    target = tmp_path / 'target' / 'p.kicad_pcb'
    def mutate(args, **kw):
        (source.parent / 'board.kicad_pcb').write_text('unexpected', encoding='utf-8')
        return dict(returncode=0, stdout='', stderr='')
    with patch.object(kb, 'run_kicad_cli', side_effect=mutate):
        with pytest.raises(kb.KicadError, match='changed the source tree'):
            importer.import_into(['pcb', 'import', '-o', str(target), str(source)], str(source), str(target))
    assert not target.exists()


def test_import_defaults_to_selected_project_board():
    from types import SimpleNamespace
    project = SimpleNamespace(pcb_path='/target/target.kicad_pcb')
    spec = kicad_cli_cmds.load_spec()['commands']['pcb import']
    assert kicad_cli_cmds.default_output(project, 'pcb import', spec, {}) == project.pcb_path
    assert kicad_cli_cmds.default_output(None, 'pcb import', spec, {}) is None


@pytest.mark.e2e
def test_altium_intake_reads_selected_target_and_preserves_source(tmp_path):
    from test_e2e import rj, run, REPO
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    source = source_dir / 'PCB1.PcbDoc'
    fixture = Path(REPO) / '.fixtures/kicad-src/qa/data/pcbnew/plugins/altium/issue24654/PCB1.PcbDoc'
    shutil.copyfile(fixture, source)
    before = importer.source_inventory(str(source_dir))
    target = tmp_path / 'target'
    run(['project', 'new', target, '--name', 'target'])
    result = rj(['--project', target, 'pcb', 'import', source, '--format', 'altium'])
    info = rj(['--project', target, 'pcb', 'info'])
    assert info['footprints'] > 0
    assert result['read_back']['footprints'] == info['footprints']
    assert result['read_back']['path'] == str(target / 'target.kicad_pcb')
    assert importer.source_inventory(str(source_dir)) == before


@pytest.mark.e2e
def test_import_after_unsaved_edit_in_plan_and_dry_run(tmp_path):
    from test_e2e import rj, run, REPO
    target = tmp_path / 'target'
    run(['project', 'new', target, '--name', 'target'])
    incoming = target / 'incoming'
    incoming.mkdir()
    source = incoming / 'PCB1.PcbDoc'
    shutil.copyfile(Path(REPO) / '.fixtures/kicad-src/qa/data/pcbnew/plugins/altium/issue24654/PCB1.PcbDoc', source)
    original = importer.source_inventory(str(incoming))
    board = target / 'target.kicad_pcb'
    before = board.read_bytes()
    plan = tmp_path / 'import-plan.json'
    plan.write_text(json.dumps({'version': 1, 'steps': [
        {'args': ['pcb', 'outline', 'rect', '--at', '0,0', '--size', '1,1']},
        {'args': ['pcb', 'import', str(source), '--format', 'altium']},
        {'args': ['pcb', 'info']}]}), encoding='utf-8')
    preview = rj(['--project', target, '--dry-run', 'run', plan])
    assert preview['dry_run'] and preview['steps'][2]['result']['footprints'] > 0
    assert board.read_bytes() == before
    actual = rj(['--project', target, 'run', plan])
    metadata = actual['steps'][1]['result']['read_back']
    assert metadata['scope'] == 'session'
    assert metadata['sha256'] == hashlib.sha256(board.read_bytes()).hexdigest()
    assert actual['steps'][2]['result']['footprints'] == metadata['footprints']
    assert importer.source_inventory(str(incoming)) == original
