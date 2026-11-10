#!/usr/bin/env python3
"""Check that a built wheel ships exactly the runtime branding in this source tree."""

import argparse
from email.parser import BytesParser
import json
from pathlib import Path
import zipfile
import tomllib


def check_wheel(archive, root):
    root = Path(root)
    required = ("icloud_for_agents/branding.py", "icloud_for_agents/metadata.py",
                "icloud_for_agents/assets/icon-128.png")
    with zipfile.ZipFile(archive) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel member")
        if wheel.testzip() is not None:
            raise ValueError("Corrupt wheel member")
        for name in required:
            if name not in names or wheel.read(name) != (root / "src" / name).read_bytes():
                raise ValueError("Missing or stale wheel branding: " + name)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("Expected exactly one wheel package metadata file")
        metadata = BytesParser().parsebytes(wheel.read(metadata_names[0]))
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        for field, key in (("Name", "name"), ("Version", "version"), ("Summary", "description")):
            if metadata.get(field) != project[key]:
                raise ValueError("Missing or stale wheel metadata: " + field)
        expected_urls = sorted(f"{label}, {url}" for label, url in project["urls"].items())
        if sorted(metadata.get_all("Project-URL", [])) != expected_urls:
            raise ValueError("Missing or stale wheel project URLs")
    return {"status": "passed", "runtime_branding_files": list(required),
            "package_metadata": "name, version, description and project URLs match source"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(check_wheel(args.archive, args.source_root)))
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(1, str(exc) + "\n")
