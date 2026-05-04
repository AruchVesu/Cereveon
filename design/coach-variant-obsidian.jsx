// coach-variant-hud.jsx
// Variation C — "HUD Telemetry"
// Cockpit / instrument layout. Data-dense, grid lines, two-tone neon.
// Board is inset in a bracketed frame with telemetry rails on both sides.

function CoachVariantHUD({ boardStyle = 'wireframe' }) {
  const accent = '#4fd9e5';
  const danger = '#ffc069';
  const ink    = '#e6e2d6';

  const Label = ({ children, color = '#6b7080' }) => (
    <div style={{
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 8, letterSpacing: 2, textTransform: 'uppercase',
      color,
    }}>{children}</div>
  );

  const Value = ({ children, color = ink, size = 13 }) => (
    <div style={{
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: size, fontWeight: 500, color, letterSpacing: 0.5,
    }}>{children}</div>
  );

  return (
    <div style={{
      position: 'relative',
      width: '100%', height: '100%',
      background: '#070910',
      color: ink,
      overflow: 'hidden',
    }}>
      {/* grid background */}
      <div aria-hidden style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        backgroundImage: `
          linear-gradient(rgba(79,217,229,0.04) 1px, transparent 1px),
          linear-gradient(90deg, rgba(79,217,229,0.04) 1px, transparent 1px)
        `,
        backgroundSize: '24px 24px',
        maskImage: 'radial-gradient(ellipse at center, black 30%, transparent 80%)',
      }} />

      {/* ─── Top HUD bar ────────────────────────────────────────────── */}
      <div style={{
        padding: '12px 14px 10px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: `1px solid ${accent}18`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 10, height: 10, borderRadius: 1,
            background: accent, boxShadow: `0 0 8px ${accent}`,
          }} />
          <div>
            <Label color={accent}>Cereveon // LIVE</Label>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif',
              fontStyle: 'italic', fontSize: 14, color: ink, marginTop: 1,
            }}>Session 047</div>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <Label>Move · 14</Label>
          <Value size={12}>W to move</Value>
        </div>
      </div>

      {/* ─── Clocks row ─────────────────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        gap: 1, background: `${accent}10`, padding: 1,
      }}>
        <div style={{ background: '#0a0c14', padding: '10px 14px' }}>
          <Label>Opponent · 1680</Label>
          <Value color={danger} size={22}>14:03</Value>
        </div>
        <div style={{ background: '#0a0c14', padding: '10px 14px', textAlign: 'right' }}>
          <Label>You · 1720</Label>
          <Value color={accent} size={22}>14:21</Value>
        </div>
      </div>

      {/* ─── Board + rails ──────────────────────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '22px 1fr 22px',
        alignItems: 'center',
        padding: '14px 10px 10px',
        gap: 8,
      }}>
        {/* left rail — eval band vertical */}
        <div style={{ height: 300, position: 'relative' }}>
          <div style={{
            position: 'absolute', left: '50%', top: 0, bottom: 0,
            width: 2, transform: 'translateX(-50%)',
            background: 'rgba(255,255,255,0.05)',
            borderRadius: 999,
          }}>
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0,
              height: '62%',
              background: `linear-gradient(0deg, ${accent}, transparent)`,
              borderRadius: 999,
            }} />
            <div style={{
              position: 'absolute', bottom: '62%', left: '50%',
              transform: 'translate(-50%, 50%)',
              width: 10, height: 10, borderRadius: '50%',
              background: accent, boxShadow: `0 0 10px ${accent}`,
            }} />
          </div>
          <Label color={accent}>EVAL</Label>
          <div style={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            textAlign: 'center',
          }}>
            <Label>W</Label>
          </div>
        </div>

        {/* board */}
        <div style={{
          position: 'relative',
          padding: 6,
          background: '#05070c',
          border: `1px solid ${accent}22`,
        }}>
          {/* corner ticks */}
          {[[0,0],[0,1],[1,0],[1,1]].map(([r,c], i) => (
            <div key={i} style={{
              position: 'absolute',
              [r ? 'bottom' : 'top']: -4,
              [c ? 'right' : 'left']: -4,
              width: 8, height: 8,
              borderTop: !r ? `1.5px solid ${accent}` : undefined,
              borderBottom: r ? `1.5px solid ${accent}` : undefined,
              borderLeft: !c ? `1.5px solid ${accent}` : undefined,
              borderRight: c ? `1.5px solid ${accent}` : undefined,
            }} />
          ))}
          <CoachBoard
            size={300}
            variant={boardStyle}
            accent={accent}
            danger={danger}
            palette={{ light: '#1a1f2c', dark: '#0d1019', border: 'rgba(79,217,229,0.12)' }}
            showHintFocus={[2, 5]}
          />
        </div>

        {/* right rail — captured stack */}
        <div style={{ height: 300, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
          <Label>TAKE</Label>
          <div style={{
            display: 'flex', flexDirection: 'column', gap: 2,
            padding: '6px 2px',
            border: `1px solid ${accent}18`,
          }}>
            {['p','p','p','n'].map((p, i) => (
              <span key={i} className="piece" style={{
                fontSize: 14, color: '#0a0c11',
                filter: 'invert(0.9) sepia(0.5) hue-rotate(150deg) brightness(1.3)',
                lineHeight: 1,
              }}>{CV_GLYPH[p]}</span>
            ))}
          </div>
        </div>
      </div>

      {/* ─── Telemetry strip (ESV bands, not centipawns) ────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        borderTop: `1px solid ${accent}18`,
        borderBottom: `1px solid ${accent}18`,
        background: '#080a12',
      }}>
        {[
          { k: 'BAND',   v: 'BETTER', c: accent },
          { k: 'PHASE',  v: 'MIDGM',  c: ink    },
          { k: 'THEME',  v: 'PIN',    c: ink    },
          { k: 'TEMPO',  v: '+1',     c: accent },
        ].map((m, i) => (
          <div key={m.k} style={{
            padding: '8px 10px',
            borderRight: i < 3 ? `1px solid ${accent}10` : undefined,
          }}>
            <Label>{m.k}</Label>
            <Value color={m.c} size={13}>{m.v}</Value>
          </div>
        ))}
      </div>

      {/* ─── Commentary ─────────────────────────────────────────────── */}
      <div style={{
        padding: '12px 14px 0',
        display: 'flex', gap: 10,
      }}>
        <div style={{
          width: 3, flexShrink: 0, borderRadius: 999,
          background: `linear-gradient(180deg, ${accent}, transparent)`,
          boxShadow: `0 0 6px ${accent}`,
        }} />
        <div style={{ flex: 1 }}>
          <Label color={accent}>Coach // tutor voice</Label>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif',
            fontSize: 16, lineHeight: 1.35, color: ink,
            marginTop: 4, textWrap: 'pretty',
          }}>
            Notice how your pieces are working together — the bishop pins the
            knight, and your central pawn holds the space.
          </div>
        </div>
      </div>

      {/* ─── Bottom controls ────────────────────────────────────────── */}
      <div style={{
        position: 'absolute', left: 12, right: 12, bottom: 14,
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
        gap: 4,
      }}>
        {[
          { l: 'EXPLAIN', c: accent, on: true },
          { l: 'ASK',     c: ink,    on: false },
          { l: 'OFFER ½', c: danger, on: false },
        ].map(b => (
          <button key={b.l} style={{
            height: 36,
            border: `1px solid ${b.on ? b.c : 'rgba(255,255,255,0.08)'}`,
            background: b.on ? `${b.c}15` : 'transparent',
            color: b.c,
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 10, letterSpacing: 2, textTransform: 'uppercase',
            cursor: 'pointer',
            boxShadow: b.on ? `0 0 10px ${b.c}22, inset 0 0 0 1px ${b.c}22` : undefined,
          }}>{b.l}</button>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { CoachVariantHUD });
