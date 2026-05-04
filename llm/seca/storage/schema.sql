-- Raw-sqlite schema for the storage tables that are NOT modelled by
-- SQLAlchemy.  Authoritative ownership map:
--
--   SQLAlchemy (Base.metadata.create_all in init_auth_schema)
--   ----------------------------------------------------------
--     players                       — auth/models.py:Player
--     sessions                      — auth/models.py:Session
--     game_events                   — events/models.py:GameEvent
--     analytics_events              — analytics/models.py:AnalyticsEvent
--     rating_updates                — brain/models.py:RatingUpdate
--     confidence_updates            — brain/models.py:ConfidenceUpdate
--     bandit_experiences            — brain/models.py:BanditExperience
--     training_decisions            — brain/training/models.py:TrainingDecision
--     training_outcomes             — brain/training/models.py:TrainingOutcome
--
--   This file (executed by storage/db.py:init_db)
--   ---------------------------------------------
--     games                         — repo.py game-lifecycle rows
--     moves                         — repo.py per-ply move log
--     explanations                  — repo.py /explanation_outcome learning score
--
-- Why split?  ``games``, ``moves``, ``explanations`` are written exclusively
-- by repo.py via raw sqlite3 (no ORM session) for the /move and
-- /explanation_outcome request paths.  Modelling them in SQLAlchemy would
-- gain nothing without porting repo.py too.  Leaving them here is the
-- minimal-change boundary; the duplicate ``players`` /
-- ``training_decisions`` / ``training_outcomes`` definitions that used to
-- live here were removed because they conflicted with the SQLAlchemy
-- models — schema.sql ran first under FastAPI lifespan, creating only
-- partial tables, then ``Base.metadata.create_all`` saw the tables
-- already present and skipped the missing columns.

CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    player_id TEXT,
    result TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    -- In-progress checkpoint state for cross-device resume.  Populated
    -- by repo.checkpoint_game() during play; nulled out (or just left
    -- alongside finished_at) when the game completes.  The client
    -- pulls these via GET /game/active at cold-start when no local
    -- snapshot exists, e.g. after a fresh install on a second device.
    current_fen TEXT,
    current_uci_history TEXT,
    last_checkpoint_at TIMESTAMP,
    FOREIGN KEY(player_id) REFERENCES players(id)
);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT,
    ply INTEGER,
    fen TEXT,
    uci TEXT,
    san TEXT,
    eval REAL,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

CREATE TABLE IF NOT EXISTS explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT,
    ply INTEGER,
    explanation_type TEXT,
    confidence REAL,
    learning_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

-- Per-player LinUCB weights — backs the deferred bandit decision step
-- in seca/brain/bandit/decision.py.  Each row is one player × one
-- action: A is an n×n matrix (sufficient statistic for the design
-- matrix), b is an n×1 vector (sufficient statistic for the
-- context-weighted reward).  Stored as JSON list-of-lists for A and
-- list for b so SQLite stays portable; numpy round-trips at read.
--
-- Updated incrementally per game (no gradient descent, no neural
-- retraining); SECA v1 explicitly permits this kind of lightweight
-- decision-layer adaptation.  See docs/SECA.md for the boundary.
CREATE TABLE IF NOT EXISTS bandit_weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    action TEXT NOT NULL,
    n_features INTEGER NOT NULL,
    A_json TEXT NOT NULL,
    b_json TEXT NOT NULL,
    alpha REAL NOT NULL DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, action)
);

-- Per-player opening repertoire — backs AtriumOpenings.  Each row is
-- one opening line the player has committed to studying.  GET
-- /repertoire returns the list ordered by ordinal; if a player has
-- nothing stored the endpoint returns the canonical 4-entry default
-- repertoire so a fresh user sees a populated screen.
--
-- ordinal: stable display order (Roman numerals I–IV in the UI).
-- mastery: 0.0–1.0 — how well the player knows this line.  Updated
--          by future drill endpoints; for now seeded from the
--          design defaults.
-- is_active: exactly one row per player should be 1 (the line the
--          "Drill active line" button targets).  Enforced by the
--          set-active endpoint, not by a SQL constraint, so manual
--          inserts won't crash if the invariant is briefly broken.
CREATE TABLE IF NOT EXISTS repertoire (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    eco TEXT NOT NULL,
    name TEXT NOT NULL,
    line TEXT NOT NULL,
    mastery REAL NOT NULL DEFAULT 0.0,
    is_active INTEGER NOT NULL DEFAULT 0,
    ordinal INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(player_id) REFERENCES players(id),
    UNIQUE(player_id, eco)
);
