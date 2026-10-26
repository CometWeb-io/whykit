"""Large graph exports preserve bytes without holding another full JSON string."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from synthetic_vault import generate
from whykit import console, graph


class HashSink(io.TextIOBase):
    def __init__(self):
        self.digest = hashlib.sha256()
        self.largest_write = 0

    def write(self, text):
        self.largest_write = max(self.largest_write, len(text))
        self.digest.update(text.encode('utf-8', 'surrogateescape'))
        return len(text)


class StreamedGraphTests(unittest.TestCase):
    def test_json_and_obsidian_stdout_preserve_bytes_without_whole_text_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = generate(Path(tmp) / 'vault', 300)
            expected_graph = graph.build_graph(root)
            for format_name, payload in (('json', expected_graph), ('obsidian', graph.as_obsidian(expected_graph))):
                expected = hashlib.sha256((json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode()).digest()
                sink = HashSink()
                with contextlib.redirect_stdout(sink):
                    code = graph.main(['--root', str(root), '--format', format_name])
                self.assertEqual(code, 0)
                self.assertEqual(sink.digest.digest(), expected)
                self.assertLess(sink.largest_write, 4096, 'export materialized a whole JSON document')

    def test_file_export_preserves_crlf_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = generate(Path(tmp) / 'vault', 100)
            output = root / 'graph.json'
            output.write_bytes(b'old\r\n')
            if os.name != 'nt':
                output.chmod(0o640)
            payload = graph.build_graph(root)
            expected = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').replace('\n', '\r\n').encode()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(graph.main(['--root', str(root), '--output', 'graph.json']), 0)
            self.assertEqual(output.read_bytes(), expected)
            if os.name != 'nt':
                self.assertEqual(output.stat().st_mode & 0o777, 0o640)

    def test_serialization_interruption_preserves_old_file_and_cleans_temporary(self):
        from whykit import io as durability
        writer = getattr(durability, 'atomic_write_json', None)
        self.assertIsNotNone(writer, 'streamed atomic JSON writer is missing')
        def interrupted(*_args, **_kwargs):
            yield '{\n'
            raise KeyboardInterrupt('injected interruption')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'graph.json'
            path.write_bytes(b'old\r\n')
            with patch('json.JSONEncoder.iterencode', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                writer(path, {'answer': 1})
            self.assertEqual(path.read_bytes(), b'old\r\n')
            self.assertEqual(list(path.parent.glob('.graph.json.whykit-tmp-*')), [])


class StreamedConsoleTests(unittest.TestCase):
    def test_ascii_console_uses_utf8_and_restores_encoding_on_success_and_failure(self):
        emit = getattr(console, 'emit_json', None)
        self.assertIsNotNone(emit, 'streamed machine JSON writer is missing')
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='ascii', errors='strict', newline='\r\n')
        payload = {'value': 'Łódź 😀', 'escaped': '\n"\\'}
        emit(payload, file=stream)
        stream.flush()
        expected = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').replace('\n', '\r\n').encode()
        self.assertEqual(raw.getvalue(), expected)
        self.assertEqual((stream.encoding, stream.errors), ('ascii', 'strict'))
        with self.assertRaises(TypeError):
            emit({'bad': object()}, file=stream)
        self.assertEqual((stream.encoding, stream.errors), ('ascii', 'strict'))


if __name__ == '__main__':
    unittest.main()
