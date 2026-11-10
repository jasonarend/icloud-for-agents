"""Isolated HTTP lifecycle process with fake launchctl; never alters login services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_http_service import fixture


def main():
    with tempfile.TemporaryDirectory(prefix="http-service-process-") as directory:
        root, home = fixture(directory)
        code = r'''
import json, pathlib, sys
from unittest.mock import patch
events=[]
def audit(event,args):
    if event in {"socket.connect","socket.connect_ex","socket.bind","socket.getaddrinfo","subprocess.Popen"}:
        events.append(event)
        raise AssertionError("Isolated lifecycle cannot use network or subprocesses")
sys.addaudithook(audit)
from test_http_service import FakeLaunchctl
from icloud_for_agents import http_service as service
root,home=map(pathlib.Path,sys.argv[1:])
with patch("icloud_for_agents.http_keys.MacKeychain",side_effect=AssertionError("No Keychain")):
    preview=service.prepare(root,home=home,proxy_host="mcp.example.invalid")
    assert not (root/"local/http-service.json").exists()
    service.configure(root,preview["review_sha256"],home=home,proxy_host="mcp.example.invalid")
    assert service.install(root,home)["started"] is False
    runner=FakeLaunchctl(preview["profile"])
    with patch.object(service,"_port_free"):
        assert service.start(root,home,runner)["started"] is True
    assert service.status(root,home,runner)["running"] is True
    runner.running=False  # A matching launchd job can remain loaded after exit.
    actions_before=len([call for call in runner.calls if call[1]!="print"])
    with patch.object(service,"_port_free"):
        assert service.start(root,home,runner)["started"] is True
    actions=[call for call in runner.calls if call[1]!="print"]
    assert len(actions)==actions_before+1 and actions[-1][1]=="kickstart"
    runner.bootout_code=1
    runner.delay=3
    stopped=service.stop(root,home,runner,sleeper=lambda _:None)
    assert stopped["stopped"] and stopped["stop_request_warning"]
    assert service.remove(root,home,runner)["removed"]
    assert (root/"local/settings.json").exists()
    assert not any("local.icloud-for-agents.tunnel" in str(call) for call in runner.calls)
assert events==[]
print(json.dumps({"passed":True,"real_credentials_used":False,"real_launchd_used":False,
                  "profile_install_start_status_stop_remove":True,"loaded_stopped_recovery":True,"blocked_events":events}))
'''
        result = subprocess.run([sys.executable, "-c", code, str(root), str(home)], cwd=directory,
                                env={"HOME": str(home), "PATH": os.defpath,
                                     "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests")))},
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Synthetic HTTP service process failed: " + result.stderr[-4000:])
        data = json.loads(result.stdout)
        assert data["passed"] and data["blocked_events"] == []
        print(json.dumps(data))


if __name__ == "__main__":
    main()
