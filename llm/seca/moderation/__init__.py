"""User reports of AI-generated coach content.

Backs the in-app "Report" affordance Google Play's AI-Generated Content
policy requires: users must be able to flag offensive AI output without
leaving the app, and developers must use the reports to inform
moderation.  Rows are operator-read-only (a moderation queue) — never
read back into any coaching, prompt, or adaptation path.
"""
