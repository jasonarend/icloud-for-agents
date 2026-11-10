"""Exercise native HTTP-key storage in a disposable standalone macOS Keychain.

Never uses the default/login Keychain, changes its search list, or permits UI.
The password and bearer values remain in memory and anonymous pipes only.
"""

import ctypes
import os
from pathlib import Path
import secrets
import tempfile

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys, MacKeychain
from icloud_for_agents.http_transport import create_app
from fastmcp import FastMCP
from starlette.testclient import TestClient


def checked(status, action):
    if status:
        raise AssertionError(f"Standalone synthetic Keychain {action} failed ({status})")


def pipe_action(action):
    read_fd, write_fd = os.pipe()
    try:
        result = action(write_fd)
        os.close(write_fd)
        write_fd = None
        return result, os.read(read_fd, 512)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)


def run():
    native = MacKeychain()
    security, core = native.security, native.core
    pointer, uint = ctypes.c_void_p, ctypes.c_uint32
    security.SecKeychainCreate.argtypes = [ctypes.c_char_p, uint, pointer, ctypes.c_ubyte,
                                           pointer, ctypes.POINTER(pointer)]
    security.SecKeychainCreate.restype = ctypes.c_int32
    security.SecKeychainUnlock.argtypes = [pointer, uint, pointer, ctypes.c_ubyte]
    security.SecKeychainUnlock.restype = ctypes.c_int32
    security.SecKeychainDelete.argtypes = [pointer]
    security.SecKeychainDelete.restype = ctypes.c_int32
    security.SecKeychainCopySearchList.argtypes = [ctypes.POINTER(pointer)]
    security.SecKeychainCopySearchList.restype = ctypes.c_int32
    security.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(pointer)]
    security.SecKeychainCopyDefault.restype = ctypes.c_int32
    security.SecKeychainGetPath.argtypes = [pointer, ctypes.POINTER(uint), pointer]
    security.SecKeychainGetPath.restype = ctypes.c_int32
    core.CFArrayGetCount.argtypes = [pointer]
    core.CFArrayGetCount.restype = ctypes.c_long
    core.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_long]
    core.CFArrayGetValueAtIndex.restype = pointer

    def path_of(reference):
        length, buffer = uint(4096), ctypes.create_string_buffer(4096)
        checked(security.SecKeychainGetPath(reference, ctypes.byref(length), buffer), "path inspection")
        return buffer.value

    def snapshot():
        search, default = pointer(), pointer()
        checked(security.SecKeychainCopySearchList(ctypes.byref(search)), "search-list inspection")
        try:
            paths = tuple(path_of(core.CFArrayGetValueAtIndex(search, i))
                          for i in range(core.CFArrayGetCount(search)))
        finally:
            core.CFRelease(search)
        default_status = security.SecKeychainCopyDefault(ctypes.byref(default))
        if default_status == -25307:  # errSecNoDefaultKeychain; never create one.
            return paths, None
        checked(default_status, "default inspection")
        try:
            return paths, path_of(default)
        finally:
            core.CFRelease(default)

    previous = ctypes.c_ubyte()
    checked(security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)), "interaction inspection")
    checked(security.SecKeychainSetUserInteractionAllowed(False), "noninteractive setup")
    try:
        before = snapshot()
        with tempfile.TemporaryDirectory(prefix="icloud-http-native-keychain-") as directory:
            root = Path(directory)
            keychain_path = root / "synthetic-http.keychain"
            password = secrets.token_urlsafe(32).encode("ascii")
            reference = pointer()
            try:
                checked(security.SecKeychainCreate(os.fsencode(keychain_path), len(password), password,
                                                    False, None, ctypes.byref(reference)), "creation")
                assert reference.value
                assert Path(os.fsdecode(path_of(reference))).parent.resolve() == root.resolve()
                assert snapshot() == before, "Standalone Keychain changed existing search/default metadata"
                checked(security.SecKeychainUnlock(reference, len(password), password, True), "unlock")
                backend = MacKeychain(keychain=reference)
                keys = HttpKeys(backend, PlanStore(root / "runtime"))
                first, original = pipe_action(lambda fd: keys.create("Synthetic A", fd, owner_approved=True))
                second, second_token = pipe_action(lambda fd: keys.create("Synthetic B", fd, owner_approved=True))
                assert keys.authenticate(original) == first
                assert keys.authenticate(second_token) == second
                app = create_app(FastMCP("Synthetic Keychain HTTP"), keys)
                with TestClient(app, base_url="http://127.0.0.1") as client:
                    # Stateless GET is unsupported, but a non-401 proves the
                    # request passed the real Keychain-backed HTTP gate.
                    def response(token):
                        return client.get("/mcp", headers={"Authorization": "Bearer " + token.decode()}).status_code

                    assert response(original) == 405
                    replacement, rotated = pipe_action(lambda fd: keys.rotate(first["client_id"], fd, owner_approved=True))
                    assert keys.authenticate(original) is None
                    assert keys.authenticate(rotated) == replacement
                    assert response(original) == 401 and response(rotated) == 405
                    assert keys.authenticate(second_token) == second and response(second_token) == 405
                    assert keys.revoke(first["client_id"], owner_approved=True)["status"] == "revoked"
                    assert keys.authenticate(rotated) is None and response(rotated) == 401
                    assert keys.authenticate(second_token) == second and response(second_token) == 405
                    assert keys.revoke(second["client_id"], owner_approved=True)["status"] == "revoked"
                    assert keys.authenticate(second_token) is None and response(second_token) == 401
                assert snapshot() == before, "Native lifecycle changed existing search/default metadata"
            finally:
                if reference.value:
                    try:
                        checked(security.SecKeychainDelete(reference), "disposable cleanup")
                    finally:
                        core.CFRelease(reference)
            assert snapshot() == before, "Cleanup changed existing search/default metadata"
            assert not keychain_path.exists(), "Disposable Keychain file was retained"
    finally:
        checked(security.SecKeychainSetUserInteractionAllowed(previous.value), "interaction restoration")
    print("Native HTTP key creation, rotation and revocation passed in a deleted standalone Keychain; existing Keychain configuration unchanged")


if __name__ == "__main__":
    run()
