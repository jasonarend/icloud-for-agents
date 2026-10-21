"""Genuine local-owner policy receipt interface; never registered as an MCP tool.

TTY confirmation is an operator workflow boundary, not an OS-user sandbox.
"""
import json
from .core import SafetyError, digest


def approve_local(manager, proposal_id, tty_factory=None):
    if manager.approval_policy != 'local-receipt':
        raise SafetyError('This interface is for the explicitly selected strict local-receipt mode')
    with manager.locked():
        proposal = manager.store.read(manager.store.path(proposal_id,'.json'))
        if (manager.clock() >= proposal['expires'] or manager.store.path(proposal_id,'.started').exists()
                or manager.store.path(proposal_id,'.revoked').exists()):
            raise SafetyError('Proposal expired, started or revoked; no receipt')
        sha = digest(proposal)
        phrase = 'APPROVE POLICY ' + proposal_id + ' ' + sha
        factory = tty_factory or (lambda: open('/dev/tty','r+',encoding='utf-8'))
        with factory() as tty:
            tty.write(json.dumps(proposal,ensure_ascii=True,indent=2))
            tty.write('\nReview persistent before/after scope or exact one-use items above.\nType: '+phrase+'\n> ')
            tty.flush()
            if tty.readline().rstrip('\n') != phrase:
                raise SafetyError('No local owner authorization recorded')
        if manager.clock() >= proposal['expires']:
            raise SafetyError('Proposal expired during owner review; no receipt')
        manager.store.write_once(manager.store.path(proposal_id,'.approved'),
                                 {'plan_id':proposal_id,'sha256':sha,'time':manager.clock(),'authorization':'local-owner-tty'})
        return {'approved':proposal_id,'sha256':sha}
