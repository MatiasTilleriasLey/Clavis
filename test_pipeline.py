"""Check de integración del pipeline audio->MusicXML. Necesita las deps ML
(scripts/install_ml.sh); si no están, se saltea. Corre: .venv/bin/python test_pipeline.py"""
import os
import subprocess
import tempfile

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

try:
    import basic_pitch  # noqa: F401
    from app.pipeline import transcribe
    HAVE_ML = True
except Exception:
    HAVE_ML = False


def run():
    if not HAVE_ML:
        print("SKIP: deps ML no instaladas (correr scripts/install_ml.sh)")
        return
    work = tempfile.mkdtemp()
    wav = os.path.join(work, "a4.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "22050", wav],
        check=True, capture_output=True,
    )
    xml = transcribe(wav, work)
    txt = open(xml).read()
    assert "score-partwise" in txt, "no generó un MusicXML válido"
    assert "<pitch>" in txt, "el MusicXML no contiene ninguna nota"
    assert "<step>A</step>" in txt, "no detectó A4 en un tono de 440Hz"
    print("OK: pipeline audio->MusicXML verificado (A4 detectado)")


if __name__ == "__main__":
    run()
