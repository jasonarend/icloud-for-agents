"""Pin credential-bearing launchers to verified bytes in an owner-private cache."""

import hashlib
from collections import deque
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


def _acl_allows_write(path, *, symlink=False):
    """Reject macOS ACL write grants, including grants not reflected in mode bits."""
    if sys.platform != "darwin":
        return False
    import ctypes
    import errno
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    getter = libc.acl_get_link_np if symlink else libc.acl_get_file
    getter.argtypes = [ctypes.c_char_p, ctypes.c_int]
    getter.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    libc.acl_get_permset_mask_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
    libc.acl_free.argtypes = [ctypes.c_void_p]
    ctypes.set_errno(0)
    acl = getter(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:  # Darwin reports an absent ACL this way.
            return False
        raise ExecutableSafetyError(f"Cannot inspect access controls: {path}")
    try:
        entry = ctypes.c_void_p()
        which = 0  # ACL_FIRST_ENTRY; subsequent entries use ACL_NEXT_ENTRY (-1).
        write_mask = sum(1 << bit for bit in (2, 4, 5, 6, 8, 10, 12, 13))
        while True:
            status = libc.acl_get_entry(acl, which, ctypes.byref(entry))
            if status == -1:
                if ctypes.get_errno() == errno.EINVAL:
                    break
                raise ExecutableSafetyError(f"Cannot inspect access controls: {path}")
            if status != 0:
                raise ExecutableSafetyError(f"Cannot inspect access controls: {path}")
            which = -1
            tag, permissions = ctypes.c_int(), ctypes.c_uint64()
            if (libc.acl_get_tag_type(entry, ctypes.byref(tag)) != 0
                    or libc.acl_get_permset_mask_np(entry, ctypes.byref(permissions)) != 0):
                raise ExecutableSafetyError(f"Cannot inspect access controls: {path}")
            if tag.value == 1 and permissions.value & write_mask:  # ACL_EXTENDED_ALLOW
                return True
        return False
    finally:
        libc.acl_free(acl)


def trusted_path(path, *, directory=False, executable=False):
    """Verify lexical and resolved paths cannot be replaced by another OS user.

    Root-owned sticky temporary directories are safe when every child in the
    selected chain is owned by root or the current user. Safe interpreter and
    system-directory symlinks remain supported.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ExecutableSafetyError(f"Use an absolute protected installation path: {path}")

    def inspect(part, *, final=False):
        info = part.lstat()
        if info.st_uid not in {0, os.getuid()}:
            raise ExecutableSafetyError(f"Installation path belongs to another user: {part}")
        link = stat.S_ISLNK(info.st_mode)
        if _acl_allows_write(part, symlink=link):
            raise ExecutableSafetyError(f"Installation path has an ACL write grant: {part}")
        if not link:
            is_directory = stat.S_ISDIR(info.st_mode)
            sticky_system = is_directory and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_mode & 0o022 and not sticky_system:
                raise ExecutableSafetyError(f"Installation path is writable by other users: {part}")
            if not final and not is_directory:
                raise ExecutableSafetyError(f"Installation ancestor is not a directory: {part}")
            if final and (directory != is_directory or (not directory and not stat.S_ISREG(info.st_mode))):
                raise ExecutableSafetyError(f"Unexpected installation path type: {part}")
            if final and executable and not info.st_mode & 0o111:
                raise ExecutableSafetyError(f"Installation interpreter is not executable: {part}")
        return link

    # Resolve one link edge at a time. Path.resolve() would hide an intermediate
    # relay in a writable directory when its final destination happens to be safe.
    pending = deque(path.parts[1:])
    current = Path(path.anchor)
    inspect(current, final=not pending)
    links = 0
    while pending:
        component = pending.popleft()
        if component == "..":
            current = current.parent
            continue
        part = current / component
        if inspect(part, final=not pending):
            links += 1
            if links > 40:
                raise ExecutableSafetyError(f"Unresolvable installation path: {path}")
            target = Path(os.readlink(part))
            if target.is_absolute():
                current = Path(target.anchor)
                parts = target.parts[1:]
            else:
                parts = target.parts
            pending.extendleft(reversed(parts))
        else:
            current = part
    inspect(current, final=True)
    return current


def stage_profile(content):
    """Publish validated nonsecret profile bytes under the protected cache."""
    directory = _cache_directory()
    raw = content.encode("utf-8")
    target = directory / (hashlib.sha256(raw).hexdigest() + ".yaml")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".profile-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ExecutableSafetyError(ValueError):
    """Executable identity or private staging could not be verified."""


def _open(path):
    path = Path(path)
    if not path.is_absolute():
        raise ExecutableSafetyError("Executable must use an absolute path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or stat.S_IMODE(info.st_mode) & 0o022 or not info.st_mode & 0o111):
            raise ExecutableSafetyError("Executable must be owned and not writable by others")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def _unchanged(before, after):
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ExecutableSafetyError("Executable changed while being inspected")


def executable_digest(path):
    fd, before = _open(path)
    with os.fdopen(fd, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        _unchanged(before, os.fstat(stream.fileno()))
        return digest


def _cache_directory():
    # Do not use TMPDIR: its ancestors may be writable by another local user.
    # Root's sticky temporary directory protects this owned entry from rename.
    parent = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
    for ancestor in (parent, *parent.parents):
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
            raise ExecutableSafetyError("System executable-cache parent is unsafe")
        if _acl_allows_write(ancestor):
            raise ExecutableSafetyError("System executable-cache parent has an ACL write grant")
        if info.st_mode & 0o022 and not (ancestor == parent and info.st_mode & stat.S_ISVTX):
            raise ExecutableSafetyError("System executable-cache parent is writable")
    directory = parent / ("icloud-for-agents-executables-" + str(os.getuid()))
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ExecutableSafetyError("Executable cache must be an owned private directory")
    if _acl_allows_write(directory):
        raise ExecutableSafetyError("Executable cache has an ACL write grant")
    return directory


def stage_executable(path, expected):
    """Copy one opened identity before loading a credential; never re-exec its source path."""
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ExecutableSafetyError("Reviewed executable checksum is invalid")
    fd, before = _open(path)
    temporary = None
    try:
        directory = _cache_directory()
        with os.fdopen(fd, "rb") as source:
            fd = None
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".prepare-", delete=False) as target:
                temporary = Path(target.name)
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                _unchanged(before, os.fstat(source.fileno()))
                if digest.hexdigest() != expected:
                    raise ExecutableSafetyError("Reviewed executable identity changed")
                target.flush()
                os.fchmod(target.fileno(), 0o700)
                os.fsync(target.fileno())
        staged = directory / expected
        os.replace(temporary, staged)
        temporary = None
        return staged
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
