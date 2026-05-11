# Sprint 6.A follow-up (2026-05-12): ``engine.py``'s ``CoachEngine``
# class was deleted as unreachable dead code — no in-tree caller
# imported it, and its ``choose_next_action`` body referenced a
# nonexistent ``CurriculumPolicy.enumerate_actions`` method, so it
# would have crashed at runtime if it had ever been invoked.
# The package no longer re-exports anything by default; callers
# import individual submodules (``llm.seca.coach.live_controller``,
# ``llm.seca.coach.executor``, etc.) directly.
