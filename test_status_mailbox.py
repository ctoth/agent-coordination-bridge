"""Synthetic late-reply tests; no live session or transcript is mutated."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import claude_bridge as bridge
from status_mailbox import Mailbox, claude_request
from test_claude_bridge import FakeStdout, TARGET


CID = 'codex-status-' + 'a' * 32  # the older ID form must still be accepted
SENDER = 'agent-bridge-' + 'a' * 8


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'target.jsonl'
        self.log.write_bytes(b'')
        self.path = self.root / 'mailbox.sqlite3'
        self.box = Mailbox(self.path)
        self.addCleanup(self.box.close)

    def append(self, role, content, session=None):
        row = dict(type=role, sessionId=session or TARGET['sessionId'], message=dict(content=content))
        with self.log.open('ab') as out:
            out.write(json.dumps(row).encode() + b'\n')

    def receipt(self):
        self.append('user', 'Another Claude session sent a message:\n'
                    f'<agent-message from="{SENDER}">{CID}</agent-message>')

    def reply(self, cid=CID, sender=SENDER, session=None):
        self.append('assistant', [dict(type='tool_use', name='SendMessage', id='late-reply',
                    input=dict(to=sender, message=cid + ': Waiting for review.'))], session)

    def claim(self):
        self.box.claim(CID, claude_request(TARGET, SENDER, self.log, self.log.stat().st_size, 'Status?'))

    def test_sender_timeout_then_fresh_collector_recovers_late_failed_reply(self):
        child = types.SimpleNamespace(stdout=FakeStdout(), closed=False)
        def close():
            child.closed = True
        child.stdin = types.SimpleNamespace(write=lambda _: None, flush=lambda: None, close=close)
        child.wait = lambda timeout: 0
        with patch.object(bridge, 'transcript', return_value=self.log), \
                patch.object(bridge, 'inbox_identities', return_value=set()), \
                patch.object(bridge.subprocess, 'Popen', return_value=child) as start, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(bridge.BridgeError, 'Timed out'):
                bridge.ask('claude.exe', TARGET, 0, self.box, CID)
            self.assertTrue(child.closed)
            self.receipt()
            self.reply()
            self.append('user', [dict(type='tool_result', tool_use_id='late-reply',
                        content='{"success":false,"message":"No agent reachable"}')])
            with self.assertRaisesRegex(ValueError, 'already claimed'):
                bridge.ask('claude.exe', TARGET, 0, self.box, CID)
            self.assertEqual(start.call_count, 1)
        fresh = Mailbox(self.path)
        try:
            result = fresh.collect(CID)
            self.assertEqual(result['outcome'], 'reply_collected')
            self.assertEqual(result['source'], 'target_reply_attempt')
            self.assertFalse(result['live_delivery_confirmed'])
            self.assertEqual(result['sender_session'], TARGET['sessionId'])
            self.assertEqual(result['recipient'], SENDER)
            self.assertEqual(fresh.collect(CID), result)
            self.assertTrue(fresh.acknowledge(CID)['acknowledged'])
            self.log.unlink()
            self.assertTrue(fresh.acknowledge(CID)['acknowledged'])
        finally:
            fresh.close()

    def test_exact_identity_receipt_and_correlation_required(self):
        self.claim()
        self.reply()
        self.receipt()
        self.reply(cid=CID + '0')
        self.reply(sender='unrelated-sender')
        self.reply(session='22222222-2222-4222-8222-222222222222')
        self.assertEqual(self.box.collect(CID)['outcome'], 'pending')

    def test_premature_sender_eof_then_late_status_record(self):
        child = types.SimpleNamespace(stdout=FakeStdout(), closed=False)
        child.stdout.lines.put(None)
        def close():
            child.closed = True
        child.stdin = types.SimpleNamespace(write=lambda _: None, flush=lambda: None, close=close)
        child.wait = lambda timeout: 0
        with patch.object(bridge, 'transcript', return_value=self.log), \
                patch.object(bridge, 'inbox_identities', return_value=set()), \
                patch.object(bridge.subprocess, 'Popen', return_value=child), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(bridge.BridgeError, 'Messenger exited'):
                bridge.ask('claude.exe', TARGET, 1, self.box, CID)
        self.assertTrue(child.closed)
        self.receipt()
        self.append('assistant', [dict(type='text', text='STATUS-REPLY ' + CID + '\nStill working.')])
        fresh = Mailbox(self.path)
        try:
            self.assertEqual(fresh.collect(CID)['outcome'], 'reply_collected')
        finally:
            fresh.close()

    def test_fallback_status_survives_sender_exit(self):
        self.claim()
        self.receipt()
        self.append('assistant', [dict(type='text', text='AGENT-REPLY ' + CID + '\nBlocked on review.')])
        self.assertEqual(self.box.collect(CID)['source'], 'target_reply_record')

    def test_marker_without_answer_or_for_longer_id_is_not_a_reply(self):
        self.claim()
        self.receipt()
        self.append('assistant', [dict(type='text', text='AGENT-REPLY ' + CID + '\n')])
        self.append('assistant', [dict(type='text', text='AGENT-REPLY ' + CID + '0\nOther.')])
        self.assertEqual(self.box.collect(CID)['outcome'], 'pending')

    def test_historical_reply_and_rewritten_transcript_refused(self):
        self.receipt()
        self.reply()
        self.claim()
        self.assertEqual(self.box.collect(CID)['outcome'], 'pending')
        self.log.write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError, 'prefix changed'):
            self.box.collect(CID)

    def test_missing_reply_ack_refused_and_partial_record_deferred(self):
        self.claim()
        with self.assertRaisesRegex(ValueError, 'missing reply'):
            self.box.acknowledge(CID)
        with self.log.open('ab') as out:
            out.write(b'{"type":')
        self.assertEqual(self.box.collect(CID)['outcome'], 'pending')

    def test_collect_cli_never_discovers_or_launches_sender(self):
        self.claim()
        with patch.object(bridge, 'executable', side_effect=AssertionError('must not discover')), \
                patch('sys.argv', ['claude_bridge.py', 'collect', '--request-id', CID, '--mailbox', str(self.path)]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main(), 0)


if __name__ == '__main__':
    unittest.main()
