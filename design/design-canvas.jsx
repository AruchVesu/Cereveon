// coach-variant-obsidian.jsx
// Variation A — "Obsidian Console"
// Dense cyberpunk with a scholarly serif. Neon cyan filaments.
// The coach commentary sits in a glassy card over a subtle scanline field.

function CoachVariantObsidian({ boardStyle = 'flat' }) {
  const accent = '#4fd9e5';  // neon cyan — player / data
  const danger = '#ffc069';  // amber — opponent / warnings
  const bg = '#0a0c13';

  return (
    <div style={{
      position: 'relative',
      width: '100%', height: '100%',
      background: `radial-gradient(ellipse at 50% 0%, #141828 0%, ${bg} 60%)`,
      color: '#e6e2d6',
      fontFamily: 'Inter, system-ui, sans-serif',
      overflow: 'hidden',
    }}>
      {/* Scanline atmosphere */}
      <div aria-hidden style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'repeating-linear-gradient(0deg, transparent 0, transparent 2px, rgba(255,255,255,0.012) 2px, rgba(255,255,255,0.012) 3px)',
        opacity: 0.7, mixBlendMode: 'overlay',
      }} />

      {/* ─── Header ──────────────────────────────────────────────────── */}
      <div style={{
        padding: '14px 16px 10px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif',
            fontStyle: 'italic', fontWeight: 500,
            fontSize: 22, letterSpacing: 0.5, color: '#f4efe1',
            lineHeight: 1,
          }}>Cereveon</div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 8, letterSpacing: 2.2, color: accent,
            border: `1px solid ${accent}55`, padding: '2px 5px', borderRadius: 2,
            textTransform: 'uppercase',
          }}>Mode&nbsp;II</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 10, color: '#7a8094', letterSpacing: 1,
          }}>ELO·1720</div>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: accent, boxShadow: `0 0 8px ${accent}`,
            animation: 'cv-pulse 2.2s ease-in-out infinite',
          }} />
        </div>
      </div>

      {/* ─── Opponent strip ──────────────────────────────────────────── */}
      <div style={{
        padding: '10px 16px 8px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 30, height: 30, borderRadius: '50%',
            background: `radial-gradient(circle at 35% 30%, ${danger}33, transparent 70%), #1a1410`,
            border: `1px solid ${danger}44`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'Cormorant Garamond, serif',
            fontSize: 16, color: danger, fontStyle: 'italic',
          }}>Σ</div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, color: '#f4efe1' }}>Opponent</div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: '#7a8094', letterSpacing: 1 }}>
              ~1680 ELO · adaptive
            </div>
          </div>
        </div>
        <CapturedRow pieces={['p', 'p']} side="white" size={14} />
        <div style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 13, fontWeight: 500, color: danger,
          letterSpacing: 0.5,
        }}>14:03</div>
      </div>

      {/* ─── Board frame ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 12px' }}>
        <div style={{
          position: 'relative',
          padding: 10,
          borderRadius: 6,
          background: 'linear-gradient(180deg, #10131c, #0a0c13)',
          boxShadow: `inset 0 0 0 1px rgba(255,255,255,0.05), 0 0 40px ${accent}11`,
        }}>
          {/* corner brackets */}
          {[[0,0],[0,1],[1,0],[1,1]].map(([r,c],i) => (
            <div key={i} style={{
              position: 'absolute',
              [r ? 'bottom' : 'top']: -1,
              [c ? 'right' : 'left']: -1,
              width: 14, height: 14,
              borderTop: !r ? `1px solid ${accent}` : undefined,
              borderBottom: r ? `1px solid ${accent}` : undefined,
              borderLeft: !c ? `1px solid ${accent}` : undefined,
              borderRight: c ? `1px solid ${accent}` : undefined,
              opacity: 0.7,
            }} />
          ))}
          <CoachBoard
            size={340}
            variant={boardStyle}
            accent={accent}
            danger={danger}
            showHintFocus={[2, 5]}  /* commentary spotlight on f6 — the pinned knight */
          />
        </div>
      </div>

      {/* ─── Player strip ───────────────────────────────────────────── */}
      <div style={{
        padding: '8px 16px 10px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 30, height: 30, borderRadius: '50%',
            background: `radial-gradient(circle at 35% 30%, ${accent}33, transparent 70%), #0f1828`,
            border: `1px solid ${accent}44`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'Cormorant Garamond, serif',
            fontSize: 14, color: accent, fontWeight: 600,
          }}>AG</div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, color: '#f4efe1' }}>You · White</div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: '#7a8094', letterSpacing: 1 }}>
              last: Bc1–g5
            </div>
          </div>
        </div>
        <CapturedRow pieces={['p']} side="black" size={14} />
        <div style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 13, fontWeight: 500, color: accent,
          letterSpacing: 0.5,
          textShadow: `0 0 8px ${accent}66`,
        }}>14:21</div>
      </div>

      {/* ─── Eval band ──────────────────────────────────────────────── */}
      <div style={{ padding: '4px 18px 10px' }}>
        <EvalBand band="better" side="white" accent={accent} danger={danger} />
      </div>

      {/* ─── Coach card ─────────────────────────────────────────────── */}
      <div style={{
        margin: '2px 14px 14px',
        borderRadius: 8,
        background: 'linear-gradient(180deg, rgba(20,24,36,0.9), rgba(10,12,19,0.9))',
        border: `1px solid ${accent}22`,
        backdropFilter: 'blur(6px)',
        padding: '12px 14px',
        position: 'relative',
        overflow: 'hidden',
      }}>
        {/* filament on the left edge */}
        <div style={{
          position: 'absolute', left: 0, top: 10, bottom: 10, width: 2,
          background: `linear-gradient(180deg, transparent, ${accent}, transparent)`,
          boxShadow: `0 0 8px ${accent}`,
        }} />
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 6,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CoachMark size={22} color={accent} />
            <span style={{
              fontFamily: 'Cormorant Garamond, serif',
              fontStyle: 'italic',
              fontSize: 14, color: '#f4efe1',
              letterSpacing: 0.3,
            }}>Coach</span>
          </div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 8, letterSpacing: 2, color: '#5e6475',
          }}>LIVE</div>
        </div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif',
          fontSize: 18, lineHeight: 1.35, color: '#f4efe1',
          textWrap: 'pretty',
        }}>
          Notice how your pieces are working together — the bishop on g5 quietly
          constrains Black's knight, and your central pawn holds the space
          you've been building toward.
        </div>
        <div style={{
          marginTop: 10,
          display: 'flex', gap: 6, flexWrap: 'wrap',
        }}>
          {['pin', 'center', 'development'].map(t => (
            <span key={t} style={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 9, letterSpacing: 1.5, textTransform: 'uppercase',
              padding: '3px 7px',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 999,
              color: '#9aa0b4',
            }}>{t}</span>
          ))}
        </div>
      </div>

      {/* ─── Bottom action row ──────────────────────────────────────── */}
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 14,
        display: 'flex', justifyContent: 'center', gap: 8,
        padding: '0 16px',
      }}>
        {[
          { label: 'Ask', primary: true },
          { label: 'Why?', primary: false },
          { label: 'Resign', primary: false },
        ].map(a => (
          <button key={a.label} style={{
            flex: a.primary ? 2 : 1,
            height: 40,
            border: a.primary ? `1px solid ${accent}` : '1px solid rgba(255,255,255,0.08)',
            background: a.primary ? `${accent}15` : 'rgba(255,255,255,0.02)',
            color: a.primary ? accent : '#c8ccda',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 11, letterSpacing: 2, textTransform: 'uppercase',
            borderRadius: 4,
            cursor: 'pointer',
            boxShadow: a.primary ? `0 0 16px ${accent}22, inset 0 0 0 1px ${accent}22` : undefined,
          }}>{a.label}</button>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { CoachVariantObsidian });
