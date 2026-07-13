from dotenv import load_dotenv

load_dotenv()  # carga .env en dev antes de leer config

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # Dev con TLS autofirmado (Secure cookies lo exigen). Bind solo a localhost.
    app.run(host="127.0.0.1", port=8443, ssl_context=("certs/cert.pem", "certs/key.pem"))
