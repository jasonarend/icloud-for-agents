"""Exercise a random, credentials-free launchd service; never the real tunnel."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

from icloud_for_agents import login_service


def run():
    domain = f'gui/{os.getuid()}'

    def launchctl(*arguments):
        return subprocess.run(['/bin/launchctl', *arguments], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10, check=False)

    domain_result = launchctl('print', domain)
    if domain_result.returncode in {113, 125}:
        return {'status': 'skipped', 'reason': 'gui_launchd_domain_unavailable',
                'launchctl_returncode': domain_result.returncode, 'production_service_touched': False}
    if domain_result.returncode != 0:
        raise AssertionError('GUI launchd domain inspection failed with code ' + str(domain_result.returncode))

    label = 'local.icloud-agents.lifecycle-smoke.' + uuid.uuid4().hex
    original_label = login_service.LABEL
    target = f'{domain}/{label}'
    observed_loaded = 0
    with tempfile.TemporaryDirectory(prefix='icloud-login-lifecycle-') as directory:
        root = Path(directory)
        home = root / 'home'
        ready = root / 'ready'
        (root / '.venv/bin').mkdir(parents=True)
        (root / '.venv/bin/python').symlink_to(sys.executable)
        (root / 'scripts').mkdir()
        # The helper ignores the product-shaped argument and only waits for a
        # signal. Its delayed exit makes asynchronous removal observable.
        (root / 'scripts/start-private-tunnel.py').write_text(
            'import os, signal, time\n'
            'from pathlib import Path\n'
            'def stop(*args):\n'
            '    time.sleep(0.5)\n'
            '    raise SystemExit(0)\n'
            'signal.signal(signal.SIGTERM, stop)\n'
            f'Path({str(ready)!r}).write_text(str(os.getpid()))\n'
            'while True: time.sleep(1)\n')
        login_service.LABEL = label
        plist = home / 'Library/LaunchAgents' / (label + '.plist')
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps(login_service.definition(root)))
        plist.chmod(0o600)

        def bootstrap():
            ready.unlink(missing_ok=True)
            result = launchctl('bootstrap', domain, str(plist))
            if result.returncode:
                raise AssertionError('Synthetic bootstrap failed with code ' + str(result.returncode))
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not ready.exists():
                raise AssertionError('Synthetic helper did not start')

        def observing_runner(command, **kwargs):
            nonlocal observed_loaded
            result = subprocess.run(command, **kwargs)
            if command == ['/bin/launchctl', 'print', target] and result.returncode == 0:
                observed_loaded += 1
            return result

        def assert_absent():
            if launchctl('print', target).returncode != 113 or launchctl('print', domain).returncode != 0:
                raise AssertionError('Synthetic service absence could not be verified')

        try:
            bootstrap()
            result = login_service.stop(root, home=home, runner=observing_runner)
            if not result.get('stopped') or not plist.exists():
                raise AssertionError('Stop did not retain the synthetic login registration')
            assert_absent()
            bootstrap()
            result = login_service.uninstall(root, home=home, runner=observing_runner)
            if not result.get('uninstalled') or plist.exists():
                raise AssertionError('Uninstall did not remove its confirmed stopped registration')
            assert_absent()
        finally:
            # Cleanup targets only this random label, even on an assertion or
            # timeout. No production label or tunnel process is selected.
            try:
                if launchctl('print', target).returncode == 0:
                    launchctl('kill', 'SIGKILL', target)
                    launchctl('bootout', target)
                    deadline = time.monotonic() + 5
                    while launchctl('print', target).returncode == 0 and time.monotonic() < deadline:
                        time.sleep(0.05)
                assert_absent()
            finally:
                login_service.LABEL = original_label
    return {'status': 'passed', 'cycles': 2, 'delayed_loaded_observations': observed_loaded,
            'production_service_touched': False, 'credentials_or_network_used': False}


if __name__ == '__main__':
    print(json.dumps(run()))
