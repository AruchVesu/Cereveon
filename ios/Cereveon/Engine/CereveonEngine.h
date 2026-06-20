// iOS bridge for the Android SachmatuLenta engine (android/app/src/main/cpp).
//
// Pure Objective-C interface — no C++ types — so it can be imported from Swift
// through the app's bridging header (Cereveon-Bridging-Header.h).
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// A move in the engine's raw, Black-relative board coordinates (row/col).
@interface CRVAIMove : NSObject
@property (nonatomic, readonly) NSInteger fromX;
@property (nonatomic, readonly) NSInteger fromY;
@property (nonatomic, readonly) NSInteger toX;
@property (nonatomic, readonly) NSInteger toY;
@property (nonatomic, readonly) unichar promotion;  // 0 if none, else 'Q','R','B','N'
@end

@interface CereveonEngine : NSObject
/// Best move for BLACK in the given FEN, at engine-default strength (100).
/// Mirrors native_chess_engine.cpp: the FEN side-to-move field is ignored — the
/// engine always computes Black's reply (the app's AI plays Black). Returns nil
/// when there is no legal move (or the FEN is invalid).
- (nullable CRVAIMove *)bestMoveForFEN:(NSString *)fen;
/// As above, with an explicit strength in [0, 100].
- (nullable CRVAIMove *)bestMoveForFEN:(NSString *)fen strength:(NSInteger)strength;
/// perft node count — move-generation correctness check used by CI.
- (uint64_t)perftForFEN:(NSString *)fen depth:(NSInteger)depth;
@end

NS_ASSUME_NONNULL_END
