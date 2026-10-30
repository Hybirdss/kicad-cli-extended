import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from kicad_cli_extended.core import fab
from test_board_gaps import package_session, exporter


def test_manifest_hashes_revision_and_release_name(package_session, tmp_path):
    project = package_session.require_project()
    Path(project.sch_path).write_text('(kicad_sch (title_block (rev "1.3.0")))')
    with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter()), patch.object(fab.kb, 'kicad_version', return_value='10.0.7'):
        result = fab.make_package(package_session, 'jlcpcb', str(tmp_path / 'release'), run_drc=False,
                                  manifest=True, name_suffix='{rev}-{preset}', require_rev='v1.3.0')
    assert result['zip']['path'] == 'p-1.3.0-jlcpcb-gerbers.zip'
    for item in result['files']:
        assert item['sha256'] == hashlib.sha256(Path(result['output_dir'], item['path']).read_bytes()).hexdigest()
    manifest = json.loads(Path(result['output_dir'], 'manifest.json').read_text())
    assert manifest['tool'] == 'kce' and manifest['kicad_version'] == '10.0.7'
    assert manifest['revision'] == '1.3.0'
    assert not any(f['path'] == 'manifest.json' for f in manifest['files'])
    output = tmp_path / 'mismatch'
    with patch.object(fab.kb, 'run_kicad_cli') as call:
        result = fab.make_package(package_session, 'jlcpcb', str(output), require_rev='2')
    assert result['failed_steps'] == ['revision'] and not result['complete']
    call.assert_not_called()
    assert not output.exists()
