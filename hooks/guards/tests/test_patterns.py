"""patterns.py: what counts as a secret, what never does, and how fast."""

import json
import os
import shutil
import string
import subprocess
import tempfile
import time
import unittest

from support import B64, FAKE, GUARDS, hexs, pem, rnd

import patterns as P


def kinds(text):
    return [f.kind for f in P.scan(text)]


class TestDetects(unittest.TestCase):
    def test_named_shapes(self):
        cases = {
            "anthropic-api-key": f"client = Anthropic(api_key='{FAKE.anthropic}')",
            "aws-access-key-id": f"aws_access_key_id = {FAKE.aws_id}",
            "slack-token": f"SLACK_BOT={FAKE.slack}",
            "github-token": f"git clone https://{FAKE.github}@github.com/o/r",
            "private-key": FAKE.pem,
            "jwt": f"curl -H 'X-Session: {FAKE.jwt}'",
            "stripe-key": "curl https://api.stripe.com/v1/charges -u sk" + "_live_" + rnd(24) + ":",
            "huggingface-token": "huggingface-cli login --token hf" + "_" + rnd(
                34, string.ascii_letters, check=lambda s: P._hf_token("hf_" + s)),
            "gitlab-token": "git clone https://oauth2:glpat" + "-" + rnd(20) + "@gitlab.com/x/y.git",
            "npm-token": "//registry.npmjs.org/:_authToken=npm" + "_" + rnd(36),
            "sendgrid-key": "SENDGRID=SG" + "." + rnd(22) + "." + rnd(43),
        }
        for kind, text in cases.items():
            with self.subTest(kind):
                self.assertEqual(kinds(text), [kind])

    def test_hex_key_only_in_context(self):
        h = FAKE.hex64
        self.assertEqual(kinds(f"https://serpapi.com/search?q=x&api_key={h}&engine=google"),
                         ["hex-secret"])
        self.assertEqual(kinds('{"serpapi_key": "' + h + '"}'), ["hex-secret"])
        self.assertEqual(kinds(f"OPENWEATHER_KEY={hexs(32)}"), ["hex-secret"])    # env-style name
        self.assertEqual(kinds(f"{h}  dist/app.tar.gz"), [])           # sha256sum output
        self.assertEqual(kinds(f"sha256: {h}"), [])

    def test_high_entropy_assignments(self):
        for text in (f'DB_PASSWORD="{rnd(24)}"', f"client_secret: {rnd(32)}",
                     f"export REFRESH_TOKEN={rnd(40)}", f"STORAGE_ACCOUNT_KEY={rnd(44)}",
                     f'"api_key" => "{rnd(30)}"', f"--api-key={rnd(32)}",
                     f"DB_PASSWORD={rnd(24, string.ascii_lowercase + string.digits, check=P._random_two_class)}",
                     f"aws_secret_access_key = /{rnd(39, B64[:-1])}",     # not a path: one "/"
                     '{\\"api_key\\": \\"' + rnd(40) + '\\"}'):           # JSON-escaped in a log
            with self.subTest(text[:14]):
                self.assertEqual(kinds(text), ["high-entropy-secret"])

    def test_an_outer_assignment_never_hides_an_inner_one(self):
        s, h = rnd(40), hexs(64)
        for text in (f"command: --api-key={s}", f'"args": "--token={s}"',
                     f"//registry.npmjs.org/:_authToken={s}", f"environment: API_KEY={s}",
                     f"value: SERPAPI_KEY={h}", f"params:api_key={h}", f"Env:SERPAPI_API_KEY={h}",
                     f"env: SERPAPI_API_KEY_PRODUCTION={h}"):
            with self.subTest(text[:22]):
                self.assertEqual(len(P.scan(text)), 1)

    def test_presigned_url_token_is_taken_whole(self):
        token = "%2F".join(rnd(30, B64[:-2]) for _ in range(12))
        url = ("https://bucket.s3.example.com/k?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential="
               "AS" + "IA" + rnd(16, string.ascii_uppercase) + "%2F20261008%2Feu-west-1%2Fs3%2Faws4_request"
               f"&X-Amz-Security-Token={token}&X-Amz-Signature={hexs(64)}")
        out, found = P.redact(url)
        self.assertEqual(sorted(found), ["aws-access-key-id", "high-entropy-secret"])
        self.assertTrue(all(part not in out for part in token.split("%2F")))
        self.assertIn("[REDACTED:high-entropy-secret]&X-Amz-Signature=", out)

    def test_credentials_in_urls_and_flags(self):
        pw = rnd(24)
        for text in (f"DATABASE_URL=postgres://app:{pw}@db.example.com:5432/app",
                     f"https://admin:{rnd(16)}@db.internal:5432/x",
                     f"mysql -uroot -p{rnd(20)} db", f"sshpass -p '{rnd(20)}' ssh host"):
            with self.subTest(text[:12]):
                found = P.scan(text)
                self.assertEqual(len(found), 1)
                self.assertNotIn(pw, P.redact(text)[0])
        for text in ("postgres://postgres:postgres@localhost/db", "https://user:${PASS}@host/x",
                     "redis://:password@cache:6379", "mysql -u root -p dbname",
                     "mysql -h db -p$MYSQL_PASSWORD", "mysql -pPASSWORD", "ssh://git@host:22/repo"):
            with self.subTest(text):
                self.assertEqual(kinds(text), [])

    def test_mysql_client_wherever_it_is_the_command(self):
        pw = rnd(14)
        for text in (f"/usr/bin/mysql -u root -p{pw}", f"sudo mysql -u root -p{pw} shop",
                     f"docker exec -it mysql mysql -uroot -p{pw}",
                     f"kubectl exec -it mysql-0 -- mysql -uroot -p{pw}",
                     f"sh -c 'mysql -u root -p{pw}'", f"echo ok && mariadb -u app -p'{pw}' -D app",
                     f"RUN mysql -u root -p{pw} < init.sql", f"    command: mysqladmin ping -p{pw}",
                     f"0 3 * * * mysqldump -u backup -p{pw} shop > /backup/shop.sql",
                     f"Run `mysql -u root -p{pw}` to connect.", f"mysql -u root -p{pw}; echo done"):
            with self.subTest(text[:24]):
                out, found = P.redact(text)
                self.assertEqual(found, ["cli-password"])
                self.assertNotIn(pw, out)
                self.assertEqual(out, text.replace(pw, "[REDACTED:cli-password]"))

    def test_jwt_at_any_boundary(self):
        jwt = FAKE.jwt
        for text in (jwt, f"token={jwt}", f'{{"id_token": "{jwt}"}}', f"Bearer {jwt}\n"):
            with self.subTest(text[:12]):
                self.assertEqual(kinds(text), ["jwt"])
        self.assertEqual(kinds("A" + jwt), [])         # inside a longer base64url run

    def test_several_findings_and_lines(self):
        text = f"a = 1\nkey = '{FAKE.anthropic}'\n\ntoken: {FAKE.github}\n"
        found = P.scan(text)
        self.assertEqual([P.line_of(text, f.start) for f in found], [2, 4])
        self.assertEqual(P.line_numbers(text, found), [2, 4])
        self.assertEqual(P.describe(text, found),
                         "anthropic-api-key on line 2, github-token on line 4")


class TestPrivateKeys(unittest.TestCase):
    """redact() must leave no line of a key body behind, in any of its shapes."""

    def assertGone(self, text, body_lines):
        out, found = P.redact(text)
        self.assertEqual([k for k in found if k == "private-key"], ["private-key"])
        left = [ln for ln in body_lines if ln in out]
        self.assertEqual(left, [], f"{len(left)} of {len(body_lines)} body lines survive")
        return out

    def test_pem_shapes(self):
        for label, width, n in (("RSA PRIVATE KEY", 64, 25), ("PRIVATE KEY", 64, 26),
                                ("EC PRIVATE KEY", 64, 3), ("OPENSSH PRIVATE KEY", 70, 6),
                                ("ENCRYPTED PRIVATE KEY", 64, 27)):
            with self.subTest(label):
                text, body = pem(label, width, n)
                out = self.assertGone(f"$ cat key.pem\n{text}\n$ echo done\n", body)
                self.assertEqual(out.count("\n"), text.count("\n") + 3)      # line numbers kept
                self.assertTrue(out.endswith("$ echo done\n"))

    def test_encrypted_legacy_pem_with_headers(self):
        text, body = pem("RSA PRIVATE KEY", 64, 20,
                         headers="Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC," + hexs(32).upper() + "\n")
        self.assertGone(text, body)

    def test_json_escaped_service_account_key(self):
        text, body = pem("PRIVATE KEY", 64, 26)
        sa = json.dumps({"type": "service_account", "private_key_id": hexs(40),
                         "private_key": text + "\n", "client_email": "x@y.iam.gserviceaccount.com"})
        out = self.assertGone(sa, body)
        self.assertIn('"client_email"', out)
        self.assertEqual(json.loads(out)["private_key"], "[REDACTED:private-key]\n")

    def test_truncated_key_without_footer(self):
        text, body = pem("RSA PRIVATE KEY", 64, 25)
        head = "\n".join(text.split("\n")[:12])               # `head -12 key.pem`
        self.assertGone(head + "\n", body[:11])

    def test_real_keys_when_tools_exist(self):
        d = tempfile.mkdtemp(prefix="guards-keys-")
        try:
            keys = []
            if shutil.which("openssl"):
                for args in (["genrsa", "2048"], ["ecparam", "-genkey", "-name", "prime256v1", "-noout"]):
                    keys.append(subprocess.run(["openssl", *args], capture_output=True, text=True).stdout)
            if shutil.which("ssh-keygen"):
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(d, "k")],
                               check=True)
                with open(os.path.join(d, "k")) as f:
                    keys.append(f.read())
            if not keys:
                self.skipTest("neither openssl nor ssh-keygen is installed")
            for key in keys:
                lines = key.strip().split("\n")
                with self.subTest(lines[0]):
                    self.assertGone(key, lines[1:-1])
                    self.assertGone(json.dumps({"private_key": key}), lines[1:-1])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestFalsePositives(unittest.TestCase):
    """Ordinary work must never trip the guards."""

    def test_searching_for_patterns(self):
        for cmd in ("grep -rn 'sk-ant-' . && rg 'AKIA[0-9A-Z]{16}' src/",
                    "grep -E 'xox[abpr]-|gh[pousr]_' -r .",
                    "git log -S'sk-ant-' -- journal.md",
                    "rg -n 'BEGIN (RSA|OPENSSH) PRIVATE KEY' ~/.ssh"):
            with self.subTest(cmd):
                self.assertEqual(kinds(cmd), [])

    def test_git_log_output(self):
        log = "\n".join(f"commit {hexs(40)}\nAuthor: T <t@example.com>\n\n    fix token refresh\n"
                        for _ in range(20))
        self.assertEqual(kinds(log), [])
        self.assertEqual(kinds("9cda71d Merge pull request #5 from SearchingAlpha/docs"), [])

    def test_docs_mentioning_aws_keys(self):
        doc = ("AWS access key IDs start with AKIA (long-term) or ASIA (temporary), for "
               "example AKIA" + "IOSFODNN7EXAMPLE with the secret wJalrXUtnFEMI/K7MDENG/"
               "bPxRfiCY" + "EXAMPLEKEY. A key like AKIA" + "ABCDEFGHIJ123456 is a sample.")
        self.assertEqual(kinds(doc), [])

    def test_base64_uuid_and_hashes(self):
        blob = '{"data": "' + rnd(800, B64) + '=="}'
        self.assertEqual(kinds(blob), [])
        self.assertEqual(kinds('session_id = "550e8400-e29b-41d4-a716-446655440000"\n'
                               'idempotency_key: 3f2a8b1c-1234-4abc-9def-0123456789ab'), [])
        h = hexs(64)
        for text in (f"image: app@sha256:{h}", f"checksum={h}", f'"integrity": "sha512-{rnd(86)}=="',
                     f"etag: {hexs(32)}", f"commit_sha: {hexs(40)}"):
            with self.subTest(text[:12]):
                self.assertEqual(kinds(text), [])

    def test_identifiers_under_key_like_names(self):
        sha1, md5 = hexs(40), hexs(32)
        for text in (f"[user]\n\tsigningkey = {sha1.upper()}\n",
                     f"git config --global user.signingKey={sha1.upper()}",
                     '"pagination_token": "' + rnd(40) + '"',
                     f"aws resourcegroupstaggingapi get-resources --pagination-token={rnd(60)}",
                     f'"key": "{md5}"', f'{{"key": "{sha1}"}}', f"dedupe_key={hexs(40)}",
                     f'revision_key = "{sha1}"', f"run_key={sha1}",
                     '"content_key": "' + "/".join(rnd(14) for _ in range(5)) + '"',
                     "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...",
                     "Authorization: Bearer <your-token-here>"):
            with self.subTest(text[:24]):
                self.assertEqual(kinds(text), [])

    def test_arns_object_keys_and_public_keys(self):
        acct = "123456789012"
        for name in ("MyTestDatabaseSecret-a1b2c3", "OrdersStackDbSecretA1B2C3D4-AbCdEfGh1234-xYz789",
                     "SlackBotTokenProd-Zx81Pq", "prod/orders/api-" + rnd(6)):
            arn = f"arn:aws:secretsmanager:eu-west-1:{acct}:secret:{name}"
            for text in (f"aws secretsmanager describe-secret --secret-id {arn}",
                         json.dumps({"SecretList": [{"ARN": arn, "Name": name}]}, indent=2),
                         '{"Resource": "' + arn + '"}'):
                with self.subTest(text[:40]):
                    self.assertEqual(kinds(text), [])
        self.assertEqual(kinds(f"arn:aws-cn:secretsmanager:cn-north-1:{acct}:secret:" + rnd(24) + "-x1Y2z3"), [])
        s3 = json.dumps({"Contents": [{"Key": "WordPress_App_2-0.zip"}, {"Key": "SalesReport2026Q3Final.xlsx"},
                                      {"Key": "Orders_Export_2026W40_v2.csv"}]})
        self.assertEqual(kinds(s3), [])
        self.assertEqual(kinds("Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"), [])
        self.assertEqual(kinds('key: "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE' + rnd(60) + '=="'), [])
        # still caught: a secret next to an ARN, a key under a weak name without an extension
        self.assertEqual(kinds(f'{{"ARN": "arn:aws:secretsmanager:eu-west-1:{acct}:secret:x", '
                               f'"SecretString": "{rnd(32)}"}}'), ["high-entropy-secret"])
        self.assertEqual(kinds(f'"key": "{rnd(32)}"'), ["high-entropy-secret"])

    def test_code_and_config_that_reference_secrets(self):
        code = ('token = os.environ["API_TOKEN"]\npassword = getpass.getpass()\n'
                'SECRET_KEY = os.getenv("SECRET_KEY")\napi_key=settings.API_KEY_FROM_SETTINGS_V2\n'
                'max_tokens=4096\ntoken = getUserAccessTokenV2FromCache()\n'
                'export GITHUB_TOKEN=$(gh auth token)\nAPI_KEY="${API_KEY}"\n'
                'curl -H "Authorization: Bearer $TOKEN"\npassword_file=/etc/secret/pw2Xa\n'
                'public_key = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE9vL8pR4tW7yB3nF6hJ1s"\n'
                'cache_key = "Ab12Cd34Ef56Gh78Ij90Kl"\n"NextToken": "eyJ' + rnd(80) + '"\n'
                'kms_key_id = arn:aws:kms:eu-west-1:000000000000:key/x\n'
                'accessToken: this.authService.getAccessTokenSilently2\n'
                'ANTHROPIC_API_KEY=sk-ant-your-key-here-000000000000\n'
                'GITHUB_TOKEN=ghp_' + "x" * 36 + '\nSECRET = "TOP-SECRET-MARKER-9f3c"\n'
                'API_KEY=[REDACTED:high-entropy-secret]\nsecret: my-app-db-credentials-prod2\n'
                'token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"\n')
        self.assertEqual(kinds(code), [])

    def test_other_programs_p_flags_near_mysql(self):
        """`-p…` belongs to mysql only when mysql is the command word."""
        for text in ("find . -name '*mysql*' -print",
                     "find /etc -name 'my.cnf' -o -name '*mysql*' -printf '%p\\n'",
                     "find . -name '*.py' -path '*mysql*' -print0 | xargs -0 wc -l",
                     "docker run --name mysql -d -p3306:3306 mysql:8",
                     "docker run -d --name db mysql:8 -p33060:33060",
                     "grep -rn mysql src -print",
                     "docker run -it --rm --name mysql -p127.0.0.1:3306:3306 mysql",
                     "gcc -o app app.c -I/usr/include/mysql -lmysqlclient -pthread",
                     "find /var/lib/mysql -name '*.ibd' -prune",
                     "apt-get install mysql-server -pfoobar",
                     "systemctl restart mysql; ps -p12345",
                     "## Run it\n\n```bash\ndocker run --name mysql -d -p3306:3306 \\\n  mysql:8\n```\n",
                     'mysqldump -u backup -p"$MYSQL_PW" shop > shop.sql'):
            with self.subTest(text[:40]):
                self.assertEqual(kinds(text), [])

    def test_pem_without_key_material(self):
        self.assertEqual(kinds("-----BEGIN OPENSSH " + "PRIVATE KEY----- marks the file"), [])
        self.assertEqual(kinds("-----BEGIN PUBLIC KEY-----\n" + rnd(64)), [])
        self.assertEqual(kinds("-----BEGIN " + "PRIVATE KEY-----\n...\n-----END " + "PRIVATE KEY-----"), [])

    def test_this_repo_is_clean(self):
        """Every text file of the guards package scans clean (fixtures are built)."""
        for path in sorted(GUARDS.rglob("*")):
            if path.suffix in (".py", ".md", ".json", ".sh") and "__pycache__" not in path.parts:
                with self.subTest(path.name):
                    self.assertEqual(kinds(path.read_text()), [])


class TestMarkerAndRedaction(unittest.TestCase):
    def test_allow_marker(self):
        k = FAKE.anthropic
        text = f"KEY = '{k}'  # {P.ALLOW_MARKER}\nOTHER = '{k}'\n"
        self.assertEqual(len(P.scan(text)), 1)
        self.assertEqual(len(P.scan(text, honor_marker=False)), 2)

    def test_redact_never_leaves_the_value(self):
        k, g = FAKE.anthropic, FAKE.github
        out, found = P.redact(f"a {k} b {g} c")
        self.assertEqual(out, "a [REDACTED:anthropic-api-key] b [REDACTED:github-token] c")
        self.assertNotIn(k, out)
        self.assertEqual(P.summarize(found), "1 x anthropic-api-key, 1 x github-token")

    def test_non_text_inputs(self):
        self.assertEqual(P.scan(""), [])
        self.assertEqual(P.scan(None), [])


class TestSpeed(unittest.TestCase):
    def best_ms(self, text):
        best = 1e9
        for _ in range(3):
            t = time.perf_counter()
            P.scan(text)
            best = min(best, (time.perf_counter() - t) * 1000)
        return best

    def test_throughput(self):
        for name, text, budget in (
                ("mixed text", (rnd(60) + " key=value token: abc password = 'x'\n") * 10000, 150),
                ("runs of =", ("=" * 80 + "\n") * 12000, 150),
                ("many assignments", ("x: API_KEY_NAME_THAT_IS_LONG=" + "a" * 30 + "\n") * 15000, 400),
                ("many findings", "".join(f"token={rnd(40)}\n" for _ in range(20000)), 1500)):
            with self.subTest(name):
                ms = self.best_ms(text)
                self.assertLess(ms, budget, f"{name}: {len(text) / 1e6:.1f} MB took {ms:.0f} ms")

    def test_no_quadratic_inputs(self):
        """Long unbroken base64 runs full of "eyJ" (base64 of JSON) and lines full of
        client names stay linear: these took seconds when each candidate was tried
        against the rest of the run."""
        import base64
        rows = json.dumps([{"id": i, "sku": f"B0{i:08d}", "qty": i % 7} for i in range(12000)])
        rows = rows[:500_000]
        for name, text in (("eyJ x 50000", "eyJ" * 50000),
                           ("base64 of 500 KB JSON", base64.b64encode(rows.encode()).decode()),
                           ("base64url of 500 KB JSON", base64.urlsafe_b64encode(rows.encode()).decode()),
                           ("base64 of '{\"' runs", base64.b64encode(b'{"' * 150_000).decode()),
                           ("mysql words", "mysql " * 100000 + " -p"),
                           ("mysql spans", ("mysql" + " " * 190 + "-p") * 3000)):
            with self.subTest(name):
                ms = self.best_ms(text)
                self.assertLess(ms, 200, f"{name}: {len(text) / 1e6:.2f} MB took {ms:.0f} ms")


if __name__ == "__main__":
    unittest.main()
