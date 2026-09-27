"""A5 process-isolation tests built for the 16Q runtime.

These tests do not rewrite A2/A4 fixtures. They exercise A5 PAPER/UI processes
through a deterministic local IPC peer and fail fast with child logs.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from a5.common import (
    ROOT, atomic, read_json, now_ms, DURATION_MS, terminate,
    command, clean_env, heartbeat, digest,
)
from a5.catalog import initial, freeze
from a5.replay import replay
from a5.supervisor import Supervisor, package_result


def wait(fn, seconds=10, process=None, log=None, label='condition'):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=fn()
        if value:
            return value
        if process is not None and process.poll() is not None:
            details=''
            if log is not None and Path(log).exists():
                details=Path(log).read_text(errors='replace')[-12000:]
            raise AssertionError(
                'PROCESS_EXITED_BEFORE_'+label+
                ' rc='+str(process.returncode)+'\n'+details
            )
        time.sleep(.04)
    details=''
    if log is not None and Path(log).exists():
        details=Path(log).read_text(errors='replace')[-12000:]
    raise AssertionError('TIMEOUT_WAITING_FOR_'+label+'\n'+details)


class ProcessIsolation(unittest.TestCase):
    def setUp(self):
        self.previous=os.environ.get('BELIAL_A1_NO_BROWSER')
        os.environ['BELIAL_A1_NO_BROWSER']='1'

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('BELIAL_A1_NO_BROWSER',None)
        else:
            os.environ['BELIAL_A1_NO_BROWSER']=self.previous

    def test_paper_continues_across_ui_restart_and_blocked_spot_signal(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/'a5-process-fixture'
            session.mkdir()
            for folder in ('shadow_compare','shadow_focus'):
                path=session/'aggregate'/folder
                path.mkdir(parents=True)
                (path/'events.jsonl').touch()
            atomic(session/'SESSION.json',dict(
                phase='PREPARING',
                duration_ms=DURATION_MS,
            ))
            atomic(session/'INTEGRITY.json',{'ok':True})

            procs=[]
            logs=[]

            def launch(argv,name):
                log_path=session/name
                handle=log_path.open('w')
                logs.append(handle)
                proc=subprocess.Popen(
                    argv,
                    stdout=handle,
                    stderr=handle,
                    cwd=ROOT,
                    env=clean_env(),
                    start_new_session=True,
                )
                procs.append(proc)
                return proc,log_path

            paper,paper_log=launch(
                [sys.executable,'-B',str(ROOT/'tests_a5/paper_harness_a5.py'),str(session)],
                'paper-test.log',
            )
            ui=None
            ui2=None
            try:
                wait(
                    lambda: read_json(session/'paper/status.json',{}).get('metadata_finished'),
                    process=paper,
                    log=paper_log,
                    label='PAPER_METADATA',
                )

                first_books=wait(
                    lambda: (
                        read_json(session/'paper/books.json',{})
                        if read_json(session/'paper/books.json',{}).get('ingest_messages',0)>=8
                        else None
                    ),
                    process=paper,
                    log=paper_log,
                    label='PAPER_BOOKS',
                )
                self.assertIn('hyperliquid',first_books.get('venue_runtime',{}))
                self.assertIn('lighter',first_books.get('venue_runtime',{}))

                ui,ui_log=launch(
                    command('ui_worker','--session',session),
                    'ui-test.log',
                )
                url=wait(
                    lambda: read_json(session/'ui/URL.json',{}).get('url'),
                    process=ui,
                    log=ui_log,
                    label='UI_URL',
                )
                with urllib.request.urlopen(url+'health',timeout=2) as response:
                    health=json.load(response)
                self.assertTrue(health['ok'])
                self.assertTrue(health['read_only'])
                self.assertFalse(health['orders'])

                before=read_json(session/'paper/books.json',{}).get('ingest_messages',0)
                terminate(ui,3)
                wait(
                    lambda: read_json(session/'paper/books.json',{}).get('ingest_messages',0)>before+10,
                    process=paper,
                    log=paper_log,
                    label='PAPER_AFTER_UI_STOP',
                )
                self.assertIsNone(paper.poll())

                ui2,ui2_log=launch(
                    command('ui_worker','--session',session),
                    'ui-restart.log',
                )
                restarted=wait(
                    lambda: (
                        read_json(session/'ui/URL.json',{})
                        if read_json(session/'ui/URL.json',{}).get('pid')==ui2.pid
                        else None
                    ),
                    process=ui2,
                    log=ui2_log,
                    label='UI_RESTART',
                )
                with urllib.request.urlopen(restarted['url']+'health',timeout=2) as response:
                    self.assertTrue(json.load(response)['ok'])

                caps=initial()
                caps['FARTCOIN'].update(
                    qualified=True,state='QUALIFIED',
                    identity=True,price=True,actor=True,
                )
                caps['PUMP'].update(
                    state='RAW_ONLY',qualified=False,
                    reason='TransferHook',
                )
                t=now_ms()
                atomic(session/'COHORT.json',freeze(caps,t))
                heartbeat(session/'aggregate/status.json','ACTIVE')
                heartbeat(
                    session/'sources/FARTCOIN/status.json',
                    'ACTIVE',
                    generation=0,
                    integrity=True,
                    actor_ready=True,
                    actor_epoch=0,
                    ready_since_ms=t,
                )
                heartbeat(
                    session/'sources/PUMP/status.json',
                    'QUARANTINED',
                    generation=0,
                    integrity=False,
                    reason='TransferHook',
                )
                atomic(session/'SESSION.json',dict(
                    phase='CAPTURING',
                    duration_ms=DURATION_MS,
                    started_ms=t,
                    deadline_ms=t+DURATION_MS,
                    deadline_monotonic_ns=time.monotonic_ns()+8_000_000_000,
                ))

                wait(
                    lambda: read_json(session/'paper/state.json',{}).get('started_ms'),
                    process=paper,
                    log=paper_log,
                    label='PAPER_CAPTURE_START',
                )

                event=dict(
                    kind='EPISODE_OPEN',
                    episode_id='PUMP-A5-FIXTURE',
                    symbol='PUMP',
                    at_unix_ms=now_ms(),
                    paper_only=True,
                    orders=False,
                )
                with (session/'aggregate/shadow_compare/events.jsonl').open('a') as handle:
                    handle.write(json.dumps(event,separators=(',',':'))+'\n')

                wait(
                    lambda: read_json(session/'paper/state.json',{}).get('a1_blocked_signals',0)>0,
                    process=paper,
                    log=paper_log,
                    label='BLOCKED_SIGNAL',
                )
                self.assertFalse(
                    read_json(session/'paper/state.json')['groups']['CORE12']['positions']
                )

                state=read_json(session/'SESSION.json')
                state['phase']='STOPPING'
                atomic(session/'SESSION.json',state)

                paper.wait(timeout=12)
                self.assertEqual(
                    paper.returncode,0,
                    paper_log.read_text(errors='replace')[-12000:],
                )
                self.assertTrue(read_json(session/'paper/COMPLETE.json')['ok'])

                result=replay(
                    session/'paper/inputs.jsonl',
                    session/'replay',
                )
                self.assertEqual(
                    result['output_sha256'],
                    digest(session/'paper/outputs.jsonl'),
                )
            finally:
                for proc in procs:
                    terminate(proc,3)
                for handle in logs:
                    handle.close()

    def test_supervisor_ui_restart_requires_no_pool_approval(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)
            sup=Supervisor(session,base=session)
            try:
                sup.setup()
                wait(
                    lambda: read_json(session/'ui/URL.json',{}),
                    process=sup.procs['ui'],
                    log=session/'logs/ui.log',
                    label='SUPERVISOR_UI_URL',
                )
                first=sup.procs['ui'].pid
                terminate(sup.procs['ui'],3)
                platform=sup.publish()
                self.assertEqual(platform['coverage']['qualified_at_start'],0)
                self.assertEqual(platform['coverage']['requested'],16)
                self.assertNotEqual(sup.procs['ui'].pid,first)
                wait(
                    lambda: read_json(session/'ui/URL.json',{}).get('pid')==sup.procs['ui'].pid,
                    process=sup.procs['ui'],
                    log=session/'logs/ui.log',
                    label='SUPERVISOR_UI_RESTART',
                )
            finally:
                for proc in sup.procs.values():
                    terminate(proc,3)
                for handle in sup.logs.values():
                    handle.close()

    def test_archive_inventories_exported_bytes(self):
        import hashlib
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/'a5-archive-test'
            session.mkdir()
            atomic(session/'a.json',{'orders':False})
            zpath=package_result(session,Path(td)/'out')
            with zipfile.ZipFile(zpath) as archive:
                inventory=json.loads(archive.read('ZIP_SHA256.json'))
                for name,expected in inventory.items():
                    self.assertEqual(
                        hashlib.sha256(archive.read(name)).hexdigest(),
                        expected,
                    )


if __name__=='__main__':
    unittest.main()
