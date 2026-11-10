"""Assistant-operated optional local HTTP service management."""
from pathlib import Path
from icloud_for_agents.http_service import main

if __name__ == "__main__":
    main(Path(__file__).resolve().parents[1])
