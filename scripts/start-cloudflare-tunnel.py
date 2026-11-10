"""Start an owner-configured Cloudflare tunnel without putting its key in arguments."""
from pathlib import Path
from icloud_for_agents.cloudflare_tunnel import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
