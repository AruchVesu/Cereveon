#pragma once
#include <string>
#include <vector>
#include <cstdint>
#include <chrono>

// ──────────────────────────────────────────────────────────────
// Piece types and colours
// ──────────────────────────────────────────────────────────────
enum Spalva : uint8_t { BALTA = 0, JUODA = 1 };
enum PType  : uint8_t {
    NONE = 0, PAWN = 1, KNIGHT = 2, BISHOP = 3,
    ROOK = 4, QUEEN = 5, KING = 6
};

struct PieceCell {
    PType  type  = NONE;
    Spalva color = BALTA;
};

// ──────────────────────────────────────────────────────────────
// SachmatuLenta — chess engine class
//
// Board coordinate convention
//   board[row][col], row 0 = rank 8 (black's back rank),
//                    row 7 = rank 1 (white's back rank),
//                    col 0 = a-file, col 7 = h-file.
//
// Public API is stable: loadFromBoard64 / getBestMove / Move
// are the JNI contract.  Everything else may change.
// ──────────────────────────────────────────────────────────────
class SachmatuLenta {
public:
    // ── Move: JNI contract (fromX/fromY/toX/toY/isValid) ──────
    struct Move {
        int     fromX = -1, fromY = -1, toX = -1, toY = -1;
        int     score  = 0;
        uint8_t promo  = 0;    // 'Q','R','B','N' for pawn promotions, else 0

        Move() = default;
        Move(int fx, int fy, int tx, int ty, uint8_t pr = 0)
            : fromX(fx), fromY(fy), toX(tx), toY(ty), score(0), promo(pr) {}

        bool isValid() const { return fromX >= 0; }
        bool operator==(const Move& o) const {
            return fromX == o.fromX && fromY == o.fromY &&
                   toX   == o.toX   && toY   == o.toY   &&
                   promo == o.promo;
        }
    };

    // ── Life-cycle ─────────────────────────────────────────────
    SachmatuLenta();
    ~SachmatuLenta() = default;   // no heap allocations to release

    void reset();
    void setupLenta();
    void loadFromBoard64(const char* fen);

    // ── Move interface ─────────────────────────────────────────
    bool syncMove(int fr, int fc, int tr, int tc);
    Move getBestMove(Spalva s, int strengthLevel = 100);
    bool promotePawn(int r, int c, char type);

    // ── State queries ──────────────────────────────────────────
    std::string toBoard64String() const;
    Spalva      getCurrentTurn()  const { return currentTurn; }

    // ── Public API kept for backward compatibility ─────────────
    bool              isLegalMove(int fr, int fc, int tr, int tc, Spalva s) const;
    std::vector<Move> generateLegalMoves(Spalva s);   // non-const: makes/unmakes
    int               evaluateBoard()      const;
    int               minimax(int depth, bool isMax, int alpha, int beta);
    bool              isSquareAttacked(int r, int c, Spalva byColor) const;
    bool              isInCheck(Spalva s)  const;

    // ── Perft (move-generation correctness, used by CI) ────────
    uint64_t perft(int depth);

private:
    // ── Position ───────────────────────────────────────────────
    PieceCell board[8][8];
    Spalva    currentTurn;
    uint8_t   castling;     // bits: 0=W♔K  1=W♔Q  2=B♟K  3=B♟Q
    int       epFile;       // −1 = none; 0‥7 = file of en-passant target
    int       epRank;       // row  of en-passant target square
    int       halfMoveClock;
    uint64_t  currentHash;
    std::vector<uint64_t> hashHistory;

    // ── Undo record ────────────────────────────────────────────
    struct UndoInfo {
        PieceCell capturedAtTo;   // piece that occupied [toX][toY]
        bool      wasEp;
        int       epCaptureRow;   // row of the ep-captured pawn
        bool      wasCastle;
        int       rookFromCol;    // −1 when not a castling move
        int       rookToCol;
        bool      wasPromotion;
        uint8_t   castlingBefore;
        int       epFileBefore;
        int       epRankBefore;
        int       halfMoveClockBefore;
        uint64_t  hashBefore;
    };

    // ── Transposition table ────────────────────────────────────
    enum TTBound : uint8_t { TT_EXACT, TT_LOWER, TT_UPPER };
    struct TTEntry {
        uint64_t hash  = 0;
        int32_t  score = 0;
        int8_t   depth = -1;
        TTBound  bound = TT_EXACT;
        Move     best;
    };
    static constexpr size_t TT_SIZE = 1u << 20;   // 1 M entries ≈ 40 MB BSS
    static TTEntry ttTable[TT_SIZE];

    // ── Zobrist random numbers ─────────────────────────────────
    static uint64_t ZPC[2][7][8][8];   // [colour][ptype][row][col]
    static uint64_t ZSIDE;
    static uint64_t ZCAST[16];
    static uint64_t ZEP[8];
    static bool     s_zobristReady;

    static void  initZobrist();
    uint64_t     computeHash() const;

    // ── Search heuristics ──────────────────────────────────────
    static constexpr int MAX_PLY = 64;
    Move killers[MAX_PLY][2];
    int  history[8][8][8][8];   // [fr][fc][tr][tc]

    // ── Time control ───────────────────────────────────────────
    std::chrono::steady_clock::time_point searchStart;
    int timeLimitMs;
    bool timeUp() const;

    // ── Internal move mechanics ────────────────────────────────
    UndoInfo makeMove(const Move& m);
    void     unmakeMove(const Move& m, const UndoInfo& u);

    void generatePseudoLegal(Spalva s, std::vector<Move>& out) const;
    bool leavesKingInCheck(const Move& m, Spalva s);

    void scoreMoves(std::vector<Move>& mv, int ply, const Move& ttBest) const;

    // ── Search ────────────────────────────────────────────────
    int search(int depth, int alpha, int beta, int ply, bool nullOk);
    int qsearch(int alpha, int beta, int ply);

    // ── Evaluation ────────────────────────────────────────────
    int evalTapered() const;
    int pstScore(PType pt, Spalva c, int r, int col, bool mg) const;

    // ── Utility ───────────────────────────────────────────────
    bool isPathClear(int fr, int fc, int tr, int tc) const;
    static constexpr Spalva opp(Spalva s) { return s == BALTA ? JUODA : BALTA; }
};
