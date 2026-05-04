// coach-variant-atrium.jsx
// Variation B — "Atrium"
// Scholarly undertone at the front: big Cormorant title, ornamental rule,
// generous whitespace. Neon is restrained — cyan only on the eval band and
// tiny telemetry. Coach copy reads like a paragraph in a book.

function CoachVariantAtrium({ boardStyle = 'flat' }) {
  const accent = '#4fd9e5';
  const danger = '#ffc069';
  const ink    = '#f4efe1';

  return (
    <div style={{
      position: 'relative',
      width: '100%', height: '100%',
      background: 'radial-gradient(ellipse at 50% 100%, #16141f 0%, #0a0a10 70%)',
      color: ink,
      fontFamily: 'Inter, system-ui, sans-serif',
      overflow: 'hidden',
    }}>
      {/* soft vignette grain */}
      <div aria-hidden style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'radial-gradient(ellipse at 50% 40%, transparent 30%, rgba(0,0,0,0.5) 100%)',
      }} />

      {/* ─── Chapter header ─────────────────────────────────────────── */}
      <div style={{ padding: '16px 20px 8px', position: 'relative' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 9, letterSpacing: 2.4, color: '#6b7080',
          textTransform: 'uppercase',
        }}>Chapter IX · Move 14</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif',
          fontStyle: 'italic', fontWeight: 500,
          fontSize: 28, lineHeight: 1.1, letterSpacing: 0.3,
          color: ink, marginTop: 2,
        }}>The Pin</div>
        <div style={{ marginTop: 10 }}>
          <HairlineRule ornament color="rgba(255,255,255,0.12)" />
        </div>
      </div>

      {/* ─── Player rail (top = opponent) ───────────────────────────── */}
      <div style={{
        padding: '8px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        fontSize: 11,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: danger, boxShadow: `0 0 6px ${danger}`,
          }} />
          <span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 15, color: ink }}>
            Opponent
          </span>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: '#6b7080', letterSpacing: 1 }}>
            1680
          </span>
        </div>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: danger, letterSpacing: 0.5 }}>
          14:03
        </span>
      </div>

      {/* ─── Board ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'center', padding: '2px 16px 2px' }}>
        <div style={{
          padding: 8,
          borderRadius: 2,
          background: '#0d0e14',
          boxShadow: '0 10px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)',
          position: 'relative',
        }}>
          <CoachBoard
            size={332}
            variant={boardStyle}
            accent={accent}
            danger={danger}
            palette={{ light: '#302c24', dark: '#1a1712', border: 'rgba(255,255,255,0.04)' }}
            showHintFocus={[2, 5]}
          />
        </div>
      </div>

      {/* ─── Player rail bottom ─────────────────────────────────────── */}
      <div style={{
        padding: '8px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        fontSize: 11,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: accent, boxShadow: `0 0 6px ${accent}`,
          }} />
          <span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 15, color: ink }}>
            You
          </span>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: '#6b7080', letterSpacing: 1 }}>
            1720 · white
          </span>
        </div>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: accent, letterSpacing: 0.5 }}>
          14:21
        </span>
      </div>

      {/* ─── Eval band ──────────────────────────────────────────────── */}
      <div style={{ padding: '6px 24px 12px' }}>
        <EvalBand band="better" side="white" accent={accent} danger={danger} />
      </div>

      {/* ─── Coach paragraph ────────────────────────────────────────── */}
      <div style={{
        padding: '4px 24px 80px',
        position: 'relative',
      }}>
        {/* Drop cap + prose */}
        <div style={{
          fontFamily: 'Cormorant Garamond, serif',
          fontSize: 17, lineHeight: 1.45, color: ink,
          textWrap: 'pretty',
          fontWeight: 400,
        }}>
          <span style={{
            float: 'left',
            fontSize: 44, lineHeight: 0.85, paddingRight: 6, paddingTop: 4,
            fontStyle: 'italic', fontWeight: 500,
            color: accent,
            textShadow: `0 0 12px ${accent}66`,
          }}>N</span>
          otice how your pieces are working together here — the bishop on&nbsp;g5
          quietly constrains Black's knight, and the central pawn holds
          the space you've been building toward.
        </div>

        <div style={{ marginTop: 14 }}>
          <HairlineRule color="rgba(255,255,255,0.08)" />
        </div>
        <div style={{
          marginTop: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 9, letterSpacing: 2, color: '#6b7080',
          textTransform: 'uppercase',
        }}>
          <span>Theme · pin</span>
          <span style={{ color: accent }}>Band · better</span>
        </div>
      </div>

      {/* ─── Footer action ──────────────────────────────────────────── */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8, alignItems: 'center',
      }}>
        <button style={{
          flex: 1, height: 44,
          border: `1px solid ${accent}55`,
          background: 'transparent',
          color: accent,
          fontFamily: 'Cormorant Garamond, serif',
          fontStyle: 'italic', fontSize: 16, letterSpacing: 0.5,
          borderRadius: 2, cursor: 'pointer',
        }}>Ask the coach</button>
        <button style={{
          width: 44, height: 44,
          border: '1px solid rgba(255,255,255,0.1)',
          background: 'transparent',
          color: '#c8ccda',
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 20,
          borderRadius: 2, cursor: 'pointer',
        }}>?</button>
      </div>
    </div>
  );
}

Object.assign(window, { CoachVariantAtrium });
