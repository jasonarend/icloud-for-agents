"""Check tunnel child environment isolation using only a synthetic key."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from icloud_for_agents.tunnel_runtime import mcp_child_command, runtime_environment


def run():
    with tempfile.TemporaryDirectory(prefix='synthetic tunnel child ') as directory:
        root = Path(directory)
        (root / '.venv/bin').mkdir(parents=True)
        (root / 'scripts').mkdir()
        (root / '.venv/bin/python').symlink_to(sys.executable)
        startup = root / 'startup'
        startup.mkdir()
        observed = root / 'observed.json'
        # The startup module runs before the application script. It records a
        # boolean only: neither test credential nor other environment contents.
        (startup / 'sitecustomize.py').write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "Path(os.environ['SYNTHETIC_OBSERVATION']).write_text(json.dumps("
            "{'startup_has_tunnel_key': 'CONTROL_PLANE_API_KEY' in os.environ}))\n")
        (root / 'scripts/run-stdio.py').write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "p = Path(os.environ['SYNTHETIC_OBSERVATION'])\n"
            "data = json.loads(p.read_text())\n"
            "data.update(application_started=True, application_has_tunnel_key="
            "'CONTROL_PLANE_API_KEY' in os.environ, ordinary_setting="
            "os.environ.get('SYNTHETIC_SETTING'))\n"
            "p.write_text(json.dumps(data))\n")
        tunnel_environment = runtime_environment('synthetic-not-a-credential',
                                                {'PATH': os.defpath})
        assert tunnel_environment['CONTROL_PLANE_API_KEY'] == 'synthetic-not-a-credential'
        tunnel_environment.update(PYTHONPATH=str(startup), SYNTHETIC_OBSERVATION=str(observed),
                                  SYNTHETIC_SETTING='preserved')
        result = subprocess.run(shlex.split(mcp_child_command(root)), env=tunnel_environment,
                                cwd=root, capture_output=True, timeout=15, check=False)
        assert result.returncode == 0, 'Synthetic MCP child did not start'
        assert json.loads(observed.read_text()) == {
            'startup_has_tunnel_key': False, 'application_started': True,
            'application_has_tunnel_key': False, 'ordinary_setting': 'preserved'}
        assert b'synthetic-not-a-credential' not in result.stdout + result.stderr
    print('Synthetic tunnel child starts without the tunnel key, including during Python startup')


if __name__ == '__main__':
    run()
