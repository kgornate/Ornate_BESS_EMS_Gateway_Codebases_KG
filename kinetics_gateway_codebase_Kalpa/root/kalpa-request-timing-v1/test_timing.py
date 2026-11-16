import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch as mock
import install
import timing_probe as t
import report

class TimingTests(unittest.TestCase):
    def test_event_phases_and_failure(self):
        span=t.NetworkSpan()
        span.events=[('connection.connect_tcp.started',0),('connection.connect_tcp.complete',50),
            ('http11.send_request_body.started',60),('http11.send_request_body.complete',80),
            ('http11.receive_response_headers.started',80),('http11.receive_response_headers.failed',1080)]
        row=span.result(SimpleNamespace(status_code=0,exception='ReadTimeout: '))
        self.assertEqual(row['connect_tcp_ms'],50)
        self.assertEqual(row['send_body_ms'],20)
        self.assertEqual(row['wait_headers_ms'],1000)
        self.assertIsNone(row['tls_ms'])
        self.assertEqual(row['error_type'],'ReadTimeout')
    def test_trace_never_reads_sensitive_info(self):
        class Secret:
            def __getattribute__(self,name):raise AssertionError('info read')
        span=t.NetworkSpan();asyncio.run(span.trace('http11.send_request_body.started',Secret()))
        self.assertEqual(len(span.events),1)
    def test_prepare_preserves_frozen_dataclass_fields(self):
        @dataclass(frozen=True)
        class Batch:request_id:str='request'
        @t.timed_prepare
        def prepare():return Batch()
        with mock.object(t,'DEADLINE',float('inf')):
            row=prepare()
        self.assertEqual(row.request_id,'request')
        self.assertGreaterEqual(row._kalpa_prepare_ms,0)
        self.assertNotIn('_kalpa_prepare_ms',row.__dataclass_fields__)
    def test_batch_result_survives_observer_write_failure(self):
        saved=[]
        async def action(self,prepared):return 'upload-result'
        obj=SimpleNamespace(outbox=SimpleNamespace(path='live.db'))
        prepared=SimpleNamespace(request_id='request',json_bytes=b'{}',items=[])
        with mock.object(t,'DEADLINE',float('inf')),mock.object(t,'store',side_effect=OSError('observer disk error')):
            result=asyncio.run(t.timed_batch(action)(obj,prepared))
        self.assertEqual(result,'upload-result')
        self.assertEqual(obj._kalpa_timing_write_failures,1)
        self.assertIsNone(t.CURRENT.get())
    def test_network_and_ack_contexts(self):
        rows=[]
        @t.timed_network
        async def network():
            await t.trace_callback('http11.receive_response_headers.started',{})
            await t.trace_callback('http11.receive_response_headers.complete',{})
            return SimpleNamespace(status_code=200,exception=None)
        @t.measured_phase('ack_apply_ms')
        async def ack():return 3
        async def batch(self,prepared):
            t.lock_started();t.lock_acquired();await network();return await ack()
        obj=SimpleNamespace(outbox=SimpleNamespace(path='live.db'))
        prepared=SimpleNamespace(request_id='request',json_bytes=b'{}',items=[])
        with mock.object(t,'DEADLINE',float('inf')),mock.object(t,'store',side_effect=lambda r:rows.append(r.copy())):
            self.assertEqual(asyncio.run(t.timed_batch(batch)(obj,prepared)),3)
        self.assertEqual(rows[0]['network_attempts'][0]['http_status'],200)
        self.assertGreaterEqual(rows[0]['ack_apply_ms'],0)
        self.assertGreaterEqual(rows[0]['lock_wait_ms'],0)
        self.assertIsNone(t.NETWORK.get())
    def test_timing_database_contains_only_whitelisted_row(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'timing.db';t.store({'request_id':'r','logical_bytes':12},p)
            with sqlite3.connect(p) as db:
                text=db.execute('SELECT data FROM request_timing').fetchone()[0]
            self.assertIn('logical_bytes',text)
            self.assertNotIn('Authorization',text)
    def test_source_patch_preserves_existing_membership_trace(self):
        base=Path(__file__).resolve().parents[2]/'dashboard-update/gateway'
        if not base.exists():self.skipTest('Gateway baseline not bundled')
        for name in ('client.py','batcher.py','uploader.py'):
            original=(base/name).read_text()
            if name=='uploader.py':
                original=original.replace('        self.counters.last_http_status = response.status_code\n','        self.counters.last_http_status = response.status_code\n        # existing batch membership trace\n')
            updated=install.patch(name,original)
            self.assertEqual(install.patch(name,updated),updated)
            if name=='uploader.py':self.assertIn('# existing batch membership trace',updated)
            compile(updated,name,'exec')
        with self.assertRaises(RuntimeError):install.patch('client.py','unexpected source')
    def test_percentiles(self):
        values=list(range(1,101));s=report.stats(values)
        self.assertEqual(s['p95_ms'],95);self.assertEqual(s['mean_ms'],50.5)
if __name__=='__main__':unittest.main()
