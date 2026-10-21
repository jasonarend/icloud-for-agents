"""Prepare managed-policy state from an existing grant; never auto-enable access."""
import argparse,json,os
from pathlib import Path
from icloud_for_agents.local_config import read_settings, child_environment
from icloud_for_agents.services import configured
from icloud_for_agents.cleanup_access import CleanupAccess
from icloud_for_agents.managed_policy import initialize_from_base

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize',action='store_true',help='Only after explicit owner approval; feature remains disabled')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];settings=read_settings(root)
    if not configured(settings)['mail'] or settings['access_mode']!='write-enabled' or not settings.get('cleanup_grant_id'):
        parser.error('Existing approved Mail writes and a valid base cleanup grant are required')
    environment=child_environment(settings,os.environ)
    for key in list(os.environ):
        if key.startswith('ICLOUD_'):os.environ.pop(key)
    os.environ.update(environment)
    scope=CleanupAccess(settings['cleanup_grant_id'],settings['account']).scope()
    preview={'account':scope['account'],'base_scope':scope,'approval_policy':settings.get('approval_policy','local-receipt'),
             'capability_to_review':'persistent mail-policy changes and exact one-time starred/source-folder Trash exceptions',
             'feature_enabled':settings.get('policy_management_enabled',False),'initialization_does_not_enable_feature':True}
    print(json.dumps(initialize_from_base(settings['account'],settings['cleanup_grant_id']) if args.initialize else preview,indent=2))
