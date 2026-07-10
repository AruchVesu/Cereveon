"""User-submitted product feedback — model + HTTP surface.

One authenticated endpoint, ``POST /feedback``, persists a free-form
"Send feedback" message from the Android drawer form to the
``feedback_messages`` table.  Deliberately not part of any coaching or
adaptation path: rows are written for the operator to read (a plain DB
query on the production Postgres) and are never fed back into prompts,
retrieval, or any SECA decision logic — SECA freeze policy applies to
this package like any other ``llm.seca.*`` module.
"""
