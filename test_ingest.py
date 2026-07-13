"""Check de la allowlist de dominios (defensa SSRF, §4.2/§6.4). Corre: .venv/bin/python test_ingest.py"""
from app.ingest import is_allowed_url


def run():
    ok = [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "http://youtube.com/x",
        "https://www.instagram.com/reel/x",
        "https://vm.tiktok.com/x",
        "https://rr3---sn-x.googlevideo.com/videoplayback?x=1",
    ]
    bad = [
        "http://localhost/x",                    # servicio local
        "http://127.0.0.1:6379/",                # pivoteo a Redis
        "http://169.254.169.254/latest/meta",    # metadata de cloud
        "http://evil.com/x",                     # dominio arbitrario
        "https://youtube.com.evil.com/x",        # sufijo engañoso
        "https://evilyoutube.com/x",             # sin punto separador
        "file:///etc/passwd",                    # esquema no http
        "javascript:alert(1)",                   # esquema peligroso
        "",                                      # vacío
        "not a url",
    ]
    for u in ok:
        assert is_allowed_url(u), f"debería permitir: {u}"
    for u in bad:
        assert not is_allowed_url(u), f"NO debería permitir: {u}"
    print(f"OK: allowlist verificada ({len(ok)} permitidos, {len(bad)} rechazados)")


if __name__ == "__main__":
    run()
