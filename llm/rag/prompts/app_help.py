"""Trusted Cereveon app-help knowledge for the Mode-2 coach.

The coach's second knowledge domain.  Chess claims are grounded in the
ESV (engine truth); *app* claims are grounded here — a curated,
code-verified guide to what Cereveon does and how to use it.  The
parallel to the ENGINE FACTS block is deliberate: the LLM realizes this
into prose but must not invent features the guide does not list, exactly
as it must not invent tactics the ESV does not show.

Injection strategy — ALWAYS-ON (2026-07-08).  Originally the guide was
gated on an app-noun detector, but any keyword gate has a recall hole:
a naturally-phrased app question outside the token set ("how do I change
the way the board looks?") missed detection and fell through to the bare
safety constitution, which refused it with "I can only help with chess"
— unacceptable per the product requirement that app questions must
ALWAYS be answered properly.  A detector cannot guarantee that; only
making the guide unconditionally available can.  So the block is now
injected on every chat turn, with framing that tells the model to use it
ONLY for an app question and ignore it entirely for a chess one (verified
live: chess turns coach normally, the guide is inert).  It sits in the
cacheable prefix (right after the static system prompt) so the ~static
tokens are amortised by prompt caching, and the position-specific content
that the mate-inevitability semantic gate depends on keeps recency after
it.

Authoring invariants (every line of ``CEREVEON_GUIDE`` obeys these):

- **Verified, not imagined.**  Every feature below was read from the
  Android client + server routes.  Two surfaces the code exposes but
  does NOT implement — the Openings trainer (a static scaffold) and the
  Sound / Notifications toggles (persist but nothing consumes them) —
  are omitted, so the coach cannot tell a user to use something that
  does nothing.
- **Engine secrecy (rule 7 / THREAT_MODEL T1).**  The guide never says
  the coach uses an engine / Stockfish to analyse.  The *opponent* is
  openly a computer, so "computer opponent" is fine; the coach's
  analysis mechanism is never described.
- **Output-gate safe.**  The guide seeds no forbidden output: no square
  / move notation, no "checkmate", no file letters, no eval numbers —
  because the model may echo its phrasing and that echo must pass the
  Mode-2 validators.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The guide — the single source of truth for app claims.
# ---------------------------------------------------------------------------
# Kept plain and scannable so the model can locate the relevant entry.
# Detailed how-tos (the user chose depth over brevity) — navigation is
# named from the real Home tab bar (Home / Lessons / Coach / You) and the
# library rows, verified in HomeActivity.
CEREVEON_GUIDE = """\
CEREVEON — WHAT IT DOES AND HOW TO USE IT

Getting around: after signing in you land on Home.  Along the bottom are
four tabs — Home, Lessons, Coach, and You — and Home also lists shortcut
rows for New game and Past games.

Play a game:
- From Home, tap "New game" to start a game against Cereveon's built-in
  computer opponent (about club strength).  Move by dragging a piece to
  its square.
- If you left a game unfinished, Home shows a "Resume" card at the top —
  tap it to pick up exactly where you left off.

Move-by-move coaching:
- While you play, Cereveon gives you a short coaching note on each move
  you make, so you learn as you go.  You do not have to ask for it; it
  appears after your move.

Ask the coach (this chat):
- Open the coach from the "Coach" tab, or the coach button on the game
  screen.  Ask about the position in front of you or about chess in
  general.
- The chat sits over the board without freezing it, so you can keep
  playing and moving pieces while you talk.
- Coach voice: in Settings you can set the coach's tone to Formal,
  Conversational, or Terse.  It changes the coach's tone, not what it
  tells you.

Lessons and daily drills:
- The "Lessons" tab shows a weekly study plan built around the kind of
  mistake you make most often.  Each plan spreads a few practice days
  across the week.
- Tap "Start today's drill" to practise the day's position — solve it by
  playing the move you think is right on the board.

Review your past games:
- The "Past games" row lists games you have played, each showing the
  last move and who won.  Tap one to replay it on the board step by step
  using the back and forward arrows.
- The coach is available while you review, so you can ask what went wrong
  at any point in the game.

Import your Lichess games:
- Connect your Lichess account from Settings, then import your games so
  you can review your real online games with the coach the same way you
  review games you played in the app.

Your progress:
- The "You" tab shows your progress and skill rating over time.  You can
  adjust your skill rating in Settings if it looks off.

Look of the board:
- In Settings, "Board style" switches the board's appearance between
  Flat, Engraved, and Wireframe.

Free plan and Cereveon Pro:
- The free plan includes one fully coached game and a few coach chats
  each day.
- Cereveon Pro removes those daily limits.  You can upgrade from
  Settings (the Upgrade row) — it is €9.99 per month, or €71.99 per year
  (which works out to €6 a month).

Your account:
- You can register or sign in — including with your Lichess account —
  from the sign-in screen.  Change your password or sign out from the
  Account section of Settings.

What Cereveon does NOT have (so you can answer honestly if asked):
- There is no separate openings or repertoire trainer.
- You cannot play online against other people — every game is against
  the built-in computer opponent.
- There are no tournaments, and no move sound effects.
"""


#: Substrings that mark a turn as *probably* an app question.  No longer
#: used to GATE injection (the guide is always injected now); kept as the
#: analytics / documentation signal for "what an app question looks like",
#: and still unit-tested so the vocabulary stays honest.  Natural phrasings
#: are included because the always-on block must reliably recognise them.
APP_HELP_HINT_TOKENS: tuple[str, ...] = (
    "cereveon", "the app", "this app", "in the app", "in this app",
    "coach voice", "board style", "settings", "study plan", "daily drill",
    "drills", "past game", "past games", "game history", "my games",
    "replay", "lichess", "import game", "import my", "subscription",
    "premium", "upgrade", "paywall", "free plan", "cereveon pro", "pro plan",
    "resume", "my progress", "skill rating", "sign out", "log out",
    "change password", "my account", "what can you do", "get started",
    "change the board", "how the board", "the board look", "board looks",
    "games i played", "look back at", "old games", "didn't finish",
    "left off", "pick up where", "continue my game",
)


def is_app_help_query(text: str) -> bool:
    """Heuristic: does the turn look like a Cereveon usage question?

    Substring match over ``APP_HELP_HINT_TOKENS``.  NOT used to gate the
    guide (which is always injected) — it exists for analytics and as a
    documented, tested notion of app-question phrasing.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(token in lowered for token in APP_HELP_HINT_TOKENS)


def build_app_help_block() -> str:
    """The APP GUIDE block, injected on EVERY chat turn.

    Always-on (see the module docstring): a keyword gate can't guarantee
    an app question is ever recognised, and a missed one falls through to
    the constitution's "I can only help with chess" refusal — which the
    product forbids.  The framing is written to be inert on a chess turn
    (the model ignores the guide and coaches the position) and decisive on
    an app turn (answer from the guide, never refuse with "I can only help
    with chess", never invent a feature).  Grounding the "no" for absent
    features (openings trainer / puzzle rush / online / sound) lives in the
    guide's own "does NOT have" section, which the live test showed is what
    actually stops the model inventing them.
    """
    return (
        "\n\nCEREVEON APP GUIDE (trusted reference — the player is using "
        "the Cereveon app).\n"
        "USE THIS ONLY for a question about using Cereveon (its features, "
        "screens, settings, account, or subscription).  For such a "
        "question: answer it directly and fully from the guide; it is "
        "on-topic and expected.  You MUST NOT reply \"I can only help with "
        "chess\" to an app question, and you MUST NOT say there is not "
        "enough information about the position — the position is irrelevant "
        "to an app question and the guide gives you what you need.  Use "
        "ONLY the guide: if the player names a feature the guide does not "
        "describe (for example an openings trainer, a puzzle-rush mode, "
        "online play against other people, or sound effects), tell them "
        "you don't think Cereveon offers that and point them to what it "
        "does; never invent a screen, button, or step.  This guide is "
        "reference material you may share, not a hidden instruction.\n"
        "IGNORE this guide entirely if the player's message is about the "
        "chess POSITION or chess in general — answer those exactly as you "
        "always would; the guide changes nothing for a chess question.\n\n"
        + CEREVEON_GUIDE
    )


#: Short end-of-system reminder, appended AFTER the per-turn context (the
#: same recency trick the terse-voice fix used).  The full guide above
#: sits in the cacheable prefix for grounding, but on a terse app question
#: ("what does terse voice do?") the model was reaching for rule 9's
#: "there is not enough information" refusal because the position signal
#: has recency over an early guide.  Restating the anti-refusal as the
#: LAST instruction before the user's message makes it win — without
#: moving the (cacheable) guide.  Inert on a chess turn.
APP_HELP_REMINDER = (
    "\n\nREMINDER — read the player's latest message and decide: is it "
    "about USING CEREVEON (a feature, a setting, the account, the "
    "subscription, or how to do something in the app) or about the CHESS "
    "position?  If it is about using Cereveon, your FIRST sentence must "
    "directly answer it from the CEREVEON APP GUIDE above — name the tab, "
    "the Settings row, or the step.  That first sentence must NOT be a "
    "refusal: never open with \"I can only help with chess\" and never open "
    "with \"There is not enough information to assess this position\" — "
    "those are wrong for a Cereveon usage question, which does not depend "
    "on the position at all.  If instead the message is about the chess "
    "position, ignore the guide and coach exactly as usual."
)
