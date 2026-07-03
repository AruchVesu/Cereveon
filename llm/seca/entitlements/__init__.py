"""Entitlements layer: subscription plans and usage metering.

Phase 1 (dormant schema) ships only ``models.py`` — the
``usage_counters`` table plus the ``Player.plan`` column over in
``llm.seca.auth.models``.  The deterministic metering service
(``llm.seca.entitlements.service`` — limits table, ``check`` /
``record`` / ``admit`` helpers, and the ``SECA_ENTITLEMENTS_ENFORCED``
activation flag) lands in the next subtask; until it does, no runtime
code path reads anything defined here.
"""
