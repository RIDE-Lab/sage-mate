"""Apply only main's accepted credential alias; preserve old answer engine."""
from pathlib import Path
import hashlib,json,subprocess,sys
ROOT=Path.home()/'twin-alternate-egress-20261030'
BASE=Path('/home/shuhao/sage-mate-releases/twin-sidebar-e5878bf7-2687cb0d/source')
p=BASE/'src/sage_faculty_twin/config.py';b=ROOT/'original-config.py'
needle='    tavily_api_key: str = Field(default="")\n'
addition='''
    @field_validator("tavily_api_key", mode="before")
    @classmethod
    def resolve_tavily_credential(cls, value):
        # Deployment already supplies TAVILY_TOKEN; resolve it in memory only.
        # An explicit application key takes precedence. Never log credentials.
        import os
        return value or os.environ.get("TAVILY_API_KEY") or os.environ.get("TAVILY_TOKEN") or ""
'''
if sys.argv[1]=='activate':
 original=subprocess.check_output(['git','-C',str(BASE),'show','HEAD:src/sage_faculty_twin/config.py']).decode()
 assert p.read_text()==original,'Unexpected production config.py changes'
 assert original.count(needle)==1
 assert 'from pydantic import Field, model_validator' in original
 b.write_text(original);b.chmod(0o600)
 candidate=original.replace(needle,needle+addition).replace('from pydantic import Field, model_validator','from pydantic import Field, field_validator, model_validator')
 compile(candidate,str(p),'exec');p.write_text(candidate)
 (ROOT/'auth-patch-sha256.txt').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
 print('Credential alias applied; restart still required')
elif sys.argv[1]=='rollback':
 if b.exists():
  assert hashlib.sha256(p.read_bytes()).hexdigest()==(ROOT/'auth-patch-sha256.txt').read_text(),'Independent config change; refusing restore'
  p.write_bytes(b.read_bytes());print('Original config.py restored')
else:raise SystemExit('activate | rollback')
