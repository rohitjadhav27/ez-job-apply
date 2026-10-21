from ezapply.core.urls import safe_url


def test_safe_url_allows_only_http_with_host():
    assert safe_url(" https://a.com/x ") == "https://a.com/x"
    assert safe_url("http://a.com") == "http://a.com"
    for bad in (None, "", "javascript:alert(1)", "data:text/html,x", "https://", "//a.com", "ftp://a.com"):
        assert safe_url(bad) is None
