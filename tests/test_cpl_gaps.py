import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from kicad_cli_extended.core import fab
from test_board_gaps import package_session, exporter


def test_cpl_rotation_changes_only_cpl_and_reports_review(package_session, tmp_path):
    def build(name, fix=None):
        with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter()):
            result = fab.make_package(package_session, 'jlcpcb', str(tmp_path / name), run_drc=False, rotation_fix=fix)
        return result, Path(result['output_dir'])
    a, before = build('before')
    corrections = tmp_path / 'rotations.json'
    corrections.write_text('{"LED":180}')
    b, after = build('after', str(corrections))
    assert b['cpl']['review'] == [{'ref': 'D1', 'why': 'polarised', 'kicad_rotation': 0.0, 'check_in_jlc_viewer': True}]
    with (after / b['cpl']['path']).open() as f:
        assert list(csv.DictReader(f))[0]['Rotation'] == '180.00'
    for p in before.rglob('*'):
        if p.is_file() and p.suffix != '.zip' and 'cpl-jlcpcb' not in p.name:
            assert p.read_bytes() == (after / p.relative_to(before)).read_bytes()
