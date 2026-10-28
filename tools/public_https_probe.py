"""Probe complete HTTPS bodies across live DNS answers, without host pinning.

GET-only discovery; never retries application writes. TLS verifies the original
hostname on every connection. A healthy edge is scoped to one operator run.
"""
import argparse
import hashlib
import http.client
import json
import socket
import ssl
import time
import urllib.request
from urllib.parse import urlsplit


class EdgeHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, edge, **kwargs):
        super().__init__(host, **kwargs)
        self.edge = edge

    def connect(self):
        if self._tunnel_host:
            raise ValueError('edge transport does not support proxy tunnels')
        self.sock = socket.create_connection((self.edge, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.sock.close()
            raise


class EdgeHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, host, edge):
        super().__init__(context=ssl.create_default_context())
        self.host, self.edge = host, edge

    def https_open(self, request):
        # Do not carry a selected address across an HTTP redirect to another host.
        if urlsplit(request.full_url).hostname != self.host:
            return super().https_open(request)
        return self.do_open(lambda host, **kw: EdgeHTTPSConnection(host, edge=self.edge, **kw),
                            request, context=self._context)


def probe(url, *, timeout=12, max_bytes=2_000_000):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('an HTTPS URL without credentials or fragment is required')
    host, port = parsed.hostname, parsed.port or 443
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    reports = []
    for edge in addresses:
        started = time.monotonic()
        connection = EdgeHTTPSConnection(host, port=port, edge=edge, timeout=timeout,
                                          context=ssl.create_default_context())
        count, digest = 0, hashlib.sha256()
        report = {'address': edge, 'complete': False}
        try:
            connection.connect()
            transport = connection.sock
            connection.request('GET', (parsed.path or '/') + ('?'+parsed.query if parsed.query else ''),
                               headers={'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            report['status'] = response.status
            if response.status != 200:
                raise ValueError('probe requires HTTP 200')
            length = response.getheader('Content-Length')
            if length is not None and int(length) > max_bytes:
                raise ValueError('probe body exceeds limit')
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError('whole response deadline')
                transport.settimeout(remaining)
                data = response.read1(65536)
                if not data:
                    break
                count += len(data)
                if count > max_bytes:
                    raise ValueError('probe body exceeds limit')
                digest.update(data)
            if length is not None and count != int(length):
                raise http.client.IncompleteRead(b'', int(length)-count)
            report.update(complete=True, sha256=digest.hexdigest())
        except (OSError, ValueError, http.client.HTTPException) as error:
            report['error'] = type(error).__name__
        finally:
            connection.close()
        report.update(bytes=count, seconds=round(time.monotonic()-started,3))
        reports.append(report)
    return reports


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args=parser.parse_args()
    reports=probe(args.url)
    print(json.dumps(reports,indent=2))
    # Mixed results are degraded, not a healthy-site verdict.
    return 0 if reports and all(r['complete'] for r in reports) else 1

if __name__ == '__main__':
    raise SystemExit(main())
