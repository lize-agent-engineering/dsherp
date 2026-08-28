"""Run fixture setup without exposing generated credentials in terminal output."""
import json
from pathlib import Path
import subprocess
import sys
kind=sys.argv[1]
if kind not in ('platform','beta'):raise SystemExit('Expected platform or beta')
target=Path('.runtime') / f'{kind}-users.json'
if target.exists():raise SystemExit('Fixture file exists; refusing to overwrite')
script='seed_input = '+repr({'kind':kind})+'\n'+Path('infra/seed_identity.py').read_text()
result=subprocess.run(['docker','compose','-f','infra/compose.validation.yml','exec','-T',kind+'-backend','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True)
if result.returncode:
    print(result.stderr,file=sys.stderr);raise SystemExit(result.returncode)
data=json.loads(result.stdout)
target.write_text(json.dumps(data,ensure_ascii=False,indent=2));target.chmod(0o600)
print(f'{kind}: created {len(data)} synthetic users; private fixture stored')
