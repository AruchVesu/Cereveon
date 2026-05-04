// atrium-screens-2.jsx — second wave of Atrium-language screens
// Home/library · Lessons (curriculum) · Opening repertoire · Profile/stats
// · Settings · Paywall. Reuses AtriumShell, ChapterHeader, DropCapProse,
// AtriumButton, AT tokens from atrium-screens.jsx.

// ─── SCREEN 5 — Home / Library ──────────────────────────────────────
function AtriumHome() {
  return (
    <AtriumShell>
      {/* Header — wordmark + greeting */}
      <div style={{ padding: '20px 24px 8px' }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        }}>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontWeight: 500, fontSize: 22, letterSpacing: 0.4, color: AT.ink,
          }}>Cereveon</div>
          <div style={{
            width: 30, height: 30, borderRadius: '50%',
            background: `radial-gradient(circle at 35% 30%, ${AT.accent}33, transparent 70%), #0f1828`,
            border: `1px solid ${AT.accent}44`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'Cormorant Garamond, serif',
            fontSize: 14, color: AT.accent, fontWeight: 600,
          }}>AG</div>
        </div>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginTop: 14,
        }}>Tuesday · Day 047</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontWeight: 500, fontSize: 30, lineHeight: 1.1,
          color: AT.ink, marginTop: 4, textWrap: 'balance',
        }}>Continue your study.</div>
        <div style={{ marginTop: 12 }}>
          <HairlineRule ornament color="rgba(255,255,255,0.12)" />
        </div>
      </div>

      {/* Resume card — last game */}
      <div style={{ padding: '10px 20px 8px' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 8,
        }}>Resume</div>
        <div style={{
          padding: '12px 14px',
          border: `1px solid ${AT.accent}33`,
          background: `${AT.accent}06`,
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{ padding: 3, background: '#0d0e14' }}>
            <CoachBoard
              size={56} variant="flat"
              accent={AT.accent} danger={AT.danger}
              palette={{ light: '#302c24', dark: '#1a1712', border: 'rgba(255,255,255,0.04)' }}
              showCoords={false}
            />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 17, color: AT.ink, lineHeight: 1.1,
            }}>Game 047 · move 14</div>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
              letterSpacing: 1, color: AT.dim, marginTop: 4,
            }}>vs. ~1680 · your move · 14:21</div>
          </div>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 22, color: AT.accent,
          }}>→</div>
        </div>
      </div>

      {/* Library — sections */}
      <div style={{ padding: '12px 20px 0' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 10,
        }}>Library</div>
        {[
          { roman: 'I',   t: 'New game',          sub: 'Adaptive opponent · ~1680' },
          { roman: 'II',  t: 'Lessons',           sub: '3 of 12 chapters · pin & pressure' },
          { roman: 'III', t: 'Openings',          sub: 'Ruy Lopez · in progress' },
          { roman: 'IV',  t: 'Past games',        sub: '46 played · 27 won' },
        ].map((row, i, all) => (
          <React.Fragment key={row.roman}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '12px 0',
            }}>
              <div style={{
                width: 28,
                fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                fontSize: 22, color: AT.accent, opacity: 0.8,
                textAlign: 'center',
              }}>{row.roman}</div>
              <div style={{ flex: 1 }}>
                <div style={{
                  fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                  fontSize: 18, color: AT.ink, lineHeight: 1.1,
                }}>{row.t}</div>
                <div style={{
                  fontFamily: 'Inter, sans-serif', fontSize: 12,
                  color: AT.muted, marginTop: 2, fontStyle: 'italic',
                }}>{row.sub}</div>
              </div>
              <div style={{
                fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                fontSize: 18, color: AT.dim,
              }}>›</div>
            </div>
            {i < all.length - 1 && <HairlineRule color={AT.hairline} />}
          </React.Fragment>
        ))}
      </div>

      {/* Bottom tab — tiny, italic, ornamented */}
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 14,
        display: 'flex', justifyContent: 'space-around',
        padding: '12px 20px 0', borderTop: `1px solid ${AT.hairline}`,
      }}>
        {[
          { l: 'Home',     on: true  },
          { l: 'Lessons',  on: false },
          { l: 'Coach',    on: false },
          { l: 'You',      on: false },
        ].map(t => (
          <div key={t.l} style={{ textAlign: 'center', cursor: 'pointer' }}>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 14, color: t.on ? AT.accent : AT.muted,
              textShadow: t.on ? `0 0 8px ${AT.accent}66` : undefined,
            }}>{t.l}</div>
            {t.on && (
              <div style={{
                width: 4, height: 4, borderRadius: '50%',
                background: AT.accent, boxShadow: `0 0 6px ${AT.accent}`,
                margin: '4px auto 0',
              }} />
            )}
          </div>
        ))}
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 6 — Lessons (curriculum) ────────────────────────────────
function AtriumLessons() {
  return (
    <AtriumShell>
      <ChapterHeader kicker="Curriculum · Volume I" title="The Art of Restraint" />

      {/* Progress strip */}
      <div style={{ padding: '4px 24px 12px' }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 6,
        }}>
          <span>Progress</span>
          <span style={{ color: AT.accent }}>3 / 12 chapters</span>
        </div>
        <div style={{
          height: 4, background: 'rgba(255,255,255,0.05)',
          borderRadius: 999, overflow: 'hidden', position: 'relative',
        }}>
          <div style={{
            position: 'absolute', left: 0, top: 0, bottom: 0,
            width: '25%',
            background: `linear-gradient(90deg, transparent, ${AT.accent})`,
          }} />
        </div>
      </div>

      {/* Chapter list */}
      <div style={{
        position: 'absolute', top: 178, bottom: 80, left: 0, right: 0,
        padding: '0 24px',
        overflowY: 'auto',
      }}>
        {[
          { n: 'I',    t: 'On seeing the board',     state: 'done',     sub: '8 minutes · last week' },
          { n: 'II',   t: 'The pin',                 state: 'done',     sub: '12 minutes · today' },
          { n: 'III',  t: 'The fork',                state: 'current',  sub: '6 of 9 puzzles · in progress' },
          { n: 'IV',   t: 'Discovered attacks',      state: 'next',     sub: '~14 minutes' },
          { n: 'V',    t: 'Pawn structure I',        state: 'locked',   sub: 'unlocks after IV' },
          { n: 'VI',   t: 'The bishop pair',         state: 'locked',   sub: 'unlocks after V' },
        ].map((c, i, all) => {
          const color = c.state === 'done' ? AT.accent : c.state === 'current' ? AT.ink : AT.dim;
          const accentRing = c.state === 'current';
          return (
            <React.Fragment key={c.n}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 14,
                padding: '14px 0',
                opacity: c.state === 'locked' ? 0.5 : 1,
              }}>
                <div style={{
                  width: 36, height: 36, borderRadius: '50%',
                  border: `1px solid ${accentRing ? AT.accent : 'rgba(255,255,255,0.15)'}`,
                  background: c.state === 'done' ? `${AT.accent}15` : 'transparent',
                  boxShadow: accentRing ? `0 0 10px ${AT.accent}55` : undefined,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                  fontSize: 16,
                  color: accentRing ? AT.accent : color,
                }}>
                  {c.state === 'done' ? '✓' : c.n}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{
                    fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                    fontSize: 18, color: AT.ink, lineHeight: 1.1,
                  }}>{c.t}</div>
                  <div style={{
                    fontFamily: 'Inter, sans-serif', fontSize: 11,
                    color: AT.muted, marginTop: 3, fontStyle: 'italic',
                  }}>{c.sub}</div>
                </div>
                {c.state === 'current' && (
                  <span style={{
                    fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                    letterSpacing: 2, color: AT.accent, textTransform: 'uppercase',
                    border: `1px solid ${AT.accent}55`, padding: '2px 6px', borderRadius: 2,
                  }}>NOW</span>
                )}
              </div>
              {i < all.length - 1 && <HairlineRule color={AT.hairline} />}
            </React.Fragment>
          );
        })}
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
      }}>
        <AtriumButton wide label="Continue Chapter III · The fork" primary />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 7 — Opening repertoire ──────────────────────────────────
function AtriumOpenings() {
  return (
    <AtriumShell>
      <ChapterHeader kicker="Repertoire · as White" title="Your Opening Book" />

      {/* Stats strip */}
      <div style={{
        margin: '4px 20px 12px',
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 1, background: AT.hairline, padding: 1,
      }}>
        {[
          { k: 'Lines',  v: '4',   sub: 'memorized' },
          { k: 'Depth',  v: '12',  sub: 'avg. moves' },
          { k: 'Score',  v: '68%', sub: 'win rate' },
        ].map(m => (
          <div key={m.k} style={{ background: '#0a0a10', padding: '10px 12px' }}>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
              letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
            }}>{m.k}</div>
            <div style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 22, color: AT.ink, marginTop: 4, lineHeight: 1,
            }}>{m.v}</div>
            <div style={{
              fontFamily: 'Inter, sans-serif', fontSize: 10,
              color: AT.muted, marginTop: 2, fontStyle: 'italic',
            }}>{m.sub}</div>
          </div>
        ))}
      </div>

      {/* Opening cards */}
      <div style={{
        position: 'absolute', top: 220, bottom: 80, left: 0, right: 0,
        padding: '0 20px', overflowY: 'auto',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        {[
          { eco: 'C84', name: 'Ruy Lopez · Closed',     line: '1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6',     mastery: 0.78, current: true  },
          { eco: 'B22', name: 'Sicilian · Alapin',       line: '1.e4 c5 2.c3 ♘f6 3.e5 ♘d5',      mastery: 0.55, current: false },
          { eco: 'D02', name: "Queen's Pawn · London",   line: '1.d4 d5 2.♘f3 ♘f6 3.♗f4',         mastery: 0.42, current: false },
          { eco: 'A04', name: 'Réti opening',            line: '1.♘f3 d5 2.c4 e6 3.g3',           mastery: 0.18, current: false },
        ].map(o => (
          <div key={o.eco} style={{
            padding: '12px 14px',
            border: `1px solid ${o.current ? AT.accent + '55' : AT.hairline}`,
            background: o.current ? `${AT.accent}08` : 'transparent',
            position: 'relative',
          }}>
            <div style={{
              display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
                  letterSpacing: 1.5, color: o.current ? AT.accent : AT.dim,
                }}>{o.eco}</span>
                <span style={{
                  fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                  fontSize: 17, color: AT.ink,
                }}>{o.name}</span>
              </div>
              {o.current && (
                <span style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                  letterSpacing: 2, color: AT.accent, textTransform: 'uppercase',
                }}>active</span>
              )}
            </div>
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
              color: AT.muted, marginTop: 6, letterSpacing: 0.5,
            }}>{o.line}</div>
            {/* mastery bar */}
            <div style={{
              marginTop: 10,
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <div style={{
                flex: 1, height: 2, background: 'rgba(255,255,255,0.05)',
                borderRadius: 999, overflow: 'hidden', position: 'relative',
              }}>
                <div style={{
                  position: 'absolute', left: 0, top: 0, bottom: 0,
                  width: `${o.mastery * 100}%`,
                  background: o.current ? AT.accent : AT.muted,
                  boxShadow: o.current ? `0 0 6px ${AT.accent}66` : undefined,
                }} />
              </div>
              <span style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
                color: o.current ? AT.accent : AT.dim, letterSpacing: 1,
              }}>{Math.round(o.mastery * 100)}%</span>
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', gap: 8,
      }}>
        <AtriumButton wide label="Drill active line" primary />
        <AtriumButton label="+" />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 8 — Profile / Stats ─────────────────────────────────────
function AtriumProfile() {
  // Hand-drawn rating sparkline points (12 sessions, climbing)
  const ratingPoints = [1655, 1660, 1672, 1668, 1685, 1690, 1702, 1698, 1710, 1715, 1720, 1727];
  const min = Math.min(...ratingPoints);
  const max = Math.max(...ratingPoints);
  const W = 340, H = 70;
  const pts = ratingPoints.map((v, i) => {
    const x = (i / (ratingPoints.length - 1)) * W;
    const y = H - ((v - min) / (max - min)) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  return (
    <AtriumShell>
      <ChapterHeader kicker="A. Gusev · 47 days" title="Your study" />

      {/* Profile crest */}
      <div style={{
        padding: '4px 24px 6px',
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: '50%',
          background: `radial-gradient(circle at 35% 30%, ${AT.accent}33, transparent 70%), #0f1828`,
          border: `1px solid ${AT.accent}55`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 24, color: AT.accent, fontWeight: 600,
          boxShadow: `0 0 16px ${AT.accent}22`,
        }}>AG</div>
        <div style={{ flex: 1 }}>
          <div style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 22, color: AT.ink, lineHeight: 1.05,
          }}>Artiom Gusev</div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
            color: AT.dim, letterSpacing: 1, marginTop: 4,
          }}>since March · 1727 elo · intermediate</div>
        </div>
      </div>

      {/* Rating chart */}
      <div style={{ padding: '10px 24px 6px' }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 6,
        }}>
          <span>Rating · 12 sessions</span>
          <span style={{ color: AT.accent }}>+72 since start</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H + 8}`} style={{ width: '100%', height: 80, display: 'block' }}>
          <defs>
            <linearGradient id="atrium-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"  stopColor={AT.accent} stopOpacity="0.32" />
              <stop offset="100%" stopColor={AT.accent} stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon
            points={`0,${H} ${pts} ${W},${H}`}
            fill="url(#atrium-fill)"
          />
          <polyline
            points={pts}
            fill="none"
            stroke={AT.accent}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ filter: `drop-shadow(0 0 4px ${AT.accent})` }}
          />
          {/* end dot */}
          {(() => {
            const [lx, ly] = pts.split(' ').pop().split(',');
            return <circle cx={lx} cy={ly} r="3" fill={AT.accent} style={{ filter: `drop-shadow(0 0 6px ${AT.accent})` }} />;
          })()}
        </svg>
      </div>

      <div style={{ padding: '0 24px' }}>
        <HairlineRule color={AT.hairline} />
      </div>

      {/* Themes mastered */}
      <div style={{ padding: '12px 24px 6px' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.dim, textTransform: 'uppercase',
          marginBottom: 10,
        }}>Themes you handle well</div>
        <div style={{ display: 'grid', gap: 8 }}>
          {[
            { t: 'Patient defense',  v: 0.84 },
            { t: 'The pin',          v: 0.76 },
            { t: 'Pawn structure',   v: 0.62 },
            { t: 'Tactical sharpness', v: 0.38, weak: true },
          ].map(r => (
            <div key={r.t}>
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                marginBottom: 4,
              }}>
                <span style={{
                  fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                  fontSize: 14, color: r.weak ? AT.danger : AT.ink,
                }}>{r.t}</span>
                <span style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
                  color: r.weak ? AT.danger : AT.dim, letterSpacing: 1,
                }}>{Math.round(r.v * 100)}%</span>
              </div>
              <div style={{
                height: 2, background: 'rgba(255,255,255,0.05)',
                position: 'relative',
              }}>
                <div style={{
                  position: 'absolute', left: 0, top: 0, bottom: 0,
                  width: `${r.v * 100}%`,
                  background: r.weak ? AT.danger : AT.accent,
                  boxShadow: `0 0 6px ${r.weak ? AT.danger : AT.accent}66`,
                }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Coach footnote */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 70,
        padding: '12px 14px',
        border: `1px solid ${AT.accent}22`,
        background: `${AT.accent}05`,
      }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
          letterSpacing: 2, color: AT.accent, textTransform: 'uppercase',
          marginBottom: 4,
        }}>Coach · note</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 14, color: AT.ink, lineHeight: 1.4,
        }}>You're stronger when you wait. Try Chapter III to sharpen what happens when you don't have time to.</div>
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
      }}>
        <AtriumButton wide label="View full archive" />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 9 — Settings ────────────────────────────────────────────
function AtriumSettings() {
  const Toggle = ({ on }) => (
    <div style={{
      width: 32, height: 18, borderRadius: 999,
      background: on ? `${AT.accent}33` : 'rgba(255,255,255,0.06)',
      border: `1px solid ${on ? AT.accent + '88' : 'rgba(255,255,255,0.1)'}`,
      position: 'relative',
      boxShadow: on ? `0 0 8px ${AT.accent}44` : undefined,
    }}>
      <div style={{
        position: 'absolute',
        top: 1, left: on ? 14 : 1,
        width: 14, height: 14, borderRadius: '50%',
        background: on ? AT.accent : '#5a5e6a',
        boxShadow: on ? `0 0 4px ${AT.accent}` : undefined,
        transition: 'left 200ms',
      }} />
    </div>
  );

  const Row = ({ label, sub, control }) => (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '12px 0',
    }}>
      <div style={{ flex: 1 }}>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontSize: 16, color: AT.ink, lineHeight: 1.1,
        }}>{label}</div>
        {sub && (
          <div style={{
            fontFamily: 'Inter, sans-serif', fontSize: 11,
            color: AT.muted, marginTop: 2, fontStyle: 'italic',
          }}>{sub}</div>
        )}
      </div>
      {control}
    </div>
  );

  return (
    <AtriumShell>
      <ChapterHeader kicker="Preferences" title="Settings" />

      <div style={{
        position: 'absolute', top: 130, bottom: 80, left: 0, right: 0,
        padding: '0 24px', overflowY: 'auto',
      }}>
        {/* Section: Coach */}
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.accent, textTransform: 'uppercase',
          marginTop: 6, marginBottom: 4,
        }}>Coach</div>
        <Row label="Voice"           sub="Warm tutor · Cormorant prose"
             control={<span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 14, color: AT.muted }}>Warm ›</span>} />
        <HairlineRule color={AT.hairline} />
        <Row label="Live commentary" sub="Hint after every move"
             control={<Toggle on />} />
        <HairlineRule color={AT.hairline} />
        <Row label="Strictness"      sub="Allow tactical questions only after the move"
             control={<Toggle on />} />

        {/* Section: Game */}
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.accent, textTransform: 'uppercase',
          marginTop: 18, marginBottom: 4,
        }}>Game</div>
        <Row label="Board style"     sub="Flat · engraved · wireframe"
             control={<span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 14, color: AT.muted }}>Engraved ›</span>} />
        <HairlineRule color={AT.hairline} />
        <Row label="Sound"           sub="Wood click on move"
             control={<Toggle on={false} />} />
        <HairlineRule color={AT.hairline} />
        <Row label="Haptics"         sub="Subtle on capture"
             control={<Toggle on />} />

        {/* Section: System */}
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 2.4, color: AT.accent, textTransform: 'uppercase',
          marginTop: 18, marginBottom: 4,
        }}>System</div>
        <Row label="Account · gusev@…"
             control={<span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 14, color: AT.muted }}>›</span>} />
        <HairlineRule color={AT.hairline} />
        <Row label="About Cereveon"
             control={<span style={{ fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic', fontSize: 14, color: AT.muted }}>v 1.4 ›</span>} />
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
      }}>
        <AtriumButton wide label="Sign out" />
      </div>
    </AtriumShell>
  );
}

// ─── SCREEN 10 — Paywall ────────────────────────────────────────────
function AtriumPaywall() {
  return (
    <AtriumShell>
      {/* Quiet hero */}
      <div style={{ padding: '36px 28px 8px', textAlign: 'center' }}>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
          letterSpacing: 3, color: AT.accent, textTransform: 'uppercase',
        }}>Cereveon · Atrium</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
          fontWeight: 500, fontSize: 36, lineHeight: 1.05,
          color: AT.ink, marginTop: 10, textWrap: 'balance',
          textShadow: `0 0 24px ${AT.accent}22`,
        }}>A coach who remembers.</div>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif',
          fontSize: 16, color: AT.muted, marginTop: 8,
          lineHeight: 1.4, maxWidth: 320, margin: '8px auto 0',
          fontStyle: 'italic',
        }}>Continue your study with the full library, the chat coach, and adaptive opponents that grow with you.</div>
        <div style={{ marginTop: 18 }}>
          <HairlineRule ornament color="rgba(255,255,255,0.2)" />
        </div>
      </div>

      {/* Bullet list */}
      <div style={{ padding: '14px 28px 8px' }}>
        {[
          'Unlimited adaptive games',
          'Full curriculum · 12 chapters',
          'Coach chat · grounded in your games',
          'Opening repertoire drills',
        ].map(b => (
          <div key={b} style={{
            display: 'flex', alignItems: 'baseline', gap: 12,
            padding: '8px 0',
          }}>
            <span style={{
              fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
              fontSize: 16, color: AT.accent,
              textShadow: `0 0 6px ${AT.accent}66`,
            }}>✦</span>
            <span style={{
              fontFamily: 'Cormorant Garamond, serif',
              fontSize: 16, color: AT.ink,
            }}>{b}</span>
          </div>
        ))}
      </div>

      {/* Plans */}
      <div style={{ padding: '14px 24px 0' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            { k: 'monthly', t: 'Monthly',  price: '$9',    sub: 'per month',     on: false },
            { k: 'yearly',  t: 'Yearly',   price: '$72',   sub: '$6 / month',    on: true,  badge: 'best value' },
          ].map(p => (
            <div key={p.k} style={{
              padding: '14px 14px 12px',
              border: `1px solid ${p.on ? AT.accent + '88' : AT.hairline}`,
              background: p.on ? `${AT.accent}10` : 'transparent',
              position: 'relative',
              boxShadow: p.on ? `0 0 18px ${AT.accent}22` : undefined,
            }}>
              {p.badge && (
                <div style={{
                  position: 'absolute', top: -8, right: 10,
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                  letterSpacing: 2, color: AT.accent, textTransform: 'uppercase',
                  background: '#0a0a10', padding: '2px 6px',
                  border: `1px solid ${AT.accent}55`,
                }}>{p.badge}</div>
              )}
              <div style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
                letterSpacing: 2, color: AT.dim, textTransform: 'uppercase',
              }}>{p.t}</div>
              <div style={{
                fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
                fontSize: 30, color: p.on ? AT.accent : AT.ink,
                marginTop: 6, lineHeight: 1,
              }}>{p.price}</div>
              <div style={{
                fontFamily: 'Inter, sans-serif', fontSize: 11,
                color: AT.muted, marginTop: 4, fontStyle: 'italic',
              }}>{p.sub}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Fine print */}
      <div style={{
        padding: '14px 28px 0', textAlign: 'center',
        fontFamily: 'Inter, sans-serif', fontSize: 11,
        color: AT.dim, fontStyle: 'italic', lineHeight: 1.4,
      }}>
        Cancel anytime · 7 days free · restored across devices.
      </div>

      {/* Footer */}
      <div style={{
        position: 'absolute', left: 16, right: 16, bottom: 16,
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <AtriumButton wide label="Begin · 7 days free" primary />
        <div style={{ textAlign: 'center' }}>
          <span style={{
            fontFamily: 'Cormorant Garamond, serif', fontStyle: 'italic',
            fontSize: 13, color: AT.muted, cursor: 'pointer',
          }}>Maybe later</span>
        </div>
      </div>
    </AtriumShell>
  );
}

Object.assign(window, {
  AtriumHome, AtriumLessons, AtriumOpenings,
  AtriumProfile, AtriumSettings, AtriumPaywall,
});
