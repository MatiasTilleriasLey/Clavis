"""Detección de formato de audio por magic bytes, no por extensión (threat model §subprocess).
Allowlist chico (MP3/WAV/M4A/MP4), así evitamos depender de libmagic nativo."""

ALLOWED = ("wav", "mp3", "m4a", "mp4")


def detect_audio_kind(head):
    """Devuelve 'wav'|'mp3'|'m4a'|'mp4' según los primeros bytes, o None si no coincide."""
    if head is None or len(head) < 12:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3":
        return "mp3"
    # ponytail: frame-sync MPEG (0xFF + 3 bits altos) es heurístico; ffmpeg valida en serio
    # después. Suficiente como filtro de entrada previo al pipeline.
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a" if head[8:11] == b"M4A" else "mp4"
    return None
