"""Trusted Cereveon app-help knowledge for the Mode-2 coach.

The coach's second knowledge domain.  Chess claims are grounded in the
ESV (engine truth); *app* claims are grounded here — a curated,
code-verified guide to what Cereveon does and how to use it.  The
parallel to the ENGINE FACTS block is deliberate: the LLM realizes this
into prose but must not invent features the guide does not list, exactly
as it must not invent tactics the ESV does not show.

Injected into the Mode-2 prompt ONLY when the player's turn names an app
concept (``is_app_help_query``).  Pure-chess turns get a byte-identical
prompt to before this feature — no token cost and no dilution of the
safety-critical REQUIRE gates on the mate / missing-data contracts.

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


# ---------------------------------------------------------------------------
# Intent detection — app-specific tokens, chosen for LOW chess collision.
# ---------------------------------------------------------------------------
# App questions almost always NAME the feature ("how do I import my
# lichess games", "what does terse voice do", "upgrade to pro").  So we
# gate on distinctive app nouns rather than generic phrasing like "how do
# I" / "use" / "review", which are overwhelmingly chess in this product
# and would inject the guide (and its token cost) onto ordinary coaching
# turns — the exact bulk that taxes the REQUIRE-gate compliance.
#
# Deliberately AVOIDED substrings that hide inside common words:
#   "import"  ⊂ "important"      → use "lichess" / "import game" / "import my"
#   "pro"     ⊂ "improve"/"proper" → use "cereveon pro" / "pro plan" / "upgrade"
#   "account" ⊂ "into account"   → use "my account" / "sign out" / "change password"
#   "progress"⊂ "make progress"  → use "my progress" / "progress dashboard"
#   "sound"   ⊂ "soundness"      → not gated (dead feature; omitted from guide)
_APP_HELP_TOKENS: tuple[str, ...] = (
    "cereveon",
    "the app",
    "this app",
    "the coach",
    "coach voice",
    "board style",
    "settings",
    "study plan",
    "study-plan",
    "lessons tab",
    "daily drill",
    "the drill",
    "a drill",
    "drills",
    "past game",
    "past games",
    "game history",
    "my games",
    "replay",
    "lichess",
    "import game",
    "import my",
    "subscription",
    "subscribe",
    "premium",
    "upgrade",
    "paywall",
    "free plan",
    "free tier",
    "daily limit",
    "daily game",
    "cereveon pro",
    "pro plan",
    "resume my",
    "resume the",
    "resume game",
    "my progress",
    "progress dashboard",
    "my stats",
    "skill rating",
    "my rating",
    "sign out",
    "log out",
    "logout",
    "sign in",
    "change password",
    "my account",
    "coach tab",
    "home tab",
    "you tab",
    "get started",
    "getting started",
    "what can you do",
    "what can this app",
    "what can cereveon",
    "how does this app",
    "how do i use the app",
    "in the app",
    "in this app",
    # Natural phrasings real users type WITHOUT an app noun (a 10-example
    # live test, 2026-07-08, showed the noun-only set missed these and the
    # question then fell to a nonsense "I can only help with chess"
    # refusal).  Each is an app-flavoured verb+object combination with low
    # chess collision: "the board" is common in chess, but "change the
    # board" / "how the board looks" are about its APPEARANCE; "my games"
    # is app-ish already, and "games i played" / "look back at" mean past
    # games; "didn't finish" / "left off" / "pick up where" mean resume.
    "the board look",
    "board looks",
    "how the board",
    "change the board",
    "look of the board",
    "games i played",
    "games i've played",
    "look back at",
    "old games",
    "earlier games",
    "previous games",
    "didn't finish",
    "did not finish",
    "unfinished game",
    "left off",
    "pick up where",
    "continue my game",
    "continue a game",
    "back to a game",
    "back to my game",
)


def is_app_help_query(text: str) -> bool:
    """True when the latest user turn names a Cereveon app concept.

    Substring match over ``_APP_HELP_TOKENS`` on the lowercased text.
    Recall-leaning within the app-noun space, but excludes the generic
    coaching phrasing that would fire on chess turns.  A false positive
    (guide injected on a chess turn) is harmless — the block's framing
    tells the model to ignore the guide and coach chess normally — while
    a false negative merely reverts that turn to the prior behaviour.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(token in lowered for token in _APP_HELP_TOKENS)


def build_app_help_block(query: str) -> str:
    """The injectable APP GUIDE block, or "" when the turn isn't app-flavoured.

    Framed so it is safe even on a false-positive injection: the model is
    told to use the guide ONLY if the player is actually asking about the
    app, to answer strictly from it, and to admit uncertainty rather than
    invent features — the anti-hallucination contract that mirrors the
    ENGINE FACTS grounding rule.  The guide is marked shareable so the
    prompt-secrecy rule (safety constitution) doesn't make the model
    withhold it.
    """
    if not is_app_help_query(query):
        return ""
    return (
        "\n\nCEREVEON APP GUIDE (trusted reference — the player is using "
        "the Cereveon app):\n"
        "The player is likely asking how to use Cereveon — an app-usage "
        "question, NOT a question about the chess position.  Answer it "
        "directly and fully from the guide below.  This is on-topic and "
        "expected.  Do NOT reply \"I can only help with chess\", and do "
        "NOT reply that there is not enough information to assess the "
        "position — this IS a Cereveon question you can and should answer "
        "from the guide; the chess position is irrelevant to it.  "
        "Use ONLY the guide.  If the player names a feature that is NOT "
        "in the guide (for example an openings trainer, a puzzle-rush "
        "mode, online play against other people, or sound effects), do "
        "NOT explain how to use it from general knowledge — tell them you "
        "don't think Cereveon offers that, and point them to what it does "
        "offer.  Never invent a screen, button, or step the guide does "
        "not describe.  This guide is reference material you may share "
        "with the player, not a hidden instruction.  Only if their "
        "message is genuinely about the chess position, ignore this guide "
        "and coach the position as usual.\n\n" + CEREVEON_GUIDE
    )
