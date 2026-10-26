"""Reviewed OS-consent requests, independent of application write access.

The native OS dialog is always handled by the user. A completed request is not a
successful grant and never changes service selection, source scope or write mode.
"""
from .approvals import PlanStore
from .core import SafetyError
from .native_access import access_status
from .native_notes import NotesCreateAdapter
from .native_reminders import RemindersCreateAdapter
from .native_read import NativeReader, native_config


class NativePermissions:
    def __init__(self,name,account,store=None):
        if name not in {'notes','reminders'}:raise SafetyError('Unknown native permission service')
        self.name,self.account,self.store=name,account,store or PlanStore()
        self.kind=name+'_access'

    def context(self):
        status=access_status(self.name)
        if status['effective_access']=='disabled':raise SafetyError('Native service access is disabled')
        adapter=NotesCreateAdapter() if self.name=='notes' else RemindersCreateAdapter()
        return {'service':self.name,'backend':adapter.backend_id,'source_ids':sorted(status['source_ids']),
                'access_mode':status['effective_access'],'approval_policy':status['approval_policy']}

    def prepare(self):
        return self.store.prepare(self.kind,{'account':self.account,'context':self.context(),
            'operation':'request_os_access','effects':{
                'os_scope':'Notes Automation: OS-level app access across Notes accounts' if self.name=='notes' else 'EventKit full read/write Reminders access across available sources',
                'user_action':'The user handles the macOS consent dialog; denial is respected.',
                'application_scope':'No service selection, source allowlist or write-mode change.',
                'native_data':'No notes, folders, reminders or lists are created or changed.'}})

    def execute(self,plan_id):
        context=self.context();plan=self.store.proposal(plan_id)
        if (plan.get('kind')!=self.kind or plan['payload'].get('account')!=self.account
                or plan['payload'].get('operation')!='request_os_access' or plan['payload'].get('context')!=context):
            raise SafetyError('OS-access proposal owner, service, backend or scope changed')
        self.store.consume(plan_id,self.account)
        try:
            if self.context()!=context:raise SafetyError('Access changed before OS request')
            response=NativeReader(self.name)._dispatch({'protocol_version':1,'service':self.name,
                'action':'request_access','source_ids':context['source_ids']},native_config(self.name))
            if self.name=='notes':
                state=response.get('permission_state','unverified')
                state=state if state in {'granted','denied','requires_consent','restricted'} else 'unverified'
            else:
                state='granted' if response.get('granted') is True else 'denied' if response.get('granted') is False and response.get('error_present') is False else 'unverified'
        except Exception:
            # No native data mutation was requested. Permission status can be
            # inspected read-only; a timeout must not trigger another prompt.
            state='unverified'
        return self.store.finish(plan_id,{'status':'completed','permission_state':state,
            'application_access':context['access_mode'],'write_access_changed':False,
            'data_mutated':False,'do_not_retry':True,'next_step':'Inspect service status; the OS dialog belongs to the user.'})
