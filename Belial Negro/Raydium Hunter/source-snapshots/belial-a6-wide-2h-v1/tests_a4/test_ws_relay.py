"""RFC frame checks and REAL loopback TCP multiplex tests. No public RPC or TLS/market claim."""
import asyncio,base64,json,os,struct,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from a4 import ws_relay as w

class Writer:
    def __init__(self):self.data=bytearray();self.closed=False
    def write(self,v):self.data.extend(v)
    async def drain(self):pass
    def close(self):self.closed=True
    async def wait_closed(self):pass

class FrameTests(unittest.IsolatedAsyncioTestCase):
    async def read(self,data,mask=False):
        r=asyncio.StreamReader();r.feed_data(data);r.feed_eof();writer=Writer();out=await w.read_message(r,writer,expect_mask=mask);return out,writer
    async def test_masked_text_roundtrip(self):r,_=await self.read(w.frame(1,'{"x":1}',True),True);self.assertEqual(r,{'x':1})
    async def test_unmasked_text_roundtrip(self):r,_=await self.read(w.frame(1,'{"x":1}'));self.assertEqual(r,{'x':1})
    async def test_extended_short_frame(self):r,_=await self.read(w.frame(1,json.dumps({'x':'a'*400})));self.assertEqual(len(r['x']),400)
    async def test_extended_long_frame(self):r,_=await self.read(w.frame(1,json.dumps({'x':'a'*70000})));self.assertEqual(len(r['x']),70000)
    async def test_missing_mask_refused(self):
        with self.assertRaises(ValueError):await self.read(w.frame(1,'{}'),True)
    async def test_unexpected_mask_refused(self):
        with self.assertRaises(ValueError):await self.read(w.frame(1,'{}',True))
    async def test_list_refused(self):
        with self.assertRaises(ValueError):await self.read(w.frame(1,'[]'))
    async def test_nonfinite_refused(self):
        with self.assertRaises(ValueError):await self.read(w.frame(1,'{"x":NaN}'))
    async def test_binary_not_silently_parsed(self):
        with self.assertRaises(ValueError):await self.read(w.frame(2,'{}'))
    async def test_ping_returns_heartbeat_and_flushes_pong(self):r,writer=await self.read(w.frame(9,b'test'));self.assertIsNone(r);self.assertEqual(writer.data[0],138)
    async def test_pong_heartbeat(self):r,_=await self.read(w.frame(10,b'test'));self.assertIsNone(r)
    async def test_fragmented_text(self):
        first=bytearray(w.frame(1,b'{"x":'));first[0]&=127;last=w.frame(0,b'1}');r,_=await self.read(first+last);self.assertEqual(r,{'x':1})
    async def test_ping_during_fragment(self):
        first=bytearray(w.frame(1,b'{\"x\":'));first[0]&=127;r,_=await self.read(first+w.frame(9,b'ping')+w.frame(0,b'1}'));self.assertEqual(r,{'x':1})
    async def test_pong_during_fragment(self):
        first=bytearray(w.frame(1,b'{"x":'));first[0]&=127;r,_=await self.read(first+w.frame(10,b'ping')+w.frame(0,b'1}'));self.assertEqual(r,{'x':1})
    async def test_orphan_fragment_refused(self):
        with self.assertRaises(ValueError):await self.read(w.frame(0,b'{}'))
    async def test_nested_fragment_refused(self):
        first=bytearray(w.frame(1,b'{'));first[0]&=127
        with self.assertRaises(ValueError):await self.read(first+w.frame(1,b'{}'))
    async def test_rsv_refused(self):
        raw=bytearray(w.frame(1,b'{}'));raw[0]|=64
        with self.assertRaises(ValueError):await self.read(raw)
    async def test_control_too_long(self):
        with self.assertRaises(ValueError):await self.read(w.frame(9,b'a'*130))
    async def test_frame_limit_refused_before_read(self):
        with self.assertRaises(ValueError):await self.read(bytes([129,127])+struct.pack('!Q',w.MAX_FRAME+1))
    async def test_peer_close(self):
        with self.assertRaises(ConnectionError):await self.read(w.frame(8))
    async def test_http_parser(self):
        r=asyncio.StreamReader();r.feed_data(b'HTTP/1.1 429 Too Many\r\nRetry-After: 30\r\nX: a\r\nX: b\r\n\r\n');r.feed_eof();line,h=await w.headers(r);self.assertEqual(h['retry-after'],'30');self.assertEqual(h['x'],'a,b')

class BackoffTests(unittest.TestCase):
    def test_403_never_retry(self):self.assertIsNone(w.retry_delay(1,403))
    def test_401_never_retry(self):self.assertIsNone(w.retry_delay(100,401))
    def test_429_respects_seconds(self):self.assertGreaterEqual(w.retry_delay(1,429,{'retry-after':'87'}),87)
    def test_429_floor_30_seconds(self):self.assertGreaterEqual(w.retry_delay(1,429,{}),30)
    def test_exponential_bounded(self):self.assertLess(w.retry_delay(100),301);self.assertGreaterEqual(w.retry_delay(7),128)
    def test_retry_date(self):self.assertEqual(w.retry_after({'retry-after':'Thu, 01 Jan 1970 00:01:00 GMT'},at=0),60)
    def test_invalid_retry(self):self.assertEqual(w.retry_after({'retry-after':'not-time'}),0)
    def test_negative_retry_clamped(self):self.assertEqual(w.retry_after({'retry-after':'-2'}),0)
    def test_frame_encoder_size_limit(self):self.assertRaises(ValueError,w.frame,1,b'x'*(w.MAX_FRAME+1))
    def test_empty_pools_not_started(self):
        with tempfile.TemporaryDirectory() as d:self.assertRaises(ValueError,w.Relay,'wss://not-used',[],d)
    def test_25_pools_refused(self):
        with tempfile.TemporaryDirectory() as d:self.assertRaises(ValueError,w.Relay,'wss://not-used',list(map(str,range(25))),d)
    def test_local_queue_overflow_disconnects(self):
        writer=Writer();c=w.Client(writer)
        for _ in range(512):self.assertTrue(c.push({'x':1}))
        self.assertFalse(c.push({'x':1}));self.assertTrue(writer.closed)

async def until(fn,timeout=5):
    end=time.monotonic()+timeout
    while not fn():
        if time.monotonic()>end:raise AssertionError('fixture timed out')
        await asyncio.sleep(.01)

class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.connections=0;self.sockets=[];self.up=[];self.tasks=[]
        async def fixture(r,writer):
            self.connections+=1;self.up.append(writer);self.sockets.append(writer)
            try:
                while True:
                    row=await w.read_message(r,writer,expect_mask=True)
                    if row is None:continue
                    # The fixture records the subscriptions; no invented feed in production.
                    if row['method']=='logsSubscribe':writer.write(w.frame(1,json.dumps({'jsonrpc':'2.0','id':row['id'],'result':100+row['id']})));await writer.drain()
            except (Exception,asyncio.CancelledError):pass
            finally:writer.close()
        self.server=await asyncio.start_server(fixture,'127.0.0.1',0)
        async def connect(url):return await asyncio.open_connection('127.0.0.1',self.server.sockets[0].getsockname()[1])
        self.relay=w.Relay('wss://fixture.invalid',['POOL_A','POOL_B'],self.p,connect=connect)
        self.task=asyncio.create_task(self.relay.serve());await until(lambda:self.relay.state=='ACTIVE')
    async def asyncTearDown(self):
        self.relay.stop.set();await asyncio.wait_for(self.task,3)
        for x in self.sockets:x.close()
        self.server.close();await self.server.wait_closed();await asyncio.sleep(.03);self.tmp.cleanup()
    async def client(self,path=None,origin=False):
        r,writer=await asyncio.open_connection('127.0.0.1',self.relay.port);self.sockets.append(writer)
        key=base64.b64encode(b'0123456789abcdef').decode();head=f'GET {path or self.relay.path} HTTP/1.1\r\nHost: 127.0.0.1:{self.relay.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n'+('Origin: http://evil.invalid\r\n' if origin else '')+'\r\n'
        writer.write(head.encode());await writer.drain();line,h=await w.headers(r);return r,writer,line
    async def subscribe(self,r,writer,pool,ident=1):
        writer.write(w.frame(1,json.dumps({'jsonrpc':'2.0','id':ident,'method':'logsSubscribe','params':[{'mentions':[pool]},{'commitment':'confirmed'}]}),True));await writer.drain();return await asyncio.wait_for(w.read_message(r,writer,expect_mask=False),2)
    async def emit(self,sub,value='SIG'):
        data={'jsonrpc':'2.0','method':'logsNotification','params':{'subscription':sub,'result':{'context':{'slot':1},'value':{'signature':value,'err':None,'logs':['fixture']}}}}
        self.up[-1].write(w.frame(1,json.dumps(data)));await self.up[-1].drain()
    async def test_two_pools_two_clients_one_upstream(self):
        ra,wa,_=await self.client();rb,wb,_=await self.client();a=await self.subscribe(ra,wa,'POOL_A');b=await self.subscribe(rb,wb,'POOL_B');self.assertNotEqual(a['result'],b['result']);self.assertEqual(self.connections,1)
        await self.emit(101,'FIRST');row=await asyncio.wait_for(w.read_message(ra,wa,expect_mask=False),2);self.assertEqual(row['params']['subscription'],a['result']);self.assertEqual(row['params']['result']['value']['signature'],'FIRST');self.assertIn('belial_upstream_received_ms',row)
        await self.emit(102,'SECOND');row=await asyncio.wait_for(w.read_message(rb,wb,expect_mask=False),2);self.assertEqual(row['params']['subscription'],b['result']);self.assertEqual(row['params']['result']['value']['signature'],'SECOND')
    async def test_many_local_clients_do_not_multiply_remote_connections(self):
        for i in range(12):r,writer,_=await self.client();await self.subscribe(r,writer,'POOL_A' if i%2==0 else 'POOL_B',i+1)
        self.assertEqual(self.connections,1);self.assertEqual(self.relay.stats['connection_attempts'],1)
    async def test_unknown_pool_client_closed(self):
        r,writer,_=await self.client()
        with self.assertRaises(asyncio.IncompleteReadError):await self.subscribe(r,writer,'UNKNOWN')
    async def test_origin_refused(self):r,writer,line=await self.client(origin=True);self.assertIn('403',line)
    async def test_wrong_path_refused(self):r,writer,line=await self.client('/not-allowed');self.assertIn('403',line)
    async def test_write_method_refused(self):
        r,writer,_=await self.client();writer.write(w.frame(1,json.dumps({'jsonrpc':'2.0','id':1,'method':'sendTransaction','params':[]}),True));await writer.drain();self.assertEqual(await asyncio.wait_for(r.read(1),2),b'')
    async def test_unsubscribe_is_local(self):
        r,writer,_=await self.client();sub=await self.subscribe(r,writer,'POOL_A');writer.write(w.frame(1,json.dumps({'jsonrpc':'2.0','id':2,'method':'logsUnsubscribe','params':[sub['result']]}),True));await writer.drain();row=await w.read_message(r,writer,expect_mask=False);self.assertTrue(row['result']);self.assertEqual(len(self.relay.up_ids),2)
    async def test_stale_queued_delivery_disconnects_not_replays(self):
        c=w.Client(Writer());c.push({'x':1},received_ms=w.now_ms()-1000);await self.relay.writer_loop(c);self.assertTrue(c.closed);self.assertEqual(self.relay.stats['stale_delivery_disconnects'],1)
    async def test_disconnect_closes_consumers_then_backoff(self):
        r,writer,_=await self.client();await self.subscribe(r,writer,'POOL_A');self.up[-1].close()
        # Reader task wakes when the connection closes; supervisor heartbeat loop detects it.
        await until(lambda:self.relay.state=='COOLDOWN',timeout=23);self.assertEqual(await asyncio.wait_for(r.read(1),2),b'');self.assertEqual(self.connections,1)
    async def test_status_reports_actual_not_desired_counts(self):
        self.relay.mark('ACTIVE');s=json.loads((self.p/'status.json').read_text());self.assertEqual(s['upstream_connections'],1);self.assertEqual(s['subscriptions_acknowledged'],2);self.assertTrue(s['raw_recorder_is_separate_connection']);self.assertFalse(s['orders'])

class RefusalLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_access_denial_does_not_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            attempts=[]
            async def deny(url):attempts.append(url);raise w.HandshakeError(403)
            relay=w.Relay('wss://fixture.invalid',['P'],d,connect=deny);t=asyncio.create_task(relay.serve());await until(lambda:relay.state=='ACCESS_DENIED');await asyncio.sleep(.15);self.assertEqual(len(attempts),1);relay.stop.set();await asyncio.wait_for(t,2)
    async def test_rate_limit_cooldown_shared_not_per_sensor(self):
        with tempfile.TemporaryDirectory() as d:
            attempts=[]
            async def deny(url):attempts.append(url);raise w.HandshakeError(429,{'retry-after':'90'})
            relay=w.Relay('wss://fixture.invalid',['P1','P2'],d,connect=deny);t=asyncio.create_task(relay.serve());await until(lambda:relay.state=='COOLDOWN');self.assertGreater(relay.cooldown_until-w.now_ms(),88000);await asyncio.sleep(.2);self.assertEqual(len(attempts),1);relay.stop.set();await asyncio.wait_for(t,2)


class NonfiniteRetryAfterTests(unittest.TestCase):
    def test_nonfinite_retry_after_cannot_poison_deadline(self):
        for value in ('inf', '-inf', 'nan', 'NaN', '1e9999'):
            self.assertEqual(w.retry_after({'retry-after':value}), 0)
    def test_nonfinite_429_uses_bounded_backoff_not_zero_delay(self):
        delay=w.retry_delay(1,429,{'retry-after':'inf'})
        self.assertGreaterEqual(delay,30)
        self.assertLess(delay,31)
