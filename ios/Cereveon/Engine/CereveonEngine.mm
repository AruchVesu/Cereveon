// Drives the SachmatuLenta engine exactly as
// android/app/src/main/cpp/native_chess_engine.cpp does: Black-only bestMove,
// the same FEN length guard, the same strength handling (the engine maps
// strength → depth/time internally).
#import "CereveonEngine.h"
#include "SachmatuLenta.h"

// native_chess_engine.cpp rejects FENs longer than this before parsing.
static constexpr int kMaxFenLen = 256;

static bool CRVLoadFen(SachmatuLenta &engine, NSString *fen) {
    if (fen == nil) { return false; }
    const char *c = [fen UTF8String];
    if (c == nullptr) { return false; }
    int n = 0;
    while (c[n] != '\0') {
        if (++n > kMaxFenLen) { return false; }
    }
    engine.loadFromBoard64(c);
    return true;
}

@interface CRVAIMove ()
- (instancetype)initWithFromX:(NSInteger)fx fromY:(NSInteger)fy
                          toX:(NSInteger)tx toY:(NSInteger)ty
                    promotion:(unichar)promo;
@end

@implementation CRVAIMove
- (instancetype)initWithFromX:(NSInteger)fx fromY:(NSInteger)fy
                          toX:(NSInteger)tx toY:(NSInteger)ty
                    promotion:(unichar)promo {
    if ((self = [super init])) {
        _fromX = fx; _fromY = fy; _toX = tx; _toY = ty; _promotion = promo;
    }
    return self;
}
@end

static CRVAIMove *CRVWrap(const SachmatuLenta::Move &m) {
    if (!m.isValid()) { return nil; }
    return [[CRVAIMove alloc] initWithFromX:m.fromX fromY:m.fromY
                                        toX:m.toX toY:m.toY
                                  promotion:(unichar)m.promo];
}

@implementation CereveonEngine

- (nullable CRVAIMove *)bestMoveForFEN:(NSString *)fen {
    SachmatuLenta engine;
    if (!CRVLoadFen(engine, fen)) { return nil; }
    return CRVWrap(engine.getBestMove(JUODA));  // BLACK only — matches Android
}

- (nullable CRVAIMove *)bestMoveForFEN:(NSString *)fen strength:(NSInteger)strength {
    SachmatuLenta engine;
    if (!CRVLoadFen(engine, fen)) { return nil; }
    return CRVWrap(engine.getBestMove(JUODA, (int)strength));
}

- (uint64_t)perftForFEN:(NSString *)fen depth:(NSInteger)depth {
    SachmatuLenta engine;
    if (!CRVLoadFen(engine, fen)) { return 0; }
    return engine.perft((int)depth);
}

@end
