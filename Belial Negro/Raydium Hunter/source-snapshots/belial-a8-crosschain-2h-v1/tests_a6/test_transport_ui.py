import io,json,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from a6.http_pool import Pool
from a51.terminal_projection import compact_platform
class FakeResponse:
    status=200;headers={};reason='OK';will_close=False
    def read(self,n):return b'{"result":1}'
class FakeConnection:
    created=[]
    def __init__(self,*a,**kw):self.sock=None;self.calls=[];self.closed=False;self.created.append(self)
    def request(self,*a,**kw):self.calls.append((a,kw))
    def getresponse(self):return FakeResponse()
    def close(self):self.closed=True
class TransportTests(unittest.TestCase):
    def test_https_connection_reused_with_fully_consumed_body(self):
        FakeConnection.created=[]
        with patch('a6.http_pool.http.client.HTTPSConnection',FakeConnection):
            pool=Pool();req=urllib.request.Request('https://example.invalid/read',data=b'{}')
            with pool.open(req) as r:self.assertEqual(json.load(r),{'result':1})
            with pool.open(req) as r:self.assertEqual(json.load(r),{'result':1})
            self.assertEqual(len(FakeConnection.created),1);self.assertEqual(len(FakeConnection.created[0].calls),2)
    def test_redirect_not_followed(self):
        with patch('a6.http_pool.http.client.HTTPSConnection',FakeConnection),patch.object(FakeResponse,'status',302):
            with self.assertRaises(urllib.error.HTTPError):Pool().open(urllib.request.Request('https://example.invalid/read'))
    def test_plain_http_rejected(self):
        with self.assertRaises(ValueError):Pool().open(urllib.request.Request('http://example.invalid/read'))
    def test_capture_clock_projection(self):
        p=compact_platform(dict(run_id='test',duration_ms=7200000,accumulated_active_ms=123,current_segment='seg-02',gaps=[{'duration_ms':50}]))
        self.assertEqual(p['run_id'],'test');self.assertEqual(p['accumulated_active_ms'],123);self.assertEqual(p['gaps'][0]['duration_ms'],50)
