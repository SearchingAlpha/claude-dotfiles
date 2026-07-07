#!/usr/bin/env python3
"""notify.py — best-effort email notifications for long autonomous runs.

A full build spans hours across many `claude -p` sessions; the operator wants to
know — without watching the log — when it finishes, when a requirement passes, and
above all when it *stops needing them* (an escalation or a fatal error). This module
is the transport for those pings. Message composition lives in loop.py, which already
holds the run state; this file only knows how to put text in the operator's inbox.

Transport: **Amazon SES over HTTPS (boto3)**. This is a deliberate choice forced by
the environment — on a locked-down corporate dev host, direct SMTP (`:587`) is
often blocked and internal SMTP relays reject the host as an unauthorized relay
client, so neither `smtplib` path works. SES's HTTPS endpoint (port 443) stays
reachable. SES starts in *sandbox* mode,
where both sender and recipient must be verified identities — so we send FROM and TO
the same operator-verified address. That needs a single one-time verification click
(`aws ses verify-email-identity`) and sidesteps strict DMARC on a corporate sender
domain, which would otherwise make external inboxes reject the mail.

Never raises into the caller: a notification is a nicety, not part of the build. Any
failure (missing creds, unverified identity, network) is caught, logged, and swallowed.
"""

from __future__ import annotations

import socket


class Notifier:
    """SES-backed emailer. Disabled (a silent no-op) unless an address is given."""

    def __init__(self, email: str | None, region: str = "us-east-1",
                 project_label: str = "build") -> None:
        self.email = email
        self.region = region
        self.project = project_label
        self.enabled = bool(email)
        self._client = None                       # lazily built on first send
        self._host = socket.gethostname()

    def _ses(self):
        if self._client is None:
            import boto3  # deferred: no import cost / dependency unless notifying
            self._client = boto3.client("ses", region_name=self.region)
        return self._client

    def send(self, subject: str, body: str) -> bool:
        """Email one message. Returns True on success; never raises.

        Sender == recipient == the SES-verified operator address (sandbox-safe).
        A trailing signature line ties the mail to the host + project so multiple
        concurrent runs on the same box are distinguishable in the inbox.
        """
        if not self.enabled:
            return False
        full_body = (
            f"{body}\n\n"
            f"— autobuild harness on {self._host}\n"
            f"  project: {self.project}"
        )
        try:
            self._ses().send_email(
                Source=self.email,
                Destination={"ToAddresses": [self.email]},
                Message={
                    "Subject": {"Data": f"[autobuild:{self.project}] {subject}"},
                    "Body": {"Text": {"Data": full_body}},
                },
            )
            print(f"  📧 emailed: {subject}")
            return True
        except Exception as e:  # noqa: BLE001 — a notify failure must never kill the run
            print(f"  ⚠ email notification failed "
                  f"({type(e).__name__}: {e}); continuing without it.")
            return False
