import json,os,plistlib,re,signal,subprocess,time,urllib.request
from pathlib import Path
root=Path.home()/'.local/share/sage-twin-emergency';label='org.sage.twin.emergency-isolated-probe';uid=os.getuid();target=f'gui/{uid}/{label}'
p=Path.home()/f'Library/LaunchAgents/{label}.plist';assert not p.exists()
config=plistlib.loads((Path.home()/'Library/LaunchAgents/org.sage.twin.emergency-bridge.plist').read_bytes())
config['Label']=label;config['ProgramArguments']=[s.replace('127.0.0.1:35731:','127.0.0.1:35732:').replace('127.0.0.1:17898:','127.0.0.1:17899:') for s in config['ProgramArguments']]
config['StandardOutPath']=config['StandardErrorPath']=str(root/'isolated-probe.log')
p.write_bytes(plistlib.dumps(config));p.chmod(0o600)
o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def state():
 s=subprocess.check_output(['launchctl','print',target],text=True);m=re.search(r'\bpid = (\d+)',s);return int(m[1]) if m else None
def ready():
 try:
  with o.open('http://127.0.0.1:35732/health',timeout=2) as r:return json.load(r)['status']=='ok'
 except Exception:return False
try:
 subprocess.run(['launchctl','bootstrap',f'gui/{uid}',str(p)],check=True)
 end=time.monotonic()+40
 while not ready():
  assert time.monotonic()<end;time.sleep(1)
 first=state();assert first;os.kill(first,signal.SIGTERM);start=time.monotonic();end=start+45
 while True:
  current=state()
  if current and current!=first and ready():break
  assert time.monotonic()<end;time.sleep(1)
 print(json.dumps({'isolated_probe':True,'automatic_restart':True,'seconds':round(time.monotonic()-start,2),'old_pid':first,'new_pid':current,'only_get_health':True}))
finally:
 subprocess.run(['launchctl','bootout',target],capture_output=True);p.unlink(missing_ok=True)
