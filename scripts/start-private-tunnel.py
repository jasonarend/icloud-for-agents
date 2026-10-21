"""Durable owner-controlled launcher; default is credential-free preflight only."""
from pathlib import Path
from icloud_for_agents.tunnel_runtime import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
