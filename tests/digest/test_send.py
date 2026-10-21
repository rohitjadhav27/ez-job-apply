import smtplib
from types import SimpleNamespace

import pytest

from ezapply.digest.send import send_email

SETTINGS = SimpleNamespace(sender_email="bot@example.com", sender_app_password="pw")


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, fail_times=0):
        self.args = (host, port, timeout)
        self.sent = []
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def login(self, user, pw):
        self.login_args = (user, pw)

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


def test_send_builds_multipart_alternative():
    FakeSMTP.instances = []
    send_email(SETTINGS, "me@example.com", "Subj", "<p>hi</p>", "hi", smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[0]
    assert smtp.args == ("smtp.gmail.com", 465, 30)
    assert smtp.login_args == ("bot@example.com", "pw") and smtp.quit_called
    msg = smtp.sent[0]
    assert msg["To"] == "me@example.com" and msg["Subject"] == "Subj"
    assert "Ez Apply" in msg["From"] and "bot@example.com" in msg["From"]
    assert msg.get_content_type() == "multipart/alternative"
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]


def test_send_retries_then_raises():
    calls, sleeps = [], []

    class Failing(FakeSMTP):
        def login(self, user, pw):
            calls.append(1)
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    with pytest.raises(smtplib.SMTPException):
        send_email(SETTINGS, "a@b.c", "s", "h", "t", smtp_factory=Failing, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [5, 15]


def test_send_succeeds_after_transient_failure():
    sleeps, n = [], []

    class Flaky(FakeSMTP):
        def send_message(self, msg):
            n.append(1)
            if len(n) < 2:
                raise OSError("net")
            super().send_message(msg)

    send_email(SETTINGS, "a@b.c", "s", "h", "t", smtp_factory=Flaky, sleep=sleeps.append)
    assert len(n) == 2 and sleeps == [5]


def test_non_smtp_errors_not_retried():
    sleeps = []

    class Boom(FakeSMTP):
        def login(self, user, pw):
            raise ValueError("bug")

    with pytest.raises(ValueError):
        send_email(SETTINGS, "a@b.c", "s", "h", "t", smtp_factory=Boom, sleep=sleeps.append)
    assert sleeps == []


def test_close_called_when_quit_fails():
    closed = []

    class BadQuit(FakeSMTP):
        def quit(self):
            raise smtplib.SMTPServerDisconnected("gone")

        def close(self):
            closed.append(1)

    send_email(SETTINGS, "a@b.c", "s", "h", "t", smtp_factory=BadQuit)
    assert closed == [1]
