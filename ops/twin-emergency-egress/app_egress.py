"""Run on A100: activate or roll back only Twin's process environment."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time,urllib.request
BASE=Path('/home/shuhao/sage-mate-releases/twin-sidebar-e5878bf7-2687cb0d/source')
ROOT=Path.home()/'twin-alternate-egress-20261030';ROOT.mkdir(exist_ok=True,mode=0o700)
DROP=Path.home()/'.config/systemd/user/sage-mate-app.service.d/emergency-egress-20261030.conf'
TEXT='''[Service]
Environment="HTTP_PROXY=http://127.0.0.1:17898"
Environment="HTTPS_PROXY=http://127.0.0.1:17898"
Environment="http_proxy=http://127.0.0.1:17898"
Environment="https_proxy=http://127.0.0.1:17898"
Environment="TAVILY_HTTP_PROXY=http://127.0.0.1:17898"
Environment="TAVILY_HTTPS_PROXY=http://127.0.0.1:17898"
Environment="NO_PROXY=localhost,127.0.0.1,::1,11.11.11.34"
Environment="no_proxy=localhost,127.0.0.1,::1,11.11.11.34"
'''
def run(*a):return subprocess.check_output(a,text=True).strip()
def pid(u):return run('systemctl','--user','show',u,'-p','MainPID','--value')
def health():
 o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(30):
  try:
   with o.open('http://127.0.0.1:55601/health',timeout=2) as r:
    if json.load(r).get('status')=='ok':return
  except Exception:pass
  time.sleep(1)
 raise RuntimeError('Twin did not become healthy')
engine=pid('vllm-hust-qwen35-engine.service');app=pid('sage-mate-app.service')
assert Path('/proc/'+app+'/cwd').resolve()==BASE
assert run('git','-C',str(BASE),'rev-parse','HEAD')=='e5878bf72ac340b87045e6ab15750800a6a26837'
action=sys.argv[1]
if action=='activate':
 assert not DROP.exists(),'Inspect existing override first'
 DROP.write_text(TEXT);DROP.chmod(0o600)
elif action=='rollback':
 if DROP.exists():
  assert DROP.read_text()==TEXT,'Override modified independently'
  DROP.unlink()
else:raise SystemExit('activate | rollback')
try:
 run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','sage-mate-app.service');health()
 assert engine==pid('vllm-hust-qwen35-engine.service')
 assert Path('/proc/'+pid('sage-mate-app.service')+'/cwd').resolve()==BASE
except BaseException:
 if action=='activate':
  DROP.unlink(missing_ok=True);run('systemctl','--user','daemon-reload');run('systemctl','--user','restart','sage-mate-app.service')
 raise
r={'action':action,'engine_pid_unchanged':engine,'app_pid':pid('sage-mate-app.service'),'backend_revision':run('git','-C',str(BASE),'rev-parse','HEAD'),'dropin':str(DROP)}
(ROOT/(action+'.json')).write_text(json.dumps(r,indent=2));print(json.dumps(r))
