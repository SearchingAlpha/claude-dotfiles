"""redact.py through its real entry point."""

import json
import unittest

from support import B64, FAKE, Sandbox, bash_out, hexs, pem, post, read_out, rnd


class TestRedact(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def redact(self, payload):
        rc, out, err = self.sb.run("redact.py", payload)
        self.assertEqual(rc, 0)
        return json.loads(out) if out else None

    def test_bash_output_keeps_its_shape(self):
        k = FAKE.anthropic
        resp = bash_out(f"loaded config\nkey={k}\n", stderr="warning: none")
        o = self.redact(post("Bash", {"command": "cat cfg"}, resp))["hookSpecificOutput"]
        new = o["updatedToolOutput"]
        self.assertEqual(set(new), set(resp))
        self.assertEqual(new["stdout"], "loaded config\nkey=[REDACTED:anthropic-api-key]\n")
        self.assertEqual(new["stderr"], "warning: none")
        self.assertIs(new["interrupted"], False)
        self.assertNotIn(k, json.dumps(o))
        self.assertIn("1 x anthropic-api-key", o["additionalContext"])

    def test_read_and_grep_shapes(self):
        k = FAKE.github
        o = self.redact(post("Read", {"file_path": "/r/.env"}, read_out("/r/.env", f"GH={k}\nX=1\n")))
        f = o["hookSpecificOutput"]["updatedToolOutput"]["file"]
        self.assertEqual((f["filePath"], f["numLines"]), ("/r/.env", 3))
        self.assertEqual(f["content"], "GH=[REDACTED:github-token]\nX=1\n")
        grep = {"mode": "content", "numFiles": 0, "filenames": [],
                "content": f"cfg.py:3:TOKEN = '{k}'", "numLines": 1}
        o = self.redact(post("Grep", {"pattern": "TOKEN"}, grep))
        self.assertEqual(o["hookSpecificOutput"]["updatedToolOutput"]["content"],
                         "cfg.py:3:TOKEN = '[REDACTED:github-token]'")

    def test_a_private_key_disappears_whole_and_lines_stay_aligned(self):
        key, body = pem("PRIVATE KEY", 64, 26)
        content = f"# deploy key\n{key}\nnext = 1\n"
        o = self.redact(post("Read", {"file_path": "/r/k.pem"}, read_out("/r/k.pem", content)))
        new = o["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
        self.assertEqual([ln for ln in body if ln in new], [])
        self.assertEqual(new.split("\n")[1], "[REDACTED:private-key]")
        self.assertEqual(new.count("\n"), content.count("\n"))
        self.assertTrue(new.endswith("next = 1\n"))

    def test_mcp_nested_content(self):
        k = FAKE.aws_id
        resp = [{"type": "text", "text": json.dumps({"rows": [{"id": 1, "note": f"key {k}"}]})}]
        o = self.redact(post("mcp__db__query", {"sql": "select 1"}, resp))
        new = o["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(new[0]["type"], "text")
        self.assertIn("[REDACTED:aws-access-key-id]", new[0]["text"])
        self.assertNotIn(k, new[0]["text"])

    def test_nothing_found_prints_nothing(self):
        self.assertIsNone(self.redact(post("Bash", {"command": "ls"}, bash_out("a\nb\n"))))
        self.assertIsNone(self.redact(post("Read", {"file_path": "/x"}, read_out("/x", "sha " + "f" * 64))))

    def test_binary_payloads_and_paths_are_left_alone(self):
        k = FAKE.anthropic
        img = {"type": "image", "file": {"base64": rnd(600, "ABCDEFGHabcdefgh0123456789+/") + k,
                                         "type": "image/png"}}
        self.assertIsNone(self.redact(post("Read", {"file_path": "/i.png"}, img)))
        self.assertIsNone(self.redact(post("Bash", {}, {"filePath": f"/tmp/{k}", "stdout": ""})))

    def test_long_strings_under_blob_keys_are_scanned_unless_base64(self):
        k = FAKE.github
        minified = json.dumps({"rows": [{"id": i, "v": rnd(20)} for i in range(20)] + [{"t": k}]},
                              separators=(",", ":"))
        url = ("https://bucket.s3.example.com/r.csv?X-Amz-Algorithm=AWS4-HMAC-SHA256"
               "&X-Amz-Security-Token=" + rnd(600, B64[:-2]) + "&X-Amz-Signature=" + hexs(64))
        for key, value, kind in (("data", minified, "github-token"), ("source", url, "high-entropy-secret")):
            with self.subTest(key):
                self.assertGreater(len(value), 512)
                o = self.redact(post("mcp__s__get", {}, {"structuredContent": {key: value}}))
                new = o["hookSpecificOutput"]["updatedToolOutput"]["structuredContent"][key]
                self.assertIn(f"[REDACTED:{kind}]", new)
        uri = "data:image/png;base64," + rnd(700, B64) + k
        self.assertIsNone(self.redact(post("mcp__s__img", {}, [{"type": "image", "data": uri}])))

    def test_the_allow_marker_does_not_apply_to_output(self):
        k = FAKE.github
        line = json.dumps({"msg": "test guards:allow-secret", "token": k})
        for payload in (post("Bash", {"command": "cat x.jsonl"}, bash_out(line + "\n")),
                        post("Read", {"file_path": "/x.jsonl"}, read_out("/x.jsonl", line))):
            with self.subTest(payload["tool_name"]):
                o = self.redact(payload)
                self.assertNotIn(k, json.dumps(o["hookSpecificOutput"]["updatedToolOutput"]))

    def test_a_foreground_subagents_answer(self):
        k = FAKE.jwt
        resp = {"status": "completed", "prompt": "p", "agentId": "a4d2c8f1e0b3a297",     # 2.1.295 shape
                "agentType": "general-purpose", "harnessNoteCount": 0, "harnessTailCount": 0,
                "harnessSectionHash": "5d063bfd2801fd58", "resolvedModel": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": f"The token is {k}."}], "totalTokens": 12450,
                "totalDurationMs": 4821, "totalToolUseCount": 1,
                "usage": {"input_tokens": 8320, "output_tokens": 40}}
        o = self.redact(post("Agent", {"prompt": "p", "description": "d"}, resp))
        new = o["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(set(new), set(resp))
        self.assertEqual(new["content"], [{"type": "text", "text": "The token is [REDACTED:jwt]."}])
        self.assertEqual(new["usage"], resp["usage"])

    def test_failure_adds_a_warning_only(self):
        k = FAKE.anthropic
        payload = {"session_id": "s", "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                   "tool_input": {"command": "deploy"}, "error": f"Exit code 1\nbad key {k}",
                   "is_interrupt": False}
        o = self.redact(payload)["hookSpecificOutput"]
        self.assertEqual(o["hookEventName"], "PostToolUseFailure")
        self.assertNotIn("updatedToolOutput", o)
        self.assertIn("can't redact", o["additionalContext"])
        self.assertNotIn(k, o["additionalContext"])

    def test_garbage_and_disable(self):
        for payload in ("", "nope", "[]", '{"hook_event_name": "PostToolUse"}'):
            rc, out, _ = self.sb.run("redact.py", payload)
            self.assertEqual((rc, out), (0, ""))
        self.sb.config({"redact": False})
        self.assertIsNone(self.redact(post("Bash", {}, bash_out(FAKE.github))))


if __name__ == "__main__":
    unittest.main()
