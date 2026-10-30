"""Twin-only temporary tunnel; private state lives outside the checkout."""
import base64,json,os,secrets,sys,urllib.request,urllib.error
from pathlib import Path
ROOT=Path.home()/'.local/share/sage-twin-emergency'
ROOT.mkdir(parents=True,exist_ok=True);ROOT.chmod(0o700)
os.umask(0o077)
HOST='twin.sage.org.ai';NAME='twin-emergency-egress-20261030'
env={}
for line in (Path.home()/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);env[k.strip().removeprefix('export ')]=v.strip().strip('"').strip("'")
headers={'Authorization':'Bearer '+env['CLOUDFLARE_API_TOKEN'],'Content-Type':'application/json'}
account=env['CLOUDFLARE_ACCOUNT_ID'];zone=env['CLOUDFLARE_ZONE_ID']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(method,path,body=None):
 req=urllib.request.Request('https://api.cloudflare.com/client/v4'+path,method=method,headers=headers,data=None if body is None else json.dumps(body).encode())
 try:
  with opener.open(req,timeout=20) as r:d=json.load(r)
 except urllib.error.HTTPError as e:raise RuntimeError(f'Cloudflare HTTP {e.code}') from None
 if not d.get('success'):raise RuntimeError('Cloudflare rejected operation')
 return d['result']
def save(name,data):
 p=ROOT/name;p.write_text(json.dumps(data,indent=2));p.chmod(0o600)
def record():
 r=api('GET',f'/zones/{zone}/dns_records?name={HOST}');assert len(r)==1;return r[0]
def body(r):return {k:r[k] for k in ['type','name','content','ttl','proxied','comment','tags','settings'] if k in r}
# Probe existing credentials without logging their values.
candidates=[{'Authorization':'Bearer '+env[k],'Content-Type':'application/json'} for k in ['CLOUDFLARE_API_TOKEN','CLOUDFLARE_BOOTSTRAP_TOKEN'] if env.get(k)]
if env.get('CLOUDFLARE_GLOBAL_API_KEY') and env.get('CLOUDFLARE_EMAIL'):
 candidates.append({'X-Auth-Key':env['CLOUDFLARE_GLOBAL_API_KEY'],'X-Auth-Email':env['CLOUDFLARE_EMAIL'],'Content-Type':'application/json'})
for candidate in candidates:
 headers=candidate
 try:
  api('GET',f'/zones/{zone}/dns_records?name={HOST}');break
 except RuntimeError:continue
else:raise RuntimeError('No existing credential can manage the Twin zone')
action=sys.argv[1]
if action=='inspect':
 r=record();print(json.dumps({'hostname':r['name'],'type':r['type'],'content':r['content'],'proxied':r['proxied']}))
elif action=='prepare':
 if not (ROOT/'original-dns.json').exists():save('original-dns.json',record())
 if not (ROOT/'secret.json').exists():save('secret.json',{'secret':base64.b64encode(secrets.token_bytes(32)).decode()})
 secret=json.loads((ROOT/'secret.json').read_text())['secret']
 existing=api('GET',f'/accounts/{account}/cfd_tunnel?name={NAME}&is_deleted=false')
 if existing:t=next(x for x in existing if x['name']==NAME)
 else:t=api('POST',f'/accounts/{account}/cfd_tunnel',{'name':NAME,'tunnel_secret':secret,'config_src':'local'})
 save('tunnel.json',{'id':t['id'],'name':NAME})
 save('credentials.json',{'AccountTag':account,'TunnelID':t['id'],'TunnelSecret':secret})
 save('config.json',{'tunnel':t['id'],'credentials-file':str(ROOT/'credentials.json'),'protocol':'http2','metrics':'127.0.0.1:37901','ingress':[{'hostname':HOST,'service':'http://127.0.0.1:35731'},{'service':'http_status:404'}]})
 print('Dedicated Twin tunnel prepared; DNS unchanged')
elif action=='activate':
 old=json.loads((ROOT/'original-dns.json').read_text());t=json.loads((ROOT/'tunnel.json').read_text());now=record()
 new=t['id']+'.cfargotunnel.com';assert now['id']==old['id'] and now['content'] in [old['content'],new]
 b=body(old);b.update(type='CNAME',content=new,proxied=True)
 api('PUT',f"/zones/{zone}/dns_records/{old['id']}",b);print('Twin DNS activated')
elif action=='rollback':
 old=json.loads((ROOT/'original-dns.json').read_text());t=json.loads((ROOT/'tunnel.json').read_text());now=record()
 assert now['id']==old['id'] and now['content'] in [old['content'],t['id']+'.cfargotunnel.com'],'DNS changed independently; refusing overwrite'
 api('PUT',f"/zones/{zone}/dns_records/{old['id']}",body(old));print('Original Twin DNS restored')
elif action=='status':
 t=json.loads((ROOT/'tunnel.json').read_text());d=api('GET',f"/accounts/{account}/cfd_tunnel/{t['id']}")
 print(json.dumps({'status':d.get('status'),'connections':len(d.get('connections',[])),'dns_on_bridge':record()['content']==t['id']+'.cfargotunnel.com'}))
else:raise SystemExit('inspect | prepare | activate | rollback | status')
