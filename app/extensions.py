from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# Instancias compartidas, inicializadas en el app factory.
# ponytail: sin rate limiter / login todavía — eso llega con auth (paso 2).
db = SQLAlchemy()
migrate = Migrate()
