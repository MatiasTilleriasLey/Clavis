"""Check de export PDF con MuseScore. Necesita MSCORE_BIN y las deps ML; si no, se saltea.
Corre: MSCORE_BIN=... .venv/bin/python test_pdf.py"""
import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

MSCORE = os.environ.get("MSCORE_BIN")


def run():
    if not MSCORE or not os.path.exists(MSCORE):
        print("SKIP: MSCORE_BIN no seteado (correr scripts/install_ml.sh)")
        return
    from app.pipeline import musicxml_to_pdf
    work = tempfile.mkdtemp()
    xml = os.path.join(work, "s.musicxml")
    open(xml, "w").write(
        '<?xml version="1.0"?><!DOCTYPE score-partwise>'
        '<score-partwise version="3.1"><part-list><score-part id="P1">'
        '<part-name>M</part-name></score-part></part-list>'
        '<part id="P1"><measure number="1"><attributes><divisions>1</divisions>'
        '<key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>'
        '<clef><sign>G</sign><line>2</line></clef></attributes>'
        '<note><pitch><step>A</step><octave>4</octave></pitch>'
        '<duration>4</duration><type>whole</type></note></measure></part></score-partwise>'
    )
    pdf = os.path.join(work, "s.pdf")
    musicxml_to_pdf(xml, pdf, MSCORE)
    assert os.path.exists(pdf), "no generó PDF"
    assert open(pdf, "rb").read(5) == b"%PDF-", "el archivo no es un PDF"
    print("OK: export MusicXML->PDF verificado")


if __name__ == "__main__":
    run()
