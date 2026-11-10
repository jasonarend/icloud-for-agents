"""Opt-in HTTP launcher using the same validated owner settings as stdio."""
import os
from pathlib import Path
import sys
from icloud_for_agents.local_config import read_settings, child_environment

root = Path(__file__).resolve().parents[1]
environment = child_environment(read_settings(root), os.environ)
environment['ICLOUD_CONFIG_ROOT'] = str(root)
binary = str(root / '.venv/bin/python')
os.execve(binary, [binary, '-m', 'icloud_for_agents.http_transport', *sys.argv[1:]], environment)
