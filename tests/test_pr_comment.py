"""The Action's sticky pull request comment, against a local fake of the API."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pr_comment", ROOT / "scripts" / "pr_comment.py")
assert _spec is not None and _spec.loader is not None
pr_comment = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_comment)

TOKEN = "test-token-value"
BODY = "<!-- whykit-diff root=kb -->\n### WhyKit: what changed in the decisions\n"


class FakeApi:
    def __init__(self) -> None:
        self.comments: list[dict] = []
        self.requests: list[tuple[str, str, str | None]] = []
        self.status = 200
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _reply(self, status: int, payload: object) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method: str) -> None:
                api.requests.append((method, self.path, self.headers.get("Authorization")))
                if api.status != 200:
                    self._reply(api.status, {"message": "Resource not accessible by integration"})
                    return
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length)) if length else None
                if method == "GET" and url.path == "/repos/example/vault/issues/7/comments":
                    query = parse_qs(url.query)
                    size, page = int(query["per_page"][0]), int(query["page"][0])
                    self._reply(200, api.comments[(page - 1) * size : page * size])
                elif method == "POST" and url.path == "/repos/example/vault/issues/7/comments":
                    comment = {"id": 1000 + len(api.comments), "body": payload["body"], "user": {"type": "Bot"}}
                    api.comments.append(comment)
                    self._reply(201, comment)
                elif method == "PATCH" and url.path.startswith("/repos/example/vault/issues/comments/"):
                    target = int(url.path.rsplit("/", 1)[1])
                    comment = next(item for item in api.comments if item["id"] == target)
                    comment["body"] = payload["body"]
                    self._reply(200, comment)
                else:
                    self._reply(404, {"message": "Not Found"})

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def do_PATCH(self) -> None:  # noqa: N802
                self._handle("PATCH")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class PullRequestCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeApi()
        self._tmp = tempfile.TemporaryDirectory()
        self.body = Path(self._tmp.name) / "body.md"
        self.body.write_text(BODY, encoding="utf-8")

    def tearDown(self) -> None:
        self.api.close()
        self._tmp.cleanup()

    def run_script(self, *, api_url: str | None = None, token: str = TOKEN) -> tuple[int, str]:
        env = {**os.environ, "GITHUB_API_URL": api_url or self.api.url, "GITHUB_TOKEN": token}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            code = pr_comment.main(["--body", str(self.body), "--repository", "example/vault", "--pull", "7"])
        return code, out.getvalue()

    def test_creates_one_comment_then_updates_it(self) -> None:
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.api.comments), 1)
        self.body.write_text(BODY + "\nsecond push\n", encoding="utf-8")
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.api.comments), 1)
        self.assertIn("second push", self.api.comments[0]["body"])
        self.assertIn("updated comment", out)
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, _, auth in self.api.requests))
        self.assertNotIn(TOKEN, out)

    def test_finds_its_comment_on_a_later_page(self) -> None:
        self.api.comments = [{"id": n, "body": "looks good", "user": {"type": "User"}} for n in range(150)]
        self.api.comments.append({"id": 999, "body": BODY, "user": {"type": "Bot"}})
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn("updated comment 999", out)
        self.assertEqual(len(self.api.comments), 151)

    def test_never_edits_a_person_quoting_the_marker_or_another_vault(self) -> None:
        self.api.comments = [
            {"id": 1, "body": BODY + "\nquoted by a person", "user": {"type": "User"}},
            {"id": 2, "body": "<!-- whykit-diff root=other -->\nother vault", "user": {"type": "Bot"}},
        ]
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.api.comments), 3)
        self.assertEqual(self.api.comments[0]["body"], BODY + "\nquoted by a person")
        self.assertEqual(self.api.comments[1]["body"], "<!-- whykit-diff root=other -->\nother vault")

    def test_a_refused_write_is_a_warning_not_a_failure(self) -> None:
        self.api.status = 403
        code, out = self.run_script()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("::warning::"), out)
        self.assertIn("pull-requests: write", out)
        self.assertNotIn(TOKEN, out)

    def test_refuses_to_send_the_token_over_plain_http_to_another_host(self) -> None:
        code, out = self.run_script(api_url="http://api.example.invalid")
        self.assertEqual(code, 0)
        self.assertIn("non-HTTPS", out)
        self.assertEqual(self.api.requests, [])

    def test_rejects_a_body_without_the_marker_and_bad_arguments(self) -> None:
        self.body.write_text("### no marker\n", encoding="utf-8")
        code, out = self.run_script()
        self.assertEqual(code, 2)
        self.assertEqual(self.api.comments, [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                pr_comment.main(["--body", str(self.body), "--repository", "../x", "--pull", "7"]), 2,
            )
            self.assertEqual(
                pr_comment.main(["--body", str(self.body), "--repository", "example/vault", "--pull", "7;"]), 2,
            )

    def test_without_a_token_nothing_is_sent(self) -> None:
        code, out = self.run_script(token="")
        self.assertEqual(code, 0)
        self.assertIn("no token", out)
        self.assertEqual(self.api.requests, [])


if __name__ == "__main__":
    unittest.main()
