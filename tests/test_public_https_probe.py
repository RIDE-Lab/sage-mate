import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest

spec=importlib.util.spec_from_file_location('public_https_probe',Path(__file__).parents[1]/'tools/public_https_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)

def test_edge_tls_keeps_original_host_and_closes_on_verification_error(monkeypatch):
    sock=Mock();context=Mock();context.wrap_socket.side_effect=probe.ssl.SSLCertVerificationError()
    monkeypatch.setattr(probe.socket,'create_connection',Mock(return_value=sock))
    connection=probe.EdgeHTTPSConnection('example.org',edge='192.0.2.4',context=context)
    with pytest.raises(probe.ssl.SSLCertVerificationError):connection.connect()
    context.wrap_socket.assert_called_once_with(sock,server_hostname='example.org')
    sock.close.assert_called_once()

def test_body_incomplete_200_does_not_pass_and_next_dns_answer_is_probed(monkeypatch):
    monkeypatch.setattr(probe.socket,'getaddrinfo',lambda *a,**kw:[(0,0,0,'',(ip,443)) for ip in ['192.0.2.1','192.0.2.2']])
    connections=[]
    def connect(host,**kwargs):
        connection=Mock();response=Mock();response.status=200;response.getheader.return_value='4'
        response.read1.side_effect=[b'ab',b''] if not connections else [b'abcd',b'']
        connection.getresponse.return_value=response;connections.append(connection);return connection
    monkeypatch.setattr(probe,'EdgeHTTPSConnection',connect)
    reports=probe.probe('https://example.org/app.js')
    assert [r['complete'] for r in reports]==[False,True]
    assert reports[0]['error']=='IncompleteRead'
    assert reports[1]['bytes']==4
    for c in connections:
        assert c.request.call_args.args[0]=='GET'
        c.close.assert_called_once()

@pytest.mark.parametrize('url',['http://example.org','https://user:secret@example.org/','https://example.org/#fragment'])
def test_unsafe_probe_urls_rejected_before_resolving(monkeypatch,url):
    resolve=Mock();monkeypatch.setattr(probe.socket,'getaddrinfo',resolve)
    with pytest.raises(ValueError):probe.probe(url)
    resolve.assert_not_called()
