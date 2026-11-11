"""Opt-in real Tailscale Serve check using only invented MCP data.

Run with --run on an already connected Mac with HTTPS enabled and no existing
Serve routes. It creates one temporary private route and removes that route.
The same Mac is the HTTPS client; this does not test another tailnet device.
"""
import argparse
import asyncio
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]

SERVER = r'''
import atexit, hmac, json, os, signal, sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
port=int(sys.argv[1]); hostname=sys.argv[2]
descriptor=int(os.environ.pop("BRIDGE_TEST_SECRET_FD"))
token=os.read(descriptor,512); os.close(descriptor)
events=[]; reads=[]
def audit(event,args):
    forbidden=event in {"socket.connect","socket.connect_ex","socket.getaddrinfo","subprocess.Popen"}
    if event=="socket.bind": forbidden=args[1]!=("127.0.0.1",port)
    if forbidden:
        events.append(event)
        raise AssertionError("Synthetic provider forbids external access")
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ["BRIDGE_TEST_AUDIT"]).write_text(json.dumps({"blocked_events":events,"reads":len(reads)})))
from icloud_for_agents import credentials
def refuse():
    events.append("credential_lookup")
    raise AssertionError("No real credentials")
credentials.credentials=refuse
from icloud_for_agents import server
from icloud_for_agents.http_transport import create_app
server.credentials=refuse
def folders():
    reads.append(True)
    return [{"name":"Synthetic inbox","flags":[]},{"name":"Synthetic archive","flags":[]}]
server._service=SimpleNamespace(list_folders=folders)
class Keys:
    def authenticate(self,candidate):
        if hmac.compare_digest(candidate,token):
            return {"client_id":"a"*32,"manage_clients":False,"permissions":{name:"read" if name=="mail" else "none" for name in ("mail","calendar","contacts","notes","reminders")}}
        return None
import uvicorn
class SyntheticServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        previous=signal.signal(signal.SIGTERM,self.handle_exit)
        try: yield
        finally: signal.signal(signal.SIGTERM,previous)
SyntheticServer(uvicorn.Config(create_app(server.mcp,Keys(),proxy_host=hostname),host="127.0.0.1",port=port,
    access_log=False,proxy_headers=False,server_header=False,log_level="warning")).run()
'''


def run(executable):
    def tailscale(*args, timeout=20):
        return subprocess.run([executable,*args],capture_output=True,text=True,timeout=timeout,check=False)

    def configuration():
        result=tailscale("serve","status","--json")
        if result.returncode:
            raise RuntimeError("Tailscale Serve configuration cannot be inspected")
        return json.loads(result.stdout)

    original=configuration()
    if original:
        raise RuntimeError("Existing Serve configuration preserved; use an empty test configuration")
    state=tailscale("status","--json")
    if state.returncode:
        raise RuntimeError("Tailscale status is unavailable")
    state=json.loads(state.stdout)
    hostname=state.get("Self",{}).get("DNSName","").rstrip(".")
    if state.get("BackendState")!="Running" or not hostname or not state.get("CertDomains"):
        raise RuntimeError("Connected Tailscale and existing HTTPS support are required; no settings were changed")
    https_port=18443+secrets.randbelow(20000)
    mount="/icloud-release-"+uuid.uuid4().hex
    token=("ika_"+uuid.uuid4().hex+"."+secrets.token_urlsafe(32)).encode()
    attempted=False
    process=None
    restored=False
    with tempfile.TemporaryDirectory(prefix="icloud-tailnet-synthetic-") as directory:
        home=Path(directory)
        audit=home/"audit.json"
        script=home/"synthetic_server.py"
        script.write_text(SERVER)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0))
            port=probe.getsockname()[1]
        read_fd,write_fd=os.pipe()
        try:
            os.write(write_fd,token)
            os.close(write_fd); write_fd=None
            env={"HOME":directory,"PATH":os.defpath,"PYTHONPATH":str(ROOT/"src"),
                 "BRIDGE_TEST_SECRET_FD":str(read_fd),"BRIDGE_TEST_AUDIT":str(audit),
                 "ICLOUD_RUNTIME_ROOT":str(home/"runtime"),"ICLOUD_MAIL_ACCOUNT":"synthetic@example.invalid",
                 "ICLOUD_MAIL_ACCESS":"read-only","ICLOUD_HTTP_TRANSPORT":"1","ICLOUD_ENABLE_MAIL":"1",
                 "ICLOUD_ENABLE_CALENDAR":"0","ICLOUD_ENABLE_CONTACTS":"0","ICLOUD_ENABLE_NOTES":"0",
                 "ICLOUD_ENABLE_REMINDERS":"0","FASTMCP_CHECK_FOR_UPDATES":"off"}
            process=subprocess.Popen([sys.executable,str(script),str(port),hostname],cwd=directory,env=env,
                                     pass_fds=(read_fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        finally:
            os.close(read_fd)
            if write_fd is not None: os.close(write_fd)
        try:
            deadline=time.monotonic()+20
            while True:
                connection=http.client.HTTPConnection("127.0.0.1",port,timeout=1)
                try:
                    connection.request("GET","/mcp")
                    if connection.getresponse().status!=401: raise AssertionError("Local authentication refusal missing")
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic()>=deadline:
                        raise RuntimeError("Isolated MCP server did not start")
                    time.sleep(.05)
                finally: connection.close()
            if configuration()!=original:
                raise RuntimeError("Serve configuration changed before setup; no route was created")
            attempted=True
            result=tailscale("serve","--bg",f"--https={https_port}",f"--set-path={mount}",f"http://127.0.0.1:{port}")
            if result.returncode:
                raise RuntimeError("Tailscale refused the temporary route; no account or admin settings were changed")
            address=f"https://{hostname}:{https_port}{mount}/mcp"
            with httpx2.Client(trust_env=False,timeout=10) as client:
                # Serve can provision its HTTPS certificate on the first read.
                # Only read-only readiness checks repeat; never relax TLS.
                deadline=time.monotonic()+60
                while True:
                    try:
                        assert client.get(address).status_code==401
                        break
                    except httpx2.TimeoutException:
                        if time.monotonic()>=deadline: raise
                assert client.get(address,headers={"Authorization":"Bearer invalid"}).status_code==401

            async def exercise():
                transport=StreamableHttpTransport(address,headers={"Authorization":"Bearer "+token.decode()},
                    httpx_client_factory=lambda **options:httpx2.AsyncClient(trust_env=False,**options))
                async with Client(transport) as client:
                    names={item.name for item in await client.list_tools()}
                    assert "email_list_folders" in names and "email_archive" not in names and "calendar_list" not in names
                    result=await client.call_tool_mcp("email_list_folders",{})
                    assert not result.is_error
                    items=result.structured_content or json.loads(result.content[0].text)
                    assert "Synthetic inbox" in json.dumps(items)
                    refusal=await client.call_tool_mcp("email_archive",{"folder":"Synthetic inbox","uidvalidity":1,"uids":[1]})
                    assert refusal.is_error
            asyncio.run(asyncio.wait_for(exercise(),timeout=40))
        finally:
            try:
                if attempted:
                    if configuration()!=original:
                        result=tailscale("serve",f"--https={https_port}",f"--set-path={mount}","off")
                        if result.returncode:
                            raise RuntimeError("Temporary Serve route removal was not confirmed")
                    deadline=time.monotonic()+10
                    while configuration()!=original and time.monotonic()<deadline: time.sleep(.1)
                    restored=configuration()==original
                    if not restored: raise RuntimeError("Serve configuration differs from its original state")
            finally:
                if process is not None:
                    process.terminate()
                    stdout,stderr=process.communicate(timeout=15)
                    assert process.returncode==0 and token not in stdout+stderr
        report=json.loads(audit.read_text())
        assert report["blocked_events"]==[] and report["reads"]==1
    return {"status":"passed","real_tailscale_serve":True,"same_mac_https_client":True,
            "valid_tls":True,"unauthenticated_refused":True,"read_only_mcp_call":True,
            "write_refused":True,"original_serve_configuration_restored":restored,
            "real_credentials_used":False,"apple_data_used":False,"second_device_tested":False}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true",help="Opt into a temporary route on the connected tailnet")
    parser.add_argument("--tailscale-executable",default=shutil.which("tailscale"))
    args=parser.parse_args()
    if not args.run: parser.error("Use --run only for an explicitly approved synthetic tailnet test")
    if not args.tailscale_executable: parser.error("Tailscale executable is unavailable")
    print(json.dumps(run(args.tailscale_executable)))
