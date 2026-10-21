"""OWNER ONLY: review an exact managed-policy proposal locally; never agent-run."""
import argparse
import json
import os
from pathlib import Path
from icloud_for_agents.local_config import read_settings, child_environment
from icloud_for_agents.managed_policy import context
from icloud_for_agents.policy_approval import approve_local

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proposal_id')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    settings=read_settings(root)
    environment=child_environment(settings,os.environ)
    for key in list(os.environ):
        if key.startswith('ICLOUD_'):os.environ.pop(key)
    os.environ.update(environment)
    manager,_,_=context(settings['account'])
    print(json.dumps(approve_local(manager,args.proposal_id)))
