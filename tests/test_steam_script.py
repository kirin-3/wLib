import os
import subprocess
import sys

import pytest

from core.steam_script import ensure_launch_script, render_launch_script


@pytest.mark.parametrize('options,expected', [
    ({'appimage': '/path/My App.AppImage'}, "'/path/My App.AppImage'"),
    ({'launcher': '/opt/wlib/wlib'}, '/opt/wlib/wlib'),
    ({'python': '/venv/python', 'main': '/src/main.py'}, '/venv/python /src/main.py'),
    ({'flatpak': True}, 'WLIB_HOST_LD_PRELOAD'),
])
def test_script_formats(options, expected):
    script = render_launch_script(**options)
    assert expected in script
    assert '--launch "$1"' in script
    assert subprocess.run(['sh', '-n'], input=script, text=True, check=False).returncode == 0


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX exec bits')
def test_script_written_executable(tmp_path):
    path = ensure_launch_script(str(tmp_path))
    assert path.is_file()
    assert os.stat(path).st_mode & 0o777 == 0o755
