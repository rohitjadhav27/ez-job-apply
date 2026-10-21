from ezapply.core.compose import (
    MAX_COMPOSE_URL,
    compose_body,
    gmail_ios_url,
    gmail_web_url,
    mailto_url,
)


def test_gmail_web_url_encodes_and_selects_account():
    u = gmail_web_url("a@gmail.com", "hr@x.com", "Hi & bye", "Line1\nLine2")
    assert u.startswith("https://mail.google.com/mail/?") and "authuser=a%40gmail.com" in u
    assert "view=cm" in u and "fs=1" in u and "su=Hi%20%26%20bye" in u
    assert "Line1%0ALine2" in u
    assert "to=hr%40x.com" in u


def test_gmail_web_url_param_order():
    u = gmail_web_url("a@gmail.com", "hr@x.com", "S", "B")
    keys = [p.split("=")[0] for p in u.split("?", 1)[1].split("&")]
    assert keys == ["authuser", "view", "fs", "to", "su", "body"]


def test_gmail_ios_url():
    assert gmail_ios_url("hr@x.com", "Hi & bye", "a\nb") == (
        "googlegmail://co?to=hr%40x.com&subject=Hi%20%26%20bye&body=a%0Ab"
    )


def test_mailto_url():
    assert mailto_url("hr@x.com", "Hi & bye", "a b") == (
        "mailto:hr@x.com?subject=Hi%20%26%20bye&body=a%20b"
    )


def test_max_compose_url():
    assert MAX_COMPOSE_URL == 1900


def test_compose_body_replaces_resume_line():
    body = "Hello\n\nResume: https://old\n\nThanks"
    assert compose_body(body, "https://new") == "Hello\n\nResume: https://new\n\nThanks"


def test_compose_body_appends_when_absent():
    assert compose_body("Hello", "https://new") == "Hello\n\nResume: https://new"


def test_compose_body_drops_resume_line_when_no_url():
    assert compose_body("Hello\n\nResume: https://old", "") == "Hello"
    assert compose_body("Hello\n\nResume: https://old\n\nThanks", "") == "Hello\n\nThanks"
    assert compose_body("Hello", "") == "Hello"
