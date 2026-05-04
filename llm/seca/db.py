from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

try:
    from llm.seca.auth.models import Base

    # Register models before create_all
    import llm.seca.events.models  # noqa: F401
    import llm.seca.brain.models  # noqa: F401
    import llm.seca.brain.training.models  # noqa: F401
    import llm.seca.analytics.models  # noqa: F401
except ModuleNotFoundError:
    from seca.auth.models import Base
    import seca.events.models  # noqa: F401
    import seca.brain.models  # noqa: F401
    import seca.brain.training.models  # noqa: F401
    import seca.analytics.models  # noqa: F401


DATABASE_URL = "sqlite:///data/seca.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(bind=engine)

# Lightweight migration for new columns
with engine.connect() as conn:
    rows = conn.execute(text("PRAGMA table_info(players)")).fetchall()
    columns = {r[1] for r in rows}
    if "player_embedding" not in columns:
        conn.execute(text("ALTER TABLE players ADD COLUMN player_embedding TEXT DEFAULT '[]'"))
        conn.commit()
