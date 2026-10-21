"""Install or activate the reviewed per-user login service."""
from pathlib import Path
from icloud_for_agents.login_service import main
if __name__ == '__main__':
    main(root=Path(__file__).resolve().parents[1])
