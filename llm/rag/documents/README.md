# RAG document corpus

The deterministic, rule-matched RAG layer for Mode-2 explanations.
The retriever (`llm/rag/retriever/retriever.py`) walks every document
in this directory, evaluates each `conditions:` block against the
current ESV via `matches_conditions`, and returns up to `MAX_DOCS = 7`
matches sorted by `priority.PRIORITY_ORDER`.

## Loader contract

`__init__.py` uses `DOCS_DIR.glob("*.yaml")` — **non-recursive**.

- New documents go at the **top level** of this directory.
- Subdirectories are NOT scanned.  An earlier revision had nested
  `evaluation/`, `move_quality/`, `phase/`, `positional/`, `tactical/`
  subtrees with mostly-empty placeholder files; they were deleted in
  the 2026-05-07 corpus cleanup because the loader never read them.
- If you need a per-topic subdirectory layout in the future, flip the
  loader to `rglob` first and pin the new shape with a test.

## Document schema

Every YAML file under this directory must produce a top-level mapping
with these keys:

```yaml
id: <stable-string>            # used by golden tests + retrieval logs
type: <priority-bucket>         # see priority.PRIORITY_ORDER
conditions:
  <esv-path>: <expected-value> # zero or more; AND-combined
content:
  description: <prose>          # rendered into the prompt verbatim
```

`<esv-path>` uses dot-notation to walk into the ESV dict —
`evaluation.band`, `evaluation.type`, `position_flags`, `tactical_flags`,
`last_move_quality`, etc.  When the ESV value at that path is a list
(`tactical_flags: [hanging_piece, fork]`), the rule matcher treats
the condition as `expected in current` (membership).  When it's a
scalar, the matcher treats it as equality.  See
`llm/rag/retriever/rule_matcher.py:matches_conditions`.

Priority buckets (lower index → earlier in the rendered prompt):

1. `evaluation_translation`
2. `move_quality`
3. `tactical`
4. `positional`
5. `phase_guidance`

## Live corpus inventory (2026-05-07)

Eight documents, mapped to the ESV signatures they fire on.  Reading
down the leftmost column tells you what coaching context the LLM
sees for any given position.

| File | id | type | Triggers when |
|---|---|---|---|
| `eval_cp.yaml` | `eval_cp_decisive_advantage` | evaluation_translation | `evaluation.type == "cp"` AND `evaluation.band == "decisive_advantage"` |
| `evaluation.yaml` | `eval_cp` | evaluation_translation | `evaluation.type == "cp"` AND `evaluation.band == "small_advantage"` |
| `forced_mate.yaml` | `forced_mate` | evaluation_translation | `evaluation.type == "mate"` |
| `move_quality.yaml` | `move_quality_mistake` | move_quality | `last_move_quality == "mistake"` |
| `move_quality_blunder.yaml` | `move_quality_blunder` | move_quality | `last_move_quality == "blunder"` |
| `positional_better_development.yaml` | `positional_better_development` | positional | `"better_development"` in `position_flags` |
| `positional_space_advantage.yaml` | `positional_quiet` | positional | `"space_advantage"` in `position_flags` |
| `tactical.yaml` | `tactic_hanging_piece` | tactical | `"hanging_piece"` in `tactical_flags` |

## Known coverage gaps

Positions whose ESV does NOT match any of the rules above retrieve
the empty list.  `render_mode_2_prompt` substitutes the placeholder
string `(no retrieved context)` and the LLM operates against the
ESV + system prompt alone.  This is the documented "degraded but not
refused" fallback (see `docs/ARCHITECTURE.md` > RAG Retrieval).

The largest gaps in the current corpus, in approximate priority order:

- **No `evaluation.band == "clear_advantage"` document.**  The middle
  band between `small_advantage` and `decisive_advantage` is uncovered;
  `evaluation.type == "cp"` positions in that band fall back to no
  evaluation_translation document.
- **No `evaluation.band == "equal"` document.**  Equal-band positions
  carry an empty evaluation slot.
- **No phase documents at all.**  `phase_guidance` is in the priority
  list but no documents emit it; opening / middlegame / endgame
  positions never retrieve phase-specific context.
- **`last_move_quality == "inaccuracy"` is uncovered** (only `mistake`
  and `blunder` have docs).
- **`tactical_flags`** has only `hanging_piece`; common tactics like
  `fork`, `pin`, `back_rank`, `discovered_check`, `skewer`,
  `double_attack` never trigger a document.
- **`position_flags`** has only `better_development` and
  `space_advantage`; common positional motifs like
  `king_safety_weak`, `open_file`, `weak_pawn`, `bishop_pair`,
  `bad_bishop`, `outpost`, `passed_pawn` never trigger a document.

These gaps don't break anything — `retrieve()` returns `[]`, the
prompt renders `(no retrieved context)`, and the validators still
enforce every safety claim regardless of context richness.  But the
LLM has thinner explanatory material to draw from on those positions,
which lowers the ceiling of explanation quality.

## When to add a document

The default policy is **don't expand the corpus speculatively**.  Add
a document when:

1. Production telemetry shows a sustained empty-retrieval rate on a
   specific ESV signature, or
2. A user-visible quality complaint traces back to a `(no retrieved
   context)` placeholder, or
3. A new ESV field is introduced and needs at least one document so
   the field is reachable from the prompt.

Each new document increases the audit surface (one more rule to
review for correctness) and the snapshot test surface (golden tests
pin the retrieval order; a new doc may shift the rendered prompt).
The "rule-based retrieval is auditable" trade-off in
`docs/ARCHITECTURE.md` > RAG Retrieval is the binding constraint —
embedding-based retrieval would close every gap above but at the cost
of giving up reproducibility.  Adding rule-based docs one at a time
keeps the auditability property intact.

## Reference

- Loader: `llm/rag/documents/__init__.py`
- Retriever: `llm/rag/retriever/retriever.py` (priority + max-docs)
- Rule matcher: `llm/rag/retriever/rule_matcher.py`
- Architectural rationale: `docs/ARCHITECTURE.md` > RAG Retrieval
- Golden test fixtures: `llm/tests/golden/cases/` and `llm/tests/golden/expected_rag/`
