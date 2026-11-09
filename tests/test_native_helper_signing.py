"""Local helper builds sign only with the caller's explicit selected identity."""
import contextlib,io,runpy,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
class NativeHelperSigningTests(unittest.TestCase):
 def build(self,identity=None,*,exists=False):
  with tempfile.TemporaryDirectory(prefix='native-helper-signing-')as directory:
   bundle=Path(directory)/'iCloud for Agents.app';calls=[]
   developer=Path(directory)/'Developer';developer.mkdir()
   sdk=developer/'MacOSX.sdk';sdk.mkdir()
   compilers={}
   for name in ('clang','swiftc'):
    path=developer/name;path.write_text('synthetic');path.chmod(0o700);compilers[name]=str(path)
   if exists:bundle.mkdir();(bundle/'preserved').write_text('unchanged')
   def run(command,**kwargs):
    self.assertTrue(kwargs.get('check'));self.assertNotIn('shell',kwargs);calls.append(list(command))
    if command[0]=='/usr/bin/xcode-select':return SimpleNamespace(stdout=str(developer)+'\n')
    if command[0]=='/usr/bin/xcrun':return SimpleNamespace(stdout=(str(sdk)if '--show-sdk-path'in command else compilers[command[-1]])+'\n')
    if '-o'in command:
     output=Path(command[command.index('-o')+1]);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(b'synthetic unexecuted compiler output')
   argv=['build-native-helper.py','--output',str(bundle)]
   if identity is not None:argv+=['--sign-identity='+identity]
   module=runpy.run_path(str(ROOT/'scripts/build-native-helper.py'))
   with patch.object(sys,'argv',argv),patch('subprocess.run',side_effect=run),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    try:module['main']();status=0
    except SystemExit as exc:status=exc.code
   if exists:self.assertEqual((bundle/'preserved').read_text(),'unchanged')
   return status,calls
 def test_default_ad_hoc_signing_preserves_both_fixed_identities(self):
  status,calls=self.build();self.assertEqual(status,0);signs=[v for v in calls if v[0]=='/usr/bin/codesign'];self.assertEqual(len(signs),2)
  self.assertEqual([v[3]for v in signs],['-','-']);self.assertEqual([v[5]for v in signs],['com.jasonarend.icloud-for-agents.native.image-text','com.jasonarend.icloud-for-agents.native']);self.assertTrue(all('--timestamp=none'not in v for v in signs));self.assertTrue(signs[0][-1].endswith('/Contents/MacOS/icloud-agents-image-text'));self.assertTrue(signs[1][-1].endswith('/iCloud for Agents.app'))
 def test_explicit_certificate_is_single_argument_for_nested_and_outer_code(self):
  identity='Synthetic Signing Identity; $(never executed)';status,calls=self.build(identity);self.assertEqual(status,0);signs=[v for v in calls if v[0]=='/usr/bin/codesign'];self.assertEqual(len(signs),2)
  for command in signs:self.assertEqual(command[3],identity);self.assertIn('--timestamp=none',command)
  self.assertFalse(any(v[0].endswith('/security')or 'notarytool'in v or 'spctl'in v for v in calls))
 def test_invalid_identity_refuses_before_compilation_or_keychain_use(self):
  for identity in('',' ','\n','name\x7f','--deep','x'*1001):
   status,calls=self.build(identity);self.assertNotEqual(status,0);self.assertEqual(calls,[])
 def test_existing_helper_is_preserved_before_any_compilation_or_signing(self):
  status,calls=self.build('Synthetic Signing Identity',exists=True);self.assertNotEqual(status,0);self.assertEqual(calls,[])
