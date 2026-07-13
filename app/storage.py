"""Storage de partituras en filesystem, organizado por user_id. Nunca se sirve como estático:
el acceso pasa siempre por endpoints que verifican ownership (§4.8, §6.25)."""
import os
import shutil

from flask import current_app


def _scores_dir(user_id):
    root = current_app.config["STORAGE_ROOT"]
    d = os.path.join(root, "scores", str(int(user_id)))
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def path_for(user_id, stored_uuid, ext):
    return os.path.join(_scores_dir(user_id), f"{stored_uuid}.{ext}")


def save(user_id, stored_uuid, xml_src, pdf_src=None):
    shutil.copy(xml_src, path_for(user_id, stored_uuid, "musicxml"))
    if pdf_src and os.path.exists(pdf_src):
        shutil.copy(pdf_src, path_for(user_id, stored_uuid, "pdf"))


def delete(user_id, stored_uuid):
    for ext in ("musicxml", "pdf"):
        try:
            os.remove(path_for(user_id, stored_uuid, ext))
        except FileNotFoundError:
            pass
