"""Worker de la cola de transcripción. Correr UN solo proceso => a lo sumo un job pesado
a la vez (límite de concurrencia sin GPU, §6.27):  .venv/bin/python worker.py"""
from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402

from redis import Redis  # noqa: E402
from rq import Queue, Worker  # noqa: E402

from app.jobs import QUEUE_NAME  # noqa: E402

if __name__ == "__main__":
    conn = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    Worker([Queue(QUEUE_NAME, connection=conn)], connection=conn).work()
