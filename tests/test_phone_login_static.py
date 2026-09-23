from pathlib import Path


def test_phone_login_routes_and_security_contract():
    source = Path(__file__).resolve().parents[1] / "service" / "phone_login.py"
    text = source.read_text(encoding="utf-8")
    for route in (
        '"/connect/{token}"',
        '"/connect/{token}/start"',
        '"/connect/{token}/code"',
        '"/connect/{token}/password"',
        '"/connect/{token}/resend"',
        '"/connect/{token}/cancel"',
    ):
        assert route in text
    assert "in_memory=True" in text
    assert "export_session_string" in text
    assert "connect_session" in text
    assert "MAX_VERIFY_ATTEMPTS" in text
    assert "SESSION_PASSWORD_NEEDED" in text
    assert "Cache-Control" in text
