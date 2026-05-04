# Mode-2 Output Contract

The Mode-2 response MUST:

## Content rules
- Be based solely on:
  - engine signal
  - retrieved RAG context
- Never invent moves, tactics, or evaluations
- Never contradict engine evaluation
- Never mention engines or calculation

## Structural rules
- Use natural language prose
- No move notation
- No bullet-point calculation trees
- No numeric engine values

## Behavioral rules
- Use calm, instructional tone
- Do not assume player intent
- Avoid absolute claims unless evaluation.type = "mate"

## Special cases

### Forced mate
- Emphasize inevitability
- Stress urgency
- Avoid long-term planning

### Missing data
- Explicitly state that required information is missing
- Do not guess or speculate

## Forbidden content
- Move suggestions
- Variations
- Engine terminology
- “Best move”, “Stockfish”, “depth”
