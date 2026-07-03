"""Billing layer: Google Play purchase verification → plan activation.

One surface (``router.py``): ``POST /billing/google/verify`` takes the
purchase token the Android Play Billing flow produced, verifies it
server-side against the Google Play Developer API, and flips the
authenticated player's ``plan`` via ``llm.seca.entitlements.set_plan``.
The client is never trusted about what it bought — entitlement comes
only from Google's answer.

Deliberately out of scope here (tracked follow-ups): Real-Time
Developer Notifications (expiry → automatic downgrade), refund
handling, and purchase-token persistence for audit.
"""
