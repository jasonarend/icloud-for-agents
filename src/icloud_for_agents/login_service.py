"""Reusable per-user login service. No credentials are read during installation."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
from .tunnel_runtime import launch_plan, runtime_lock, RuntimeSetupError, project_root

LABEL = 'local.icloud-for-agents.tunnel'


def definition(root):
    root = Path(root).resolve()
    return {'Label': LABEL, 'ProgramArguments': [str(root/'.venv/bin/python'),
            str(root/'scripts/start-private-tunnel.py'), '--start-with-keychain'],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': False,
            'LimitLoadToSessionType': 'Aqua', 'ProcessType': 'Background', 'Umask': 63,
            'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}


def install(root, home=None, runner=subprocess.run):
    plan = launch_plan(root)  # no Keychain lookup
    home = Path.home() if home is None else Path(home)
    directory = home/'Library/LaunchAgents'
    if directory.is_symlink():
        raise RuntimeSetupError('LaunchAgents directory must not be a symlink')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/(LABEL+'.plist')
    if target.exists() or target.is_symlink():
        raise RuntimeSetupError('Existing login service found; inspect it before replacing anything')
    with target.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(definition(plan['root']), stream)
    # Never stop a foreground process or bootstrap a competing runtime.
    try:
        fd = runtime_lock(plan['root'])
    except RuntimeSetupError:
        return {'installed': True, 'started': False, 'reason': 'foreground_runtime_active', 'path': str(target)}
    os.close(fd)
    result = runner(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)],
                    capture_output=True, check=False)
    return {'installed': True, 'started': result.returncode == 0,
            'reason': 'bootstrap_requested' if result.returncode == 0 else 'bootstrap_failed', 'path': str(target)}



def activate(root, home=None, runner=subprocess.run):
    plan = launch_plan(root)
    home = Path.home() if home is None else Path(home)
    target = home/'Library/LaunchAgents'/(LABEL+'.plist')
    if target.is_symlink() or not target.is_file():
        raise RuntimeSetupError('Install the reviewed login service first')
    info = target.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600 or plistlib.loads(target.read_bytes()) != definition(plan['root']):
        raise RuntimeSetupError('Installed login service differs from the reviewed definition')
    fd = runtime_lock(plan['root'])  # foreground still active: refuse BEFORE bootstrap
    os.close(fd)
    result = runner(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], capture_output=True, check=False)
    return {'started':result.returncode == 0, 'reason':'bootstrap_requested' if result.returncode == 0 else 'bootstrap_failed'}


def reviewed_service(root, home=None):
    """Inspect only the installed definition; shutdown must not need credentials/binary."""
    home = Path.home() if home is None else Path(home)
    directory = home/'Library/LaunchAgents'
    target = directory/(LABEL+'.plist')
    if directory.is_symlink() or target.is_symlink() or not target.is_file():
        raise RuntimeSetupError('Reviewed login service not found')
    info = target.stat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600
            or plistlib.loads(target.read_bytes()) != definition(root)):
        raise RuntimeSetupError('Installed login service differs from the reviewed definition')
    return target


def status(root, home=None, runner=subprocess.run):
    reviewed_service(root, home)
    result = runner(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, check=False)
    return {'status': 'loaded' if result.returncode == 0 else
            'not_loaded' if result.returncode == 113 else 'status_unavailable',
            'returncode': result.returncode}


def stop(root, home=None, runner=subprocess.run):
    reviewed_service(root, home)
    result = runner(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, check=False)
    if result.returncode not in {0, 113}:
        raise RuntimeSetupError('Service stop failed; installation retained')
    verified = status(root, home, runner)
    if verified['status'] != 'not_loaded':
        raise RuntimeSetupError('Service stop unconfirmed; installation retained')
    return {'stopped': True, 'login_startup_retained': True}


def uninstall(root, home=None, runner=subprocess.run):
    stop(root, home, runner)
    # Revalidate after the external operation before removing exactly one plist.
    reviewed_service(root, home).unlink()
    return {'stopped': True, 'uninstalled': True, 'credentials_and_records_retained': True}


def main(root=None):
    import json
    parser = argparse.ArgumentParser(description='Install the approved user login service without credential provisioning')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install', action='store_true')
    group.add_argument('--activate', action='store_true', help='After owner stops foreground runtime')
    group.add_argument('--status', action='store_true', help='Inspect the reviewed per-user launchd service')
    group.add_argument('--stop', action='store_true', help='Stop now; retain startup at the next login')
    group.add_argument('--uninstall', action='store_true', help='Stop and remove only the reviewed login plist')
    args = parser.parse_args()
    try:
        selected = project_root() if root is None else root
        action = (activate if args.activate else status if args.status else stop if args.stop
                  else uninstall if args.uninstall else install)
        print(json.dumps(action(selected)))
    except Exception:
        parser.exit(1, 'Login service setup refused; inspect private configuration and existing service. No automatic retry.\n')
