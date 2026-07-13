"""Check del sniffer de magic bytes. Corre: .venv/bin/python test_audio.py"""
from app.audio import detect_audio_kind


def run():
    # WAV: RIFF....WAVE
    assert detect_audio_kind(b"RIFF\x24\x08\x00\x00WAVEfmt ") == "wav"
    # MP3 con tag ID3
    assert detect_audio_kind(b"ID3\x03\x00\x00\x00\x00\x00\x00\x00\x00") == "mp3"
    # MP3 frame sync (0xFF 0xFB)
    assert detect_audio_kind(b"\xff\xfb\x90\x00" + b"\x00" * 8) == "mp3"
    # M4A: ftyp M4A
    assert detect_audio_kind(b"\x00\x00\x00\x20ftypM4A \x00\x00") == "m4a"
    # MP4: ftyp isom
    assert detect_audio_kind(b"\x00\x00\x00\x18ftypisom\x00\x00") == "mp4"
    # Rechazos: contenido no-audio, demasiado corto, extensión mentida
    assert detect_audio_kind(b"<?xml version=") is None       # no es audio
    assert detect_audio_kind(b"RIFF") is None                 # corto
    assert detect_audio_kind(b"%PDF-1.4\x00\x00\x00\x00") is None  # PDF disfrazado
    assert detect_audio_kind(None) is None
    print("OK: sniffer de magic bytes verificado (9 aserciones)")


if __name__ == "__main__":
    run()
