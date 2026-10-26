"""Optional owner terminal controls for native grants; MCP provides the same paths."""
import argparse,json
from pathlib import Path
from .core import SafetyError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service',required=True,choices=('notes','reminders'))
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare');prepare.add_argument('--scope-file',type=Path,required=True)
    for command in ('activate','finalize','status','revoke'):
        commands.add_parser(command).add_argument('grant_id')
    changes=commands.add_parser('changes');changes.add_argument('grant_id');changes.add_argument('--changes-file',type=Path,required=True)
    args=parser.parse_args()
    from .native_tools import editor
    from .native_grants import NativeGrants
    engine=editor(args.service);manager=NativeGrants(engine)
    def read(path):
        with path.open(encoding='utf-8') as stream:raw=stream.read(100001)
        if len(raw)>100000:raise SafetyError('Native permission input exceeds review bound')
        return json.loads(raw)
    try:
        if args.command=='prepare':result=manager.prepare(read(args.scope_file))
        elif args.command=='changes':result=engine.prepare_with_grant(args.grant_id,read(args.changes_file))
        else:result=getattr(manager,args.command)(args.grant_id)
        print(json.dumps(result,ensure_ascii=True,indent=2))
    except (SafetyError,OSError,ValueError) as exc:parser.error(str(exc))


if __name__=='__main__':main()
