"""No network/DNS mutations: exercise the operational rollback guard in isolation."""
import copy,contextlib,io,json,runpy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SCRIPT=Path(__file__).with_name('cloudflare_bridge.py')
class FakeAPI:
 def __init__(self):
  self.original={'id':'dns-1','type':'CNAME','name':'twin.sage.org.ai','content':'original.cfargotunnel.com','ttl':1,'proxied':True,'comment':'preserve me','tags':['owner:twin']}
  self.record=copy.deepcopy(self.original);self.tunnels=[];self.writes=[]
 def open(self,req,timeout):
  method=req.get_method();url=req.full_url;payload=json.loads(req.data) if req.data else None
  if method=='GET' and '/dns_records' in url:result=[self.record]
  elif method=='GET' and '/cfd_tunnel?' in url:result=self.tunnels
  elif method=='POST' and url.endswith('/cfd_tunnel'):
   self.tunnels=[{'id':'new-tunnel','name':payload['name']}];result=self.tunnels[0]
  elif method=='PUT' and url.endswith('/dns_records/dns-1'):
   self.writes.append(payload);self.record={'id':'dns-1',**payload};result=self.record
  else:raise AssertionError((method,url))
  return io.BytesIO(json.dumps({'success':True,'result':result}).encode())
class RollbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.api=FakeAPI()
  (self.home/'.env').write_text('CLOUDFLARE_API_TOKEN=synthetic\nCLOUDFLARE_ACCOUNT_ID=account\nCLOUDFLARE_ZONE_ID=zone\n')
 def tearDown(self):self.tmp.cleanup()
 def run_action(self,action):
  with patch.object(Path,'home',return_value=self.home),patch('urllib.request.build_opener',return_value=self.api),patch.object(sys,'argv',[str(SCRIPT),action]),contextlib.redirect_stdout(io.StringIO()):runpy.run_path(str(SCRIPT),run_name='__main__')
 def test_prepare_does_not_switch_dns(self):
  self.run_action('prepare');self.assertEqual(self.api.record,self.api.original);self.assertEqual(self.api.writes,[])
  self.assertEqual((self.home/'.local/share/sage-twin-emergency/credentials.json').stat().st_mode & 0o777,0o600)
 def test_round_trip_preserves_dns_fields(self):
  self.run_action('prepare');self.run_action('activate');self.assertEqual(self.api.record['content'],'new-tunnel.cfargotunnel.com')
  self.run_action('rollback');self.assertEqual(self.api.record,self.api.original)
  self.assertEqual({w['name'] for w in self.api.writes},{'twin.sage.org.ai'})
 def test_independent_dns_change_is_not_overwritten(self):
  self.run_action('prepare');self.run_action('activate');self.api.record['content']='someone-elses.cfargotunnel.com';n=len(self.api.writes)
  with self.assertRaises(AssertionError):self.run_action('rollback')
  self.assertEqual(len(self.api.writes),n)
 def test_prepare_is_idempotent(self):
  self.run_action('prepare');p=self.home/'.local/share/sage-twin-emergency/credentials.json';before=p.read_bytes();self.run_action('prepare');self.assertEqual(p.read_bytes(),before);self.assertEqual(len(self.api.tunnels),1)
if __name__=='__main__':unittest.main()
