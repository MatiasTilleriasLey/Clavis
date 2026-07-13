"""Check de integración del pipeline audio->MusicXML de piano. Necesita las deps ML
(scripts/install_ml.sh) y el checkpoint de piano; si no están, se saltea.
Corre: .venv/bin/python test_pipeline.py"""
import os
import subprocess
import tempfile

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

try:
    import piano_transcription_inference  # noqa: F401
    from app.pipeline import transcribe
    HAVE_ML = True
except Exception:
    HAVE_ML = False


def run():
    if not HAVE_ML:
        print("SKIP: deps ML no instaladas (correr scripts/install_ml.sh)")
        return
    work = tempfile.mkdtemp()
    wav = os.path.join(work, "tone.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "22050", wav],
        check=True, capture_output=True,
    )
    mscore = os.environ.get("MSCORE_BIN")
    if not mscore or not os.path.exists(mscore):
        print("SKIP: MSCORE_BIN no seteado (requerido para la notación)")
        return
    xml, pdf = transcribe(wav, work, title="Test", mscore_bin=mscore)
    txt = open(xml).read()
    assert "score-partwise" in txt, "no generó un MusicXML válido"
    # Piano => gran pentagrama: dos staves (clave de sol + clave de fa) en un part.
    assert "<sign>F</sign>" in txt, "falta la clave de fa (mano izquierda)"
    assert pdf and os.path.exists(pdf), "no generó el PDF"
    print("OK: pipeline audio->MusicXML+PDF (piano, MuseScore) verificado")


if __name__ == "__main__":
    run()
