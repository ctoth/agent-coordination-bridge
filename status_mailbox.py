"""Durable, local request and reply data for every provider. Collection never sends or executes messages."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import message


def claude_request(target, sender, transcript, offset, question):
    """Bind a Claude request to the target transcript exactly as it is before sending."""
    with Path(transcript).open('rb') as source:
        prefix = source.read(offset)
    if len(prefix) != offset:
        raise ValueError('Transcript changed before request; do not send')
    return dict(provider='claude', target=target, sender=sender,
                sender_ids=[sender, '@' + sender], question=question,
                transcript=str(Path(transcript).resolve()), offset=offset,
                prefix_sha256=hashlib.sha256(prefix).hexdigest())


class Mailbox:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, request TEXT NOT NULL, reply TEXT, acknowledged INTEGER NOT NULL DEFAULT 0)')
        self.db.commit()

    def close(self):
        self.db.close()

    def claim(self, correlation, request):
        """Commit the request before sending; one ID is sent at most once from this mailbox."""
        try:
            with self.db:
                self.db.execute('INSERT INTO requests(id, request) VALUES (?, ?)',
                                (correlation, json.dumps(dict(request, correlation=correlation))))
        except sqlite3.IntegrityError as exc:
            raise ValueError('Request already claimed; collect it, never resend') from exc

    def request(self, correlation):
        row = self.db.execute('SELECT request FROM requests WHERE id=?', (correlation,)).fetchone()
        if row is None:
            raise ValueError('Unknown request ID')
        return json.loads(row[0])

    def record(self, correlation, **fields):
        """Add facts learned after the claim, such as a sender address or the turn sent to."""
        request = dict(self.request(correlation), **fields)
        with self.db:
            self.db.execute('UPDATE requests SET request=? WHERE id=?', (json.dumps(request), correlation))

    def sender_identities(self, correlation, identities):
        self.record(correlation, sender_ids=sorted(set(self.request(correlation)['sender_ids']) | set(identities)))

    def store_reply(self, correlation, reply):
        """Keep the first reply found; later finds never replace it."""
        request = self.request(correlation)
        reply = dict(reply, correlation=correlation, provider=request['provider'], outcome='reply_collected')
        with self.db:
            self.db.execute('UPDATE requests SET reply=? WHERE id=? AND reply IS NULL',
                            (json.dumps(reply), correlation))

    def collect(self, correlation):
        # A persisted reply remains available even after the provider removes its transcript.
        row = self.db.execute('SELECT reply, acknowledged FROM requests WHERE id=?', (correlation,)).fetchone()
        if row is None:
            raise ValueError('Unknown request ID')
        if row[0]:
            return dict(json.loads(row[0]), acknowledged=bool(row[1]))
        request = self.request(correlation)
        candidate = self.claude_reply(request) if request['provider'] == 'claude' else None
        if candidate is None:
            return dict(correlation=correlation, provider=request['provider'], outcome='pending', acknowledged=False)
        self.store_reply(correlation, candidate)
        return self.collect(correlation)

    def claude_reply(self, request):
        correlation = request['correlation']
        from claude_bridge import Evidence, message_content, text_content
        evidence = Evidence(correlation, request['sender'], request['target']['name'])
        evidence.sender_ids.update(request['sender_ids'])
        marker = re.compile(r'(?<![\w-])' + re.escape(correlation) + r'(?![\w-])')
        with Path(request['transcript']).open('rb') as source:
            prefix = source.read(request['offset'])
            if hashlib.sha256(prefix).hexdigest() != request['prefix_sha256']:
                raise ValueError('Transcript prefix changed; refusing collection')
            for line in source:
                if not line.endswith(b'\n'):
                    break
                record = json.loads(line)
                if record.get('sessionId') != request['target']['sessionId']:
                    continue
                content = message_content(record)
                origin = record.get('attachment', {}).get('origin', {})
                if marker.search(text_content(content)) or marker.search(str(origin.get('body', ''))):
                    evidence.target(record)
                if not evidence.receipt or record.get('type') != 'assistant':
                    continue
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    inp = block.get('input', {})
                    body = inp.get('message', '')
                    if (block.get('type') == 'tool_use' and block.get('name') == 'SendMessage'
                            and inp.get('to') in evidence.sender_ids and isinstance(body, str)
                            and marker.search(body)):
                        return self.claude_found(request, body, 'target_reply_attempt')
                    body = text_content([block])
                    if block.get('type') == 'text' and message.reply(body, correlation):
                        return self.claude_found(request, body, 'target_reply_record')
        return None

    @staticmethod
    def claude_found(request, text, source):
        return dict(text=text, source=source, sender_session=request['target']['sessionId'],
                    recipient=request['sender'], live_delivery_confirmed=False)

    def acknowledge(self, correlation):
        with self.db:
            changed = self.db.execute('UPDATE requests SET acknowledged=1 WHERE id=? AND reply IS NOT NULL',
                                      (correlation,)).rowcount
        if not changed:
            raise ValueError('Cannot acknowledge a missing reply')
        return self.collect(correlation)
