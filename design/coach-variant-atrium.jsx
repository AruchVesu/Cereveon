// coach-common.jsx — tokens, chess board renderer, shared UI atoms
// Unicode chess pieces (♔♕♖♗♘♙ / ♚♛♜♝♞♟) keep fidelity high without
// hand-drawing SVG. Filters do the neon outline work.

// ─── Sample game state ───────────────────────────────────────────────
// A mid-game position (Ruy Lopez-ish) — White to move.
// Using 8x8 array, lowercase = black, uppercase = white, '.' = empty.
const CV_POSITION = [
  ['r', '.', 'b', 'q', '.', 'r', 'k', '.'],
  ['p', 'p', '.', '.', '.', 'p', 'p', 'p'],
  ['.', '.', 'n', 'p', '.', 'n', '.', '.'],
  ['.', '.', '.', '.', 'p', '.', 'B', '.'],
  ['.', '.', '.', '.', 'P', '.', '.', '.'],
  ['.', '.', 'N', '.', '.', 'N', '.', '.'],
  ['P', 'P', 'P', '.', '.', 'P', 'P', 'P'],
  ['R', '.', '.', 'Q', '.', 'R', 'K', '.'],
];

const CV_GLYPH = {
  K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
  k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
};

// Last move: Bc1-g5 (White bishop develops, pins the knight on f6).
const CV_LAST_MOVE = { from: [7, 2], to: [3, 6], san: 'Bg5' };

// ─── Board renderer ──────────────────────────────────────────────────
// `variant` selects a board style: 'flat' | 'engraved' | 'wireframe'.
// `accent` is the neon highlight color. `palette` controls light/dark square colors.
function CoachBoard({
  size = 320,
  variant = 'flat',
  accent = '#4fd9e5',
  danger = '#ffc069',
  palette,
  showCoords = true,
  showLastMove = true,
  lastMove = CV_LAST_MOVE,
  position = CV_POSITION,
  showHintFocus = null, // [row, col] — subtle ring, NO arrows (no move suggestions)
}) {
  const pal = palette || {
    light: '#262a36',
    dark:  '#13161f',
    border: 'rgba(255,255,255,0.06)',
  };

  const cellSize = size / 8;
  const files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const ranks = ['8', '7', '6', '5', '4', '3', '2', '1'];

  const boardBg = {
    flat:      pal.light,
    engraved:  `radial-gradient(circle at 30% 20%, ${pal.light}, ${pal.dark} 70%)`,
    wireframe: '#0a0c11',
  }[variant];

  return (
    <div style={{
      position: 'relative',
      width: size, height: size,
      background: boardBg,
      borderRadius: 4,
      boxShadow: variant === 'engraved'
        ? 'inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -30px 60px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.04)'
        : variant === 'wireframe'
        ? `inset 0 0 0 1px ${accent}33, 0 0 24px ${accent}22`
        : 'inset 0 0 0 1px rgba(255,255,255,0.04)',
      overflow: 'hidden',
    }}>
      {/* squares */}
      {position.map((row, r) => row.map((p, c) => {
        const isLight = (r + c) % 2 === 0;
        const sqBg = variant === 'wireframe'
          ? 'transparent'
          : isLight ? pal.light : pal.dark;
        const isFrom = showLastMove && lastMove && lastMove.from[0] === r && lastMove.from[1] === c;
        const isTo   = showLastMove && lastMove && lastMove.to[0]   === r && lastMove.to[1]   === c;
        const isFocus = showHintFocus && showHintFocus[0] === r && showHintFocus[1] === c;

        return (
          <div key={`${r}-${c}`} style={{
            position: 'absolute',
            left: c * cellSize, top: r * cellSize,
            width: cellSize, height: cellSize,
            background: sqBg,
            boxShadow: variant === 'wireframe' ? `inset 0 0 0 0.5px ${accent}22` : undefined,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {/* last-move tint */}
            {(isFrom || isTo) && (
              <div style={{
                position: 'absolute', inset: 0,
                background: isTo ? `${accent}2e` : `${accent}1a`,
                boxShadow: isTo ? `inset 0 0 0 1.5px ${accent}` : `inset 0 0 0 1px ${accent}88`,
              }} />
            )}
            {/* focus ring (commentary spotlight, NOT a move arrow) */}
            {isFocus && (
              <div style={{
                position: 'absolute', inset: 3,
                border: `1.5px dashed ${danger}`,
                borderRadius: '50%',
                animation: 'cv-pulse 1.8s ease-in-out infinite',
              }} />
            )}
            {p !== '.' && (
              (() => {
                const isWhite = /[A-Z]/.test(p);
                // White = ivory with cyan rim. Black = warm obsidian/bronze with amber rim,
                // plus a soft inner highlight so it reads on dark squares too.
                return (
                  <span className="piece" style={{
                    fontSize: cellSize * 0.78,
                    color: isWhite ? '#f4efe1' : '#1a1108',
                    // Layered text-shadow: a 1px crisp rim in the accent color +
                    // a blurred halo — gives pieces a "carved + lit" feel.
                    textShadow: isWhite
                      ? `
                          0 0 1px ${accent}aa,
                          1px 0 0 rgba(255,255,255,0.15),
                          0 1px 0 rgba(0,0,0,0.5),
                          0 0 8px rgba(255,255,255,0.08)
                        `
                      : `
                          -0.5px -0.5px 0 ${danger}cc,
                           0.5px -0.5px 0 ${danger}cc,
                          -0.5px  0.5px 0 ${danger}cc,
                           0.5px  0.5px 0 ${danger}cc,
                           0 0 6px ${danger}55,
                           0 0 12px rgba(0,0,0,0.6)
                        `,
                    position: 'relative',
                  }}>{CV_GLYPH[p]}</span>
                );
              })()
            )}
            {/* file labels along bottom rank */}
            {showCoords && r === 7 && (
              <span style={{
                position: 'absolute', right: 3, bottom: 1,
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: 8,
                color: isLight ? '#6b7080' : '#3e424f',
                letterSpacing: 0.5,
              }}>{files[c]}</span>
            )}
            {/* rank labels along a-file */}
            {showCoords && c === 0 && (
              <span style={{
                position: 'absolute', left: 3, top: 1,
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: 8,
                color: isLight ? '#6b7080' : '#3e424f',
                letterSpacing: 0.5,
              }}>{ranks[r]}</span>
            )}
          </div>
        );
      }))}
    </div>
  );
}

// ─── Eval band — coarse (per ESV spec: no numeric scores) ──────────────
// Shows the band name + a position indicator. No centipawns, no percentages.
// Bands: 'winning' | 'better' | 'equal' | 'worse' | 'losing' | 'mate'
function EvalBand({
  band = 'better',
  side = 'white',
  accent = '#4fd9e5',
  danger = '#ffc069',
  compact = false,
}) {
  const bands = ['losing', 'worse', 'equal', 'better', 'winning'];
  const idx = Math.max(0, bands.indexOf(band));
  const pct = (idx / (bands.length - 1)) * 100;
  const color = band === 'winning' || band === 'better'
    ? (side === 'white' ? accent : danger)
    : '#7a8094';

  return (
    <div style={{ width: '100%' }}>
      <div style={{
        position: 'relative',
        height: compact ? 4 : 6,
        background: 'rgba(255,255,255,0.05)',
        borderRadius: 999,
        overflow: 'hidden',
      }}>
        {/* tick marks for each band */}
        {bands.map((_, i) => (
          <div key={i} style={{
            position: 'absolute', left: `${(i / (bands.length - 1)) * 100}%`,
            top: 0, bottom: 0, width: 1,
            background: 'rgba(255,255,255,0.08)',
          }} />
        ))}
        <div style={{
          position: 'absolute',
          left: 0, top: 0, bottom: 0,
          width: `${pct}%`,
          background: `linear-gradient(90deg, transparent, ${color})`,
          transition: 'width 400ms cubic-bezier(.3,.9,.3,1)',
        }} />
        <div style={{
          position: 'absolute',
          left: `calc(${pct}% - ${compact ? 4 : 6}px)`,
          top: '50%',
          transform: 'translateY(-50%)',
          width: compact ? 8 : 12, height: compact ? 8 : 12,
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 12px ${color}`,
        }} />
      </div>
      {!compact && (
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          marginTop: 6, fontFamily: 'JetBrains Mono, monospace',
          fontSize: 9, letterSpacing: 1.4, textTransform: 'uppercase',
          color: '#5e6475',
        }}>
          {bands.map(b => (
            <span key={b} style={{
              color: b === band ? color : undefined,
              fontWeight: b === band ? 600 : 400,
            }}>{b.slice(0, 3)}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Hairline ornament (used as section dividers) ────────────────────
function HairlineRule({ color = 'rgba(255,255,255,0.08)', ornament = false }) {
  if (!ornament) {
    return <div style={{ height: 1, background: color, width: '100%' }} />;
  }
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      fontFamily: 'Cormorant Garamond, serif',
      color,
    }}>
      <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, transparent, ${color})` }} />
      <span style={{ fontSize: 14, lineHeight: 1, color: 'rgba(230,226,214,0.4)' }}>✦</span>
      <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${color}, transparent)` }} />
    </div>
  );
}

// ─── Captured pieces row ─────────────────────────────────────────────
function CapturedRow({ pieces = ['p', 'p', 'n'], side = 'white', size = 12 }) {
  return (
    <div style={{ display: 'flex', gap: 1, alignItems: 'center' }}>
      {pieces.map((p, i) => (
        <span key={i} className="piece" style={{
          fontSize: size,
          color: side === 'white' ? '#f4efe1aa' : '#0a0c11',
          filter: side === 'white' ? undefined : 'invert(1) brightness(0.9)',
        }}>{CV_GLYPH[p]}</span>
      ))}
    </div>
  );
}

// ─── Coach avatar glyph — a stylized omega, scholarly ────────────────
function CoachMark({ size = 32, color = '#4fd9e5' }) {
  return (
    <div style={{
      width: size, height: size,
      borderRadius: '50%',
      background: `radial-gradient(circle at 35% 30%, ${color}22, transparent 70%), #0f1220`,
      border: `1px solid ${color}44`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'Cormorant Garamond, serif',
      fontStyle: 'italic',
      fontSize: size * 0.55,
      color,
      letterSpacing: -1,
      fontWeight: 500,
    }}>C</div>
  );
}

Object.assign(window, {
  CV_POSITION, CV_GLYPH, CV_LAST_MOVE,
  CoachBoard, EvalBand, HairlineRule, CapturedRow, CoachMark,
});
