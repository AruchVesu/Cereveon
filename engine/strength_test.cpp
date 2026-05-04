// Strength-parametrized engine tests.
// Compile from repo root:
//   g++ -std=c++17 -O2 -I android/app/src/main/cpp \
//       engine/strength_test.cpp android/app/src/main/cpp/SachmatuLenta.cpp \
//       -o engine/strength_bin && ./engine/strength_bin
//
// Exit 0 = all passed; non-zero = regression.

#include "SachmatuLenta.h"
#include <cstdio>
#include <cstring>

static int failures = 0;

static void check(const char* label, bool ok) {
    if (ok) printf("  PASS  %s\n", label);
    else    { printf("  FAIL  %s\n", label); ++failures; }
}

// At every strength level the engine must return a legal move from a normal position.
static void testAlwaysValidMove() {
    printf("\nTest: valid move returned at all strength levels\n");
    const char* fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
    for (int s : {0, 25, 50, 75, 100}) {
        SachmatuLenta engine;
        engine.loadFromBoard64(fen);
        SachmatuLenta::Move m = engine.getBestMove(JUODA, s);
        char label[64];
        snprintf(label, sizeof(label), "strength=%d returns valid move", s);
        check(label, m.isValid());
    }
}

// At strength=100 the engine must capture a free queen.
// Position: black rook a5 (row=3,col=0); white queen h5 (row=3,col=7) — same rank.
// Kings: black on a1 (row=7,col=0), white on h1 (row=7,col=7).
// Black to move — Ra5xh5 wins the queen with check.
static void testHangingQueenCapture() {
    printf("\nTest: strength=100 captures hanging queen\n");
    const char* fen = "8/8/8/r6Q/8/8/8/k6K b - - 0 1";
    SachmatuLenta engine;
    engine.loadFromBoard64(fen);
    SachmatuLenta::Move m = engine.getBestMove(JUODA, 100);
    // Black rook a5(3,0) → h5(3,7)
    bool ok = m.isValid() && m.fromX == 3 && m.fromY == 0 && m.toX == 3 && m.toY == 7;
    check("strength=100: Ra5xh5 (3,0)->(3,7)", ok);
}

// At strength=100 the engine (white) must find the only mating move.
// Position: k7/8/1K6/8/8/8/8/7R w - - 0 1 → Rh8#
static void testMateInOneFullStrength() {
    printf("\nTest: strength=100 finds mate-in-1\n");
    const char* fen = "k7/8/1K6/8/8/8/8/7R w - - 0 1";
    SachmatuLenta engine;
    engine.loadFromBoard64(fen);
    SachmatuLenta::Move m = engine.getBestMove(BALTA, 100);
    bool ok = m.isValid() && m.fromX == 7 && m.fromY == 7 && m.toX == 0 && m.toY == 7;
    check("strength=100: Rh8# (7,7)->(0,7)", ok);
}

// Strength=0 must still return a valid move (no crash, no null).
static void testMinStrengthNotNull() {
    printf("\nTest: strength=0 does not crash or return invalid\n");
    const char* fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    SachmatuLenta engine;
    engine.loadFromBoard64(fen);
    SachmatuLenta::Move m = engine.getBestMove(BALTA, 0);
    check("strength=0: move is valid", m.isValid());
}

int main() {
    printf("=== SachmatuLenta adaptive-strength tests ===\n");
    testAlwaysValidMove();
    testHangingQueenCapture();
    testMateInOneFullStrength();
    testMinStrengthNotNull();
    printf("\n=== %s (%d failure%s) ===\n",
           failures ? "FAILED" : "PASSED",
           failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
