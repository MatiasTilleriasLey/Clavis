import os

from dotenv import load_dotenv

load_dotenv()  # carga .env en dev antes de leer config

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # TLS autofirmado (Secure cookies lo exigen). HOST por defecto solo localhost (seguro por
    # defecto, §red). Para acceder desde otros dispositivos de la LAN: HOST=0.0.0.0 en .env.
    # NO exponer el puerto a internet público (queda fuera del threat model).
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8443"))
    app.run(host=host, port=port, ssl_context=("certs/cert.pem", "certs/key.pem"))
