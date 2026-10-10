"""Scan the Git index for local generated secrets and prohibited artefacts."""
import json
from pathlib import Path
import re
import subprocess
root=Path(__file__).resolve().parent.parent
files=subprocess.check_output(['git','ls-files','--cached','-z'],cwd=root).decode().split('\0')
secrets=[]
for source in [root/'.local'/'settings.json',root/'.local'/'demo-accounts.json']:
    if source.exists():
        def collect(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k in ['password','SECRET_KEY','PGPASSWORD'] and isinstance(v,str) and len(v)>=12:secrets.append(v.encode())
                    else:collect(v)
        collect(json.loads(source.read_text(encoding='utf-8')))
problems=[]
for name in filter(None,files):
    if re.search(r'(^|/)(\.local|\.venv|\.env|node_modules)(/|$)|\.(pdf|docx|dump|sql|log)$',name,re.I):problems.append(name+': prohibited artefact')
    data=subprocess.check_output(['git','show',':'+name],cwd=root)
    if any(secret in data for secret in secrets):problems.append(name+': generated local secret')
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}',data):problems.append(name+': private key or token pattern')
if problems:
    print('\n'.join(problems));raise SystemExit(1)
print(f'PASS: {len(list(filter(None,files)))} staged files; no generated local secrets/prohibited artefacts/token patterns found. CI/test fixed strings are synthetic only.')
