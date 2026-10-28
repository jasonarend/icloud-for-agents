"""Digest-only evidence for one admitted Reminder helper exchange.

These records do not settle uncertainty or authorize another operation. Missing
terminal evidence remains missing; no historical result is reconstructed.
"""
from .core import SafetyError, digest
from .native_execution import current
from .native_protocol import mutation_request


def begin(request):
    execution=current('reminders')
    if execution is None:
        return None
    store=execution['store'];identity=execution['plan_id'];index=execution['index']
    plan=store.read(store.path(identity,'.json'));payload=plan.get('payload',{})
    changes=payload.get('changes',[])
    if (plan.get('kind')!='reminders_change' or digest(plan)!=execution['plan_sha256']
            or store.read(store.path(identity,'.started')).get('sha256')!=digest(plan)
            or type(index) is not int or not isinstance(changes,list) or not 0<=index<len(changes)
            or request!=mutation_request('reminders',changes[index])):
        raise SafetyError('Reminder exchange does not match its admitted plan')
    context=payload.get('context',{})
    if not isinstance(context,dict) or not isinstance(context.get('backend'),str) or not context['backend']:
        raise SafetyError('Reminder exchange backend is not bound')
    record={'plan_sha256':digest(plan),'index':index,'request_sha256':digest(request),'backend':context['backend']}
    store.write_once(store.path(identity,'.native-reminder-request-'+str(index)),record)
    return {'store':store,'plan_id':identity,'index':index,'request':record}


def finish(exchange,response):
    if exchange is None:
        return
    if not isinstance(response,dict) or response.get('protocol_version')!=1:
        raise SafetyError('Invalid reminder terminal response')
    dispatched=response.get('mutation_dispatched')
    if type(dispatched) is not bool:dispatched=None
    record={'request_sha256':exchange['request']['request_sha256'],
            'plan_sha256':exchange['request']['plan_sha256'],'index':exchange['index'],
            'response_sha256':digest(response),'response_kind':'error' if 'error' in response else 'no_error_field',
            'mutation_dispatched':dispatched}
    exchange['store'].write_once(exchange['store'].path(exchange['plan_id'],'.native-reminder-response-'+str(exchange['index'])),record)


def inspect(store,plan):
    """Read optional evidence for a single-item plan without asserting causality."""
    identity=plan['plan_id'];request_path=store.path(identity,'.native-reminder-request-0')
    response_path=store.path(identity,'.native-reminder-response-0')
    if not request_path.exists():
        if response_path.exists():raise SafetyError('Reminder terminal evidence has no request binding')
        return {'terminal_response':'not_recorded','helper_reported_mutation_dispatched':None}
    request=store.read(request_path);item=plan['payload']['changes'][0]
    if not isinstance(request,dict) or type(request.get('index')) is not int or request!={'plan_sha256':digest(plan),'index':0,'request_sha256':digest(mutation_request('reminders',item)),
                 'backend':plan['payload']['context']['backend']}:
        raise SafetyError('Reminder request evidence does not match the original plan')
    if not response_path.exists():
        return {'terminal_response':'not_recorded','helper_reported_mutation_dispatched':None}
    response=store.read(response_path)
    sha=response.get('response_sha256') if isinstance(response,dict) else None
    reported=response.get('mutation_dispatched') if isinstance(response,dict) else None
    if (not isinstance(response,dict) or set(response)!={'request_sha256','plan_sha256','index','response_sha256','response_kind','mutation_dispatched'}
            or response['request_sha256']!=request['request_sha256'] or response['plan_sha256']!=request['plan_sha256'] or type(response['index']) is not int or response['index']!=0
            or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha)
            or not isinstance(response['response_kind'],str) or response['response_kind'] not in {'error','no_error_field'} or (reported is not None and type(reported) is not bool)):
        raise SafetyError('Reminder terminal evidence is malformed or mismatched')
    return {'terminal_response':'recorded','helper_reported_mutation_dispatched':reported}
