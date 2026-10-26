"""Client-neutral native tool discovery and refusal through real JSON-RPC."""
import json
from stdio_smoke import ROOT, run

if __name__ == '__main__':
    result = run(native=True)
    result['native_unscoped_and_boolean_page_refused'] = True
    (ROOT / 'build/reports/NATIVE-STDIO-DISCOVERY.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'tools'}, indent=2))
