// atrium-screens.jsx — adjacent screens in the Atrium language
// Post-move analysis · Coach chat · Game-end summary · Onboarding
// Shares tokens + atoms with coach-common.jsx. All screens live inside
// a 412×892 Android frame and reuse <CoachBoard>, <HairlineRule>, <EvalBand>.

const AT = {
  bg:     'radial-gradient(ellipse at 50% 100%, #16141f 0%, #0a0a10 70%)',
  ink:    '#f4efe1',
  muted:  '#9aa0b4',
  dim:    '#6b7080',
  accent: '#4fd9e5',
  danger: '#ffc069',
  hairline: 'rgba(255,255,255,0.08)',
};

// ── Shared chrome atoms ──────────────────────────────────────────────

function AtriumShell({ children, style }) {
  return (
    <div style={{
      position: 'relative', width: '100%', height: '100%',
      background: AT.bg, color: AT.ink,
      fontFamily: 'Inter, system-ui, sans-serif',
      overflow: 'hidden',
      ...style,
    }}>
      <div aria-hidden style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'radial-gradient(ellipse at 50% 40%, transparent 30%, rgba(0,0,0,0.5) 100%)',
      }} />
      {children}
    </div>
  );
}

function ChapterHeader({ kicker, title, back = true }) {
  return (
    <div style={{ padding: '16px 20px 8px', position: 'relative' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        {back && (
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 22, color: AT.muted, cursor: 'pointer', lineHeight: 1,
          }}>←</div>
        )}
        <div style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 9, letterSpacing: 2.4, color: AT.dim,
          textTransform: 'uppercase',
        }}>{kicker}</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 22, color: AT.muted, cursor: 'pointer', lineHeight: 1,
        }}>⋯</div>
      </div>
      <div style={{
        fontFamily: 'Cormorant Garamond, serif',
        fontStyle: 'italic', fontWeight: 500,
        fontSize: 28, lineHeight: 1.1, letterSpacing: 0.3,
        color: AT.ink, marginTop: 6, textAlign: 'center',
      }}>{title}</div>
      <div style={{ marginTop: 10 }}>
        <HairlineRule ornament color="rgba(255,255,255,0.12)" />
      </div>
    </div>
  );
}

function DropCapProse({ first, rest, color = AT.accent }) {
  return (
    <div style={{
      fontFamily: 'Cormorant Garamond, serif',
      fontSize: 17, lineHeight: 1.45, color: AT.ink,
      textWrap: 'pretty', fontWeight: 400,
    }}>
      <span style={{
        float: 'left', fontSize: 44, lineHeight: 0.85,
        paddingRight: 6, paddingTop: 4,
        fontStyle: 'italic', fontWeight: 500,
        color, textShadow: `0 0 12px ${color}66`,
      }}>{first}</span>
      {rest}
    </div>
  );
}

function AtriumButton({ label, primary, wide, icon }) {
  return (
    <button style={{
      flex: wide ? 1 : undefined,
      height: 44,
      padding: wide ? undefined : '0 18px',
      border: primary ? `1px solid ${AT.accent}55` : `1px solid rgba(255,255,255,0.1)`,
      background: 'transparent',
      color: primary ? AT.accent : AT.ink,
      fontFamily: 'Cormorant Garamond, serif',
      fontStyle: 'italic', fontSize: 16, letterSpacing: 0.5,
      borderRadius: 2, cursor: 'pointer',
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    }}>
      {icon && <span style={{ fontSize: 14 }}>{icon}</span>}
      {label}
    </button>
  );
}

// ─── SCREEN 1 — Post-move analysis ───────────────────────────────────
// After your move, the coach breaks down what happened. Shows:
// tiny board thumbnail + move notation, eval band, prose analysis,
// a "themes" tag cluster, and a footer CTA to continue.
function AtriumAnalysis({ boardStyle = 'flat' }) {
  return (
    <AtriumShell>
      <ChapterHeader kicker="Analysis · Move 14" title="Bg5 · The Pin" />

      {/* Small board thumbnail + move notation */}
      <div style={{
        padding: '12px 20px 8px',
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <div style={{
          padding: 4, background: '#0d0e14',
          boxShadow: '0 6px 20px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)',
        }}>
          <CoachBoard
            size={120} variant={boardStyle}
            accent={AT.accent} danger={AT.danger}
            palette={{ light: '#302c24', dark: '#1a1712', border: 'rgba(255,255,255,0.04)' }}
            showCoords={false}
            showHintFocus={[2, 5]}
          />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
            letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
          }}>Your move</div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 28,
            color: AT.ink, marginTop: 2,
            textShadow: `0 0 10px ${AT.accent}22`,
          }}>Bg5</div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
            color: AT.dim, letterSpacing: 1, marginTop: 4,
          }}>14.8s · library line</div>
        </div>
      </div>

      {/* Eval + quality callout */}
      <div style={{ padding: '8px 24px 14px' }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 6,
        }}>
          <span>Position</span>
          <span style={{ color: AT.accent }}>Better · holding</span>
        </div>
        <EvalBand band="better" side="white" accent={AT.accent} danger={AT.danger} compact />
      </div>

      <div style={{ padding: '0 24px 6px' }}>
        <HairlineRule color={AT.hairline} />
      </div>

      {/* Prose */}
      <div style={{ padding: '12px 24px 0' }}>
        <DropCapProse
          first="T"
          rest={<>his is a patient, positional move. The bishop doesn't attack — it <em style={{ color: AT.accent, fontStyle: 'italic' }}>constrains</em>. Black's knight on&nbsp;f6 now carries the weight of the king behind it, and you have the time to develop the rest of your army without incident.</>}
        />
      </div>

      {/* Themes */}
      <div style={{ padding: '16px 24px 0' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 8,
        }}>Themes studied</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {[
            { k: 'pin',           active: true  },
            { k: 'development',   active: true  },
            { k: 'king safety',   active: false },
            { k: 'central space', active: false },
          ].map(t => (
            <span key={t.k} style={{
              fontFamily: 'Cormorant Garamond, serif',
              fontStyle: 'italic', fontSize: 13,
              padding: '4px 10px',
              border: `1px solid ${t.active ? AT.accent + '55' : 'rgba(255,255,255,0.08)'}`,
              color: t.active ? AT.accent : AT.muted,
              borderRadius: 2,
            }}>{t.k}</span>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8,
      }}>
        <AtriumButton wide label="Continue game" primary />
        <AtriumButton label="Ask why" icon="?" />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 2 — Coach chat ───────────────────────────────────────────
function AtriumChat() {
  const msgs = [
    { who: 'you',   text: 'Why did the bishop move stop Black\u2019s plans?' },
    { who: 'coach', text: 'The bishop doesn\u2019t attack anything directly \u2014 but the knight it faces can no longer move without exposing the king. Black\u2019s kingside becomes quiet, and you gain time.' },
    { who: 'you',   text: 'So it\u2019s about tempo, not threats?' },
    { who: 'coach', text: 'Exactly. In positions like this, restraint is often the strongest move. The pieces already working for you don\u2019t need to do more \u2014 they need to keep doing what they\u2019re doing.' },
  ];

  return (
    <AtriumShell>
      <ChapterHeader kicker="Dialogue" title="With the coach" />

      {/* Context chip */}
      <div style={{
        padding: '6px 20px 2px',
        display: 'flex', justifyContent: 'center',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '4px 10px',
          border: `1px solid ${AT.hairline}`,
          borderRadius: 2,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: AT.accent, boxShadow: `0 0 6px ${AT.accent}`,
          }} />
          <span style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
            letterSpacing: 1.6, color: AT.dim, textTransform: 'uppercase',
          }}>Discussing · move 14 · Bg5</span>
        </div>
      </div>

      {/* Messages */}
      <div style={{
        position: 'absolute', top: 140, bottom: 86,
        left: 0, right: 0,
        padding: '14px 20px',
        overflowY: 'auto',
        display: 'flex', flexDirection: 'column', gap: 18,
      }}>
        {msgs.map((m, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: m.who === 'you' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{ maxWidth: '82%' }}>
              <div style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                letterSpacing: 2, color: m.who === 'coach' ? AT.accent : AT.dim,
                textTransform: 'uppercase', marginBottom: 4,
                textAlign: m.who === 'you' ? 'right' : 'left',
              }}>{m.who === 'coach' ? 'Coach' : 'You'}</div>

              {m.who === 'coach' ? (
                <div style={{
                  fontFamily: 'Cormorant Garamond, serif',
                  fontSize: 16, lineHeight: 1.4, color: AT.ink,
                  paddingLeft: 12, position: 'relative',
                  textWrap: 'pretty',
                }}>
                  <div style={{
                    position: 'absolute', left: 0, top: 4, bottom: 4, width: 1,
                    background: `linear-gradient(180deg, ${AT.accent}, transparent)`,
                    boxShadow: `0 0 4px ${AT.accent}66`,
                  }} />
                  {m.text}
                </div>
              ) : (
                <div style={{
                  fontFamily: 'Inter, sans-serif',
                  fontSize: 14, lineHeight: 1.4,
                  color: AT.muted, textAlign: 'right',
                  fontStyle: 'italic',
                }}>{m.text}</div>
              )}
            </div>
          </div>
        ))}

        {/* typing */}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', paddingLeft: 12 }}>
          {[0, 1, 2].map(i => (
            <span key={i} style={{
              width: 5, height: 5, borderRadius: '50%',
              background: AT.accent,
              boxShadow: `0 0 6px ${AT.accent}`,
              animation: `cv-pulse 1.2s ease-in-out ${i * 0.15}s infinite`,
            }} />
          ))}
        </div>
      </div>

      {/* Composer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8, alignItems: 'center',
        padding: '8px 10px 8px 14px',
        border: `1px solid ${AT.hairline}`,
        background: 'rgba(255,255,255,0.02)',
        borderRadius: 2,
      }}>
        <span style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 15, color: AT.muted, flex: 1,
        }}>Ask the coach…</span>
        <button style={{
          width: 32, height: 32,
          border: `1px solid ${AT.accent}55`,
          background: `${AT.accent}10`,
          color: AT.accent,
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 16, borderRadius: 2, cursor: 'pointer',
        }}>→</button>
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 3 — Game-end summary ─────────────────────────────────────
function AtriumSummary() {
  return (
    <AtriumShell>
      <ChapterHeader kicker="Game 047 · concluded" title="A quiet victory" />

      {/* Hero result card */}
      <div style={{
        margin: '6px 20px 10px', padding: '18px 18px 16px',
        border: `1px solid ${AT.hairline}`,
        background: 'rgba(255,255,255,0.02)',
        textAlign: 'center',
        position: 'relative',
      }}>
        {/* corner tick */}
        <div style={{
          position: 'absolute', top: -1, left: -1, width: 12, height: 12,
          borderTop: `1px solid ${AT.accent}`,
          borderLeft: `1px solid ${AT.accent}`,
        }} />
        <div style={{
          position: 'absolute', bottom: -1, right: -1, width: 12, height: 12,
          borderBottom: `1px solid ${AT.accent}`,
          borderRight: `1px solid ${AT.accent}`,
        }} />
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
        }}>Result</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 44, lineHeight: 1, color: AT.ink, marginTop: 4,
          textShadow: `0 0 16px ${AT.accent}33`,
        }}>Won · 1–0</div>
        <div style={{
          marginTop: 6,
          fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
          letterSpacing: 1.4, color: AT.accent,
        }}>38 moves · 27:41 · opponent resigned</div>
      </div>

      {/* Score strip */}
      <div style={{
        margin: '4px 20px 14px',
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 1, background: AT.hairline, padding: 1,
      }}>
        {[
          { k: 'Rating',   v: '+7',     c: AT.accent, sub: '1727' },
          { k: 'Accuracy', v: '86%',    c: AT.ink,    sub: '12 best' },
          { k: 'Theme',    v: 'Patient',c: AT.ink,    sub: 'vs. tactical' },
        ].map(m => (
          <div key={m.k} style={{
            background: '#0a0a10', padding: '10px 12px',
          }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
            }}>{m.k}</div>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 24, color: m.c, lineHeight: 1.1, marginTop: 4,
            }}>{m.v}</div>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
              color: AT.dim, letterSpacing: 0.6, marginTop: 2,
            }}>{m.sub}</div>
          </div>
        ))}
      </div>

      {/* Coach reflection */}
      <div style={{ padding: '0 24px' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.accent, textTransform: 'uppercase',
          marginBottom: 8,
        }}>Coach · reflection</div>
        <DropCapProse
          first="Y"
          rest={<>ou let the position do the talking. Restraint won this one — you didn't force, you waited, and your opponent's moves stopped working against you. Remember this feeling the next time you want to attack too early.</>}
        />
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8,
      }}>
        <AtriumButton wide label="Review game" primary />
        <AtriumButton label="New" />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 4 — Onboarding (skill calibration) ───────────────────────
// Per spec: adaptation layer computes opponent Elo from player.rating +
// player.confidence. This screen gathers those inputs in a scholarly way:
// slider for rating estimate, ornament-divided sections, and a preview of
// the opponent band that'll be dispatched.
function AtriumOnboarding() {
  return (
    <AtriumShell>
      {/* Soft header — first-run, no back button */}
      <div style={{ padding: '26px 24px 10px', textAlign: 'center' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 3, color: AT.accent, textTransform: 'uppercase',
        }}>Cereveon · Step 2 of 3</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontWeight: 500, fontSize: 34, lineHeight: 1.1,
          color: AT.ink, marginTop: 8, textWrap: 'balance',
        }}>How do you play?</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif',
          fontSize: 16, color: AT.muted, marginTop: 8,
          lineHeight: 1.4, maxWidth: 320, margin: '8px auto 0',
          fontStyle: 'italic',
        }}>We'll match an opponent at your level, and calibrate the coach's voice to how you think.</div>
        <div style={{ marginTop: 16 }}>
          <HairlineRule ornament color="rgba(255,255,255,0.12)" />
        </div>
      </div>

      {/* Rating estimate */}
      <div style={{ padding: '8px 28px 16px' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 8,
        }}>Rating · estimate</div>
        <div style={{
          display: 'flex', alignItems: 'baseline',
          justifyContent: 'space-between', marginBottom: 10,
        }}>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 44, lineHeight: 1, color: AT.accent,
            textShadow: `0 0 14px ${AT.accent}66`,
          }}>1720</div>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 14, color: AT.muted,
          }}>intermediate</div>
        </div>
        {/* slider track */}
        <div style={{ position: 'relative', height: 22 }}>
          <div style={{
            position: 'absolute', left: 0, right: 0, top: '50%', transform: 'translateY(-50%)',
            height: 2, background: 'rgba(255,255,255,0.06)',
          }} />
          <div style={{
            position: 'absolute', left: 0, width: '58%', top: '50%',
            transform: 'translateY(-50%)', height: 2,
            background: `linear-gradient(90deg, transparent, ${AT.accent})`,
          }} />
          <div style={{
            position: 'absolute', left: 'calc(58% - 8px)', top: '50%',
            transform: 'translateY(-50%)',
            width: 16, height: 16, borderRadius: '50%',
            background: AT.accent, boxShadow: `0 0 12px ${AT.accent}`,
          }} />
          {/* tick labels */}
          {[['800', '0%'], ['1400', '33%'], ['2000', '66%'], ['2600+', '100%']].map(([l, x]) => (
            <div key={l} style={{
              position: 'absolute', left: x, top: 20,
              transform: 'translateX(-50%)',
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              letterSpacing: 1, color: AT.dim,
            }}>{l}</div>
          ))}
        </div>
      </div>

      {/* Confidence picker */}
      <div style={{ padding: '20px 24px 16px' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 10,
        }}>Confidence · your voice to the coach</div>
        <div style={{ display: 'grid', gap: 6 }}>
          {[
            { k: 'sure',     t: 'Sure of it',     sub: 'I know roughly where I stand.', on: true  },
            { k: 'guessing', t: 'Guessing',       sub: 'I play for fun; haven\'t been rated.', on: false },
            { k: 'rusty',    t: 'Rusty',          sub: 'Used to be stronger, out of practice.', on: false },
          ].map(o => (
            <div key={o.k} style={{
              padding: '10px 12px',
              border: `1px solid ${o.on ? AT.accent + '55' : AT.hairline}`,
              background: o.on ? `${AT.accent}08` : 'transparent',
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <div style={{
                width: 14, height: 14, borderRadius: '50%',
                border: `1px solid ${o.on ? AT.accent : 'rgba(255,255,255,0.2)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {o.on && (
                  <div style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: AT.accent, boxShadow: `0 0 6px ${AT.accent}`,
                  }} />
                )}
              </div>
              <div>
                <div style={{
                  fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                  fontSize: 16, color: AT.ink,
                }}>{o.t}</div>
                <div style={{
                  fontFamily: 'Inter, sans-serif', fontSize: 11,
                  color: AT.muted, marginTop: 1,
                }}>{o.sub}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Preview — what will be dispatched */}
      <div style={{ padding: '4px 24px 0' }}>
        <HairlineRule color={AT.hairline} />
        <div style={{
          marginTop: 12, padding: '10px 12px',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: AT.danger, boxShadow: `0 0 6px ${AT.danger}`,
          }} />
          <div style={{ flex: 1 }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
              letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
            }}>First opponent</div>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 16, color: AT.ink, marginTop: 2,
            }}>~1680 · adaptive</div>
          </div>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 13, color: AT.muted,
          }}>adjusts as you play</div>
        </div>
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8,
      }}>
        <AtriumButton label="Back" />
        <AtriumButton wide label="Continue" primary />
      </div>
    </AtriumShell>
  );
}

Object.assign(window, {
  AT, AtriumShell, ChapterHeader, DropCapProse, AtriumButton,
  AtriumAnalysis, AtriumChat, AtriumSummary, AtriumOnboarding,
});
