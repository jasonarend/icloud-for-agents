"""Client-neutral stdio launcher: validated nonsecret owner settings reach MCP."""
import os
from pathlib import Path
from icloud_for_agents.local_config import read_settings, child_environment

root = Path(__file__).resolve().parents[1]
environment = child_environment(read_settings(root), os.environ)
# Fixed by this launcher, never inherited from an MCP client or tool argument.
environment['ICLOUD_CONFIG_ROOT'] = str(root)
os.execve(str(root / ".venv/bin/icloud-for-agents"), [str(root / ".venv/bin/icloud-for-agents")], environment)
