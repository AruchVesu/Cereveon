import SwiftUI

/// Atrium board variant. Mirrors Android `ChessBoardView` STYLE_* keys and the
/// SettingsBottomSheet board-style rows. `.flat` is the default checker board;
/// `.engraved` adds an inset bevel per square; `.wireframe` overlays a thin cyan
/// grid on the checker fill.
enum BoardStyle: String, CaseIterable {
    case flat, engraved, wireframe
}

/// Pure-presentation SwiftUI chess board, ported from the rendering half of
/// Android `ChessBoardView`. It renders a board *snapshot* (`[[Character]]` in
/// `ChessGame` convention) and emits a `(from, to)` move on the second tap — it
/// owns ONLY its transient selection state. Legality, the engine, networking,
/// and game rules all live in the parent / `ChessGame`.
///
/// Rendering fidelity (matched to Android `onDraw`):
///   - square fill: `(row + col) % 2 == 0` → dark square (boardDark), else light
///   - Unicode glyphs (♚♛♜♝♞♟) for BOTH colours, differentiated by paint:
///     White = ivory glyph + soft cyan halo, Black = warm-obsidian glyph + amber rim
///   - rank digit on col-0 squares (top-left), file letter on row-7 squares (bottom-right)
///   - last-move + selection highlights are cyan tints
///   - focus ring = pulsing dashed-amber circle (radius ≈ 0.42·square, opacity
///     1.0 ↔ 0.45 over 1.8 s) — Atrium's single-square emphasis primitive; NOT
///     an arrow (move arrows are deliberately disallowed).
struct ChessBoardView: View {
    let board: [[Character]]          // 8x8, ChessGame convention (row 0 = rank 8)
    let whiteToMove: Bool             // which side's pieces may be selected
    let lastMoveFrom: Square?
    let lastMoveTo: Square?
    let focusSquare: Square?          // coach focus ring; nil = none
    var boardStyle: BoardStyle = .flat
    var isInteractive: Bool = true
    let onMove: (Square, Square) -> Void   // emitted on the 2nd tap: (from, to)

    @State private var selected: Square?

    // Board-internal paints, derived from the public Atrium tokens so nothing is
    // a fresh hardcoded literal and the DesignSystem stays untouched. Alphas
    // mirror the Android ChessBoardView paints (highlight α80, select α120, etc.).
    private static let lastMoveTint   = AtriumColors.accentCyan.opacity(80.0 / 255.0)
    private static let selectTint     = AtriumColors.accentCyan.opacity(120.0 / 255.0)
    private static let wireframeGrid  = AtriumColors.accentCyan.opacity(130.0 / 255.0)
    // Engraved bevel: a near-black inset edge and a dimmed-ink highlight edge,
    // derived from existing tokens rather than new colour literals.
    private static let engravedShadowEdge = AtriumColors.bgBase.opacity(0.9)
    private static let engravedLightEdge  = AtriumColors.dim.opacity(140.0 / 255.0)

    var body: some View {
        // The board is always rendered 1:1 so a tap's (col,row) maps cleanly to
        // a square. GeometryReader gives us the resolved side length.
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            let squareSize = side / 8

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    drawBoard(in: &context, squareSize: squareSize)
                }
                .frame(width: side, height: side)

                if let focus = focusSquare {
                    focusRing(at: focus, squareSize: squareSize)
                }
            }
            .frame(width: side, height: side)
            .contentShape(Rectangle())
            .gesture(tapGesture(squareSize: squareSize))
            // A board snapshot change (e.g. the parent applied a move) must drop
            // any stale selection so the next tap starts a fresh pick.
            .onChange(of: board) { _ in selected = nil }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        }
        .aspectRatio(1, contentMode: .fit)
    }

    // MARK: - Canvas rendering

    private func drawBoard(in context: inout GraphicsContext, squareSize: CGFloat) {
        let glyphSize = squareSize * 0.8
        let coordSize = squareSize * 0.22
        let edgeStroke = squareSize * 0.04

        for r in 0..<8 {
            for c in 0..<8 {
                let originX = CGFloat(c) * squareSize
                let originY = CGFloat(r) * squareSize
                let rect = CGRect(x: originX, y: originY, width: squareSize, height: squareSize)

                // Square fill — (r + c) even → dark square (mirrors Android).
                let squareColor = (r + c) % 2 == 0 ? AtriumColors.boardDark : AtriumColors.boardLight
                context.fill(Path(rect), with: .color(squareColor))

                if boardStyle == .engraved {
                    drawEngravedBevel(in: &context, rect: rect, stroke: edgeStroke)
                }

                // Last-move tint (both from + to squares share one cyan tint, as
                // Android does with a single highlightPaint).
                let square = Square(row: r, col: c)
                if square == lastMoveFrom || square == lastMoveTo {
                    context.fill(Path(rect), with: .color(Self.lastMoveTint))
                }

                // Selection tint (brighter cyan).
                if square == selected {
                    context.fill(Path(rect), with: .color(Self.selectTint))
                }

                // Coordinate labels: rank digit on the col-0 squares (top-left),
                // file letter on the row-7 squares (bottom-right).
                if c == 0 {
                    drawCoordinate(in: &context,
                                   text: String(8 - r),
                                   size: coordSize,
                                   anchor: .topLeading,
                                   at: CGPoint(x: originX + coordSize * 0.33,
                                               y: originY + coordSize * 0.33))
                }
                if r == 7 {
                    let file = String(Character(UnicodeScalar(UInt8(97 + c))))
                    drawCoordinate(in: &context,
                                   text: file,
                                   size: coordSize,
                                   anchor: .bottomTrailing,
                                   at: CGPoint(x: originX + squareSize - coordSize * 0.33,
                                               y: originY + squareSize - coordSize * 0.33))
                }

                // Piece glyph.
                let piece = board[r][c]
                if piece != "." {
                    drawPiece(in: &context,
                              piece: piece,
                              size: glyphSize,
                              center: CGPoint(x: originX + squareSize / 2,
                                              y: originY + squareSize / 2))
                }
            }
        }

        if boardStyle == .wireframe {
            drawWireframeGrid(in: &context, squareSize: squareSize, stroke: edgeStroke * 0.7)
        }
    }

    /// Inset bevel: dark top/left edges, lighter bottom/right edges (engraved look).
    private func drawEngravedBevel(in context: inout GraphicsContext, rect: CGRect, stroke: CGFloat) {
        let pad = stroke / 2
        var shadow = Path()
        shadow.move(to: CGPoint(x: rect.minX + pad, y: rect.minY + pad))
        shadow.addLine(to: CGPoint(x: rect.maxX - pad, y: rect.minY + pad))
        shadow.move(to: CGPoint(x: rect.minX + pad, y: rect.minY + pad))
        shadow.addLine(to: CGPoint(x: rect.minX + pad, y: rect.maxY - pad))
        context.stroke(shadow, with: .color(Self.engravedShadowEdge), lineWidth: stroke)

        var light = Path()
        light.move(to: CGPoint(x: rect.minX + pad, y: rect.maxY - pad))
        light.addLine(to: CGPoint(x: rect.maxX - pad, y: rect.maxY - pad))
        light.move(to: CGPoint(x: rect.maxX - pad, y: rect.minY + pad))
        light.addLine(to: CGPoint(x: rect.maxX - pad, y: rect.maxY - pad))
        context.stroke(light, with: .color(Self.engravedLightEdge), lineWidth: stroke)
    }

    private func drawWireframeGrid(in context: inout GraphicsContext, squareSize: CGFloat, stroke: CGFloat) {
        let span = squareSize * 8
        var grid = Path()
        for i in 0...8 {
            let v = CGFloat(i) * squareSize
            grid.move(to: CGPoint(x: 0, y: v))
            grid.addLine(to: CGPoint(x: span, y: v))
            grid.move(to: CGPoint(x: v, y: 0))
            grid.addLine(to: CGPoint(x: v, y: span))
        }
        context.stroke(grid, with: .color(Self.wireframeGrid), lineWidth: stroke)
    }

    private func drawPiece(in context: inout GraphicsContext, piece: Character, size: CGFloat, center: CGPoint) {
        let isWhite = piece.isUppercase
        let glyphColor = isWhite ? AtriumColors.pieceWhite : AtriumColors.pieceBlack
        let haloColor = isWhite ? AtriumColors.accentCyan : AtriumColors.accentAmber
        let haloRadius: CGFloat = isWhite ? 8 : 6

        let glyph = Self.glyph(for: piece)
        let crisp = context.resolve(
            Text(glyph)
                .font(.system(size: size))
                .foregroundColor(glyphColor)
        )
        // Soft cyan halo (White) / amber rim (Black), matching the Android
        // setShadowLayer glow that keeps the obsidian glyph legible on the dark
        // board. Drawn as a blurred copy of the glyph beneath the crisp one.
        let halo = context.resolve(
            Text(glyph)
                .font(.system(size: size))
                .foregroundColor(haloColor)
        )
        context.drawLayer { layer in
            layer.addFilter(.blur(radius: haloRadius))
            layer.draw(halo, at: center, anchor: .center)
        }
        context.draw(crisp, at: center, anchor: .center)
    }

    private func drawCoordinate(in context: inout GraphicsContext,
                                text: String,
                                size: CGFloat,
                                anchor: UnitPoint,
                                at point: CGPoint) {
        let resolved = context.resolve(
            Text(text)
                .font(.system(size: size, weight: .medium, design: .monospaced))
                .foregroundColor(AtriumColors.dim)
        )
        context.draw(resolved, at: point, anchor: anchor)
    }

    private static func glyph(for piece: Character) -> String {
        switch Character(piece.lowercased()) {
        case "k": return "\u{265A}" // ♚
        case "q": return "\u{265B}" // ♛
        case "r": return "\u{265C}" // ♜
        case "b": return "\u{265D}" // ♝
        case "n": return "\u{265E}" // ♞
        case "p": return "\u{265F}" // ♟
        default:  return ""
        }
    }

    // MARK: - Focus ring (pulsing dashed amber)

    private func focusRing(at square: Square, squareSize: CGFloat) -> some View {
        let radius = squareSize * 0.42
        let lineWidth = squareSize * 0.04
        let dash: [CGFloat] = [squareSize * 0.06, squareSize * 0.045]
        let centerX = (CGFloat(square.col) + 0.5) * squareSize
        let centerY = (CGFloat(square.row) + 0.5) * squareSize

        // TimelineView(.animation) drives a continuous 1.8 s pulse without any
        // imperative animator; opacity tracks 1.0 ↔ 0.45, matching the Android
        // ValueAnimator.ofFloat(1f, 0.45f, 1f) cv-pulse keyframe.
        return TimelineView(.animation) { timeline in
            let phase = timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.8) / 1.8
            // Triangle wave 1.0 → 0.45 → 1.0 over the period.
            let pulse = 1.0 - 0.55 * (1 - abs(2 * phase - 1))
            Circle()
                .stroke(style: StrokeStyle(lineWidth: lineWidth, dash: dash))
                .foregroundStyle(AtriumColors.accentAmber)
                .opacity(pulse)
                .frame(width: radius * 2, height: radius * 2)
                .position(x: centerX, y: centerY)
        }
        .allowsHitTesting(false)
    }

    // MARK: - Tap interaction (mirrors Android onTouchEvent)

    private func tapGesture(squareSize: CGFloat) -> some Gesture {
        // DragGesture(minimumDistance: 0) fires on touch-up as a tap; the board
        // is square so (col, row) = floor(location / squareSize).
        DragGesture(minimumDistance: 0)
            .onEnded { value in
                guard isInteractive, squareSize > 0 else { return }
                let col = Int((value.location.x / squareSize).rounded(.down))
                let row = Int((value.location.y / squareSize).rounded(.down))
                guard (0..<8).contains(row), (0..<8).contains(col) else { return }
                handleTap(row: row, col: col)
            }
    }

    private func handleTap(row: Int, col: Int) {
        let tapped = Square(row: row, col: col)
        if let from = selected {
            // Second tap: emit the move and clear selection. The parent decides
            // legality and updates the board snapshot.
            selected = nil
            onMove(from, tapped)
        } else {
            // First tap: select only a piece of the side to move.
            let piece = board[row][col]
            let belongsToSideToMove = piece != "." && (piece.isUppercase == whiteToMove)
            if belongsToSideToMove {
                selected = tapped
            }
        }
    }
}
