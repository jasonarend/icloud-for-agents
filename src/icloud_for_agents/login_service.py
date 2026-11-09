"""Reusable per-user login service. No credentials are read during installation."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import time
from .tunnel_runtime import launch_plan, runtime_lock, RuntimeSetupError, project_root

LABEL = 'local.icloud-for-agents.tunnel'
COMMAND_TIMEOUT = 5
STOP_VERIFY_TIMEOUT = 10
STOP_POLL_INTERVAL = 0.1


class ServiceStopError(RuntimeSetupError):
    """A redacted stop/verification failure safe to display in the CLI."""


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


def status(root, home=None, runner=subprocess.run, *, timeout=COMMAND_TIMEOUT):
    reviewed_service(root, home)
    command_timeout = max(0.001, timeout / 2)
    try:
        result = runner(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'],
                        capture_output=True, check=False, timeout=command_timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'status_unavailable', 'returncode': None, 'reason': 'launchctl_status_timed_out'}
    except (OSError, subprocess.SubprocessError):
        return {'status': 'status_unavailable', 'returncode': None, 'reason': 'launchctl_status_failed'}
    if result.returncode == 113:
        # A missing GUI domain produces the same code as a missing service.
        # Confirm the domain exists without retaining its private job listing.
        try:
            domain = runner(['/bin/launchctl', 'print', f'gui/{os.getuid()}'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False, timeout=command_timeout)
        except (OSError, subprocess.SubprocessError):
            return {'status': 'status_unavailable', 'returncode': 113,
                    'reason': 'launchd_domain_unavailable'}
        return {'status': 'not_loaded' if domain.returncode == 0 else 'status_unavailable',
                'returncode': 113, 'domain_returncode': domain.returncode}
    return {'status': 'loaded' if result.returncode == 0 else 'status_unavailable',
            'returncode': result.returncode}


def stop(root, home=None, runner=subprocess.run, *, clock=time.monotonic, sleeper=time.sleep):
    reviewed_service(root, home)
    warning = None
    returncode = None
    try:
        result = runner(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'],
                        capture_output=True, check=False, timeout=COMMAND_TIMEOUT)
        returncode = result.returncode
        if returncode not in {0, 113}:
            warning = 'launchctl_nonzero_stop_result'
    except subprocess.TimeoutExpired:
        warning = 'launchctl_stop_timed_out'
    except (OSError, subprocess.SubprocessError):
        warning = 'launchctl_stop_request_failed'

    # bootout can return before launchd finishes unregistering the service.
    # Poll only the exact reviewed service; never repeat the stop request.
    deadline = clock() + STOP_VERIFY_TIMEOUT
    verified = {'status': 'status_unavailable'}
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            detail = ('launchd still reports the service loaded' if verified['status'] == 'loaded'
                      else 'launchd status is unavailable')
            raise ServiceStopError('Service stop unconfirmed after the bounded wait: ' + detail + '; installation retained')
        try:
            verified = status(root, home, runner, timeout=min(COMMAND_TIMEOUT, remaining))
        except (OSError, ValueError, RuntimeSetupError):
            raise ServiceStopError('Service stop verification refused because the installed definition changed or is unavailable; installation retained') from None
        if verified['status'] == 'not_loaded':
            try:
                reviewed_service(root, home)
            except (OSError, ValueError, RuntimeSetupError):
                raise ServiceStopError('Service stop verification refused because the installed definition changed or is unavailable; installation retained') from None
            value = {'stopped': True, 'login_startup_retained': True}
            if warning:
                value.update(stop_warning=warning, stop_returncode=returncode)
            return value
        sleeper(min(STOP_POLL_INTERVAL, max(0, deadline - clock())))


def uninstall(root, home=None, runner=subprocess.run, *, clock=time.monotonic, sleeper=time.sleep):
    stopped = stop(root, home, runner, clock=clock, sleeper=sleeper)
    # Revalidate after the external operation before removing exactly one plist.
    reviewed_service(root, home).unlink()
    result = {'stopped': True, 'uninstalled': True, 'credentials_and_records_retained': True}
    if stopped.get('stop_warning'):
        result.update(stop_warning=stopped['stop_warning'], stop_returncode=stopped['stop_returncode'])
    return result


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
    except ServiceStopError as exc:
        parser.exit(1, str(exc) + '. No automatic retry.\n')
    except Exception:
        parser.exit(1, 'Login service setup refused; inspect private configuration and existing service. No automatic retry.\n')
