"""Validate the complete bounded graph exposed by two selected-list providers."""
import uuid
from .core import SafetyError


def validate_list_graph(result,list_id,source_ids):
    if (not isinstance(result,dict) or result.get('protocol_version')!=1 or result.get('list_id')!=list_id
            or result.get('source_id') not in source_ids or result.get('backend')!='reminderkit-list-hierarchy-v1'
            or result.get('data_mutated') is not False or result.get('membership_verified') is not True
            or result.get('hierarchy_complete') is not True or result.get('observation_consistency')!='separate_provider_reads'
            or result.get('coverage')!='native_list_and_subtasks_matching_eventkit'
            or not isinstance(result.get('nodes'),list) or len(result['nodes'])>200):
        raise SafetyError('Complete scoped native/EventKit list hierarchy could not be verified')
    nodes=[];parents={}
    try:
        for row in result['nodes']:
            if not isinstance(row,dict) or set(row)!={'id','parent_id','title','completed'}:raise ValueError()
            identity=str(uuid.UUID(row['id'])).upper()
            parent=None if row['parent_id'] is None else str(uuid.UUID(row['parent_id'])).upper()
            if (identity!=row['id'].upper() or (parent is not None and parent!=row['parent_id'].upper()) or identity in parents or parent==identity
                    or not isinstance(row['title'],str) or len(row['title'])>1000 or len(row['title'].encode())>4000
                    or type(row['completed']) is not bool):raise ValueError()
            nodes.append(dict(row,id=identity,parent_id=parent));parents[identity]=parent
        for identity in parents:
            chain=set();current=identity
            while current is not None:
                if current not in parents or current in chain or len(chain)>=32:raise ValueError()
                chain.add(current);current=parents[current]
    except (ValueError,TypeError,AttributeError,UnicodeError):
        raise SafetyError('List hierarchy has invalid, duplicate, missing, cyclic or out-of-bound native relationships') from None
    return {key:result[key] for key in ('protocol_version','source_id','list_id','backend','data_mutated','membership_verified','hierarchy_complete','observation_consistency','coverage')}|{'nodes':sorted(nodes,key=lambda row:row['id'])}
