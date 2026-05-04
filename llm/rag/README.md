SECA v1 – Safe Adaptive Chess Coach
Features

Deterministic runtime

Adaptive opponent (non-learning)

Curriculum recommendation (static logic)

SafeWorldModel stub

No self-modification

Installation
pip install -r requirements.txt
python scripts/setup_stockfish.py

Safety Verification
python scripts/verify_safety.py

Run Server
uvicorn app.server:app --reload

Health Check
curl http://127.0.0.1:8000/health

Safety Model

SECA v1 enforces:

No online training

No bandit updates

No world model learning

No background adaptive loops

Safety layer enforced at startup via:

llm/seca/safety/freeze.py