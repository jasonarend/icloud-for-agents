"""Review a native service setup edit; apply only with existing owner authority."""
import argparse,json
from pathlib import Path
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_setup import preview,apply

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--service',required=True,choices=('notes','reminders'))
    parser.add_argument('--helper',type=Path,help='Exact signed helper executable from the reviewed build')
    parser.add_argument('--source-id',action='append',default=None,help='Exact discovered source ID; repeat for selected sources')
    parser.add_argument('--clear-sources',action='store_true',help='Explicitly select discovery-only access')
    parser.add_argument('--disable-service',action='store_true',help='Disable after the configured clients/server restart')
    parser.add_argument('--metadata-backend',choices=('scripting','notes-store-readonly','native','reminders-store-readonly'),help='Explicit optional metadata selection, validated for the selected service; omission preserves it')
    parser.add_argument('--apply-review',help='Apply this exact preview digest; not proof of human approval')
    args=parser.parse_args()
    if args.clear_sources and args.source_id is not None:parser.error('Choose exact source IDs or discovery-only access')
    sources=[] if args.clear_sources else args.source_id
    try:
        if args.apply_review:result=apply(args.root,args.service,args.helper,args.apply_review,source_ids=sources,enabled=not args.disable_service,metadata_backend=args.metadata_backend)
        else:result=preview(args.root,args.service,args.helper,source_ids=sources,enabled=not args.disable_service,metadata_backend=args.metadata_backend)[0]
        print(json.dumps(result,indent=2))
    except (SafetyError,ValueError,OSError) as exc:parser.error(str(exc))

if __name__=='__main__':main()
