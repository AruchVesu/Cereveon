#include <jni.h>
#include <mutex>
#include <string>
#include <android/log.h>
#include "SachmatuLenta.h"

#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "AI_NATIVE", __VA_ARGS__)

// The engine instances below are local per call, but SachmatuLenta's
// transposition table is a single static 40 MB array shared across ALL
// instances (deliberate: per-instance tables would multiply BSS, and the
// cache surviving across calls is the point).  Two threads entering these
// JNI functions concurrently would race on it unsynchronized (audit
// 2026-07-14, P2 #12): a torn entry that still passes the hash check can
// inject a wrong best-move hint into the search.  App call sites are
// sequential today, so this lock is uncontended in practice — it makes
// the "two ViewModels on two dispatchers" future safe by construction
// rather than by convention.
static std::mutex g_ttSearchMutex;

// A legitimate FEN / 64-char board string is well under this bound; reject
// pathologically long input rather than parse it.  This bounds the parser's
// work and keeps the placement loop's column/rank counters small (the board
// write is already guarded by r < 8 && c < 8).
static constexpr int kMaxFenLen = 256;

/**
 * Helper: convert FEN -> engine board
 * Uses a LOCAL engine instance — never global.
 * Returns false (leaving the engine untouched) for null or over-long input.
 */
static bool loadFenIntoEngine(SachmatuLenta& engine, const char* fen) {
    if (fen == nullptr) return false;
    int n = 0;
    while (n <= kMaxFenLen && fen[n] != '\0') ++n;
    if (n > kMaxFenLen) return false;
    // Current SachmatuLenta::loadFromBoard64 handles FEN automatically
    engine.loadFromBoard64(fen);
    return true;
}

extern "C" {

/**
 * The JNI function (PURE, SAFE, SINGLE MOVE)
 * Signature: Java_com_cereveon_myapp_ChessNative_getBestMove
 */
JNIEXPORT jobject JNICALL
Java_com_cereveon_myapp_ChessNative_getBestMove(
        JNIEnv* env,
        jobject /* this */,
        jstring fen
) {
    if (!fen) return nullptr;

    // 1️⃣ Convert FEN
    const char* fenStr = env->GetStringUTFChars(fen, nullptr);
    if (!fenStr) return nullptr;

    // 2️⃣ LOCAL engine instance (🔥 KEY FIX) — but the search below
    // still touches the shared static TT; hold the process-wide lock
    // for the whole load+search (see g_ttSearchMutex above).
    std::lock_guard<std::mutex> ttLock(g_ttSearchMutex);
    SachmatuLenta engine;
    bool loaded = loadFenIntoEngine(engine, fenStr);

    env->ReleaseStringUTFChars(fen, fenStr);

    if (!loaded) return nullptr;

    // 3️⃣ Ask engine for ONE move (BLACK ONLY)
    SachmatuLenta::Move m = engine.getBestMove(JUODA);
    if (!m.isValid()) {
        return nullptr;
    }

    // 4️⃣ Create AIMove Kotlin object
    // Note: FindClass needs the full package name with slashes
    jclass moveCls = env->FindClass("com/cereveon/myapp/AIMove");
    if (!moveCls) {
        LOGE("Could not find AIMove class");
        return nullptr;
    }

    // Get the constructor: AIMove(Int, Int, Int, Int, Int).  The 5th arg
    // carries the promotion piece: m.promo is 'Q'/'R'/'B'/'N' (else 0).
    // Dropping it (the old (IIII)V ctor) left the engine's promoting pawn
    // un-promoted on the back rank AND misfired the human promotion
    // dialog for the AI's move — see AIMove.promo / applyAIMove.
    jmethodID ctor = env->GetMethodID(moveCls, "<init>", "(IIIII)V");
    if (!ctor) {
        LOGE("Could not find AIMove constructor");
        return nullptr;
    }

    // Create the object using move coordinates from the engine
    // Engine uses fromX, fromY, toX, toY (+ promo)
    return env->NewObject(
        moveCls,
        ctor,
        m.fromX,
        m.fromY,
        m.toX,
        m.toY,
        static_cast<jint>(m.promo)
    );
}

JNIEXPORT jobject JNICALL
Java_com_cereveon_myapp_ChessNative_getBestMoveWithStrength(
        JNIEnv* env,
        jobject /* this */,
        jstring fen,
        jint strengthLevel
) {
    if (!fen) return nullptr;

    const char* fenStr = env->GetStringUTFChars(fen, nullptr);
    if (!fenStr) return nullptr;

    // Same shared-TT lock as getBestMove above.
    std::lock_guard<std::mutex> ttLock(g_ttSearchMutex);
    SachmatuLenta engine;
    bool loaded = loadFenIntoEngine(engine, fenStr);
    env->ReleaseStringUTFChars(fen, fenStr);
    if (!loaded) return nullptr;

    SachmatuLenta::Move m = engine.getBestMove(JUODA, static_cast<int>(strengthLevel));
    if (!m.isValid()) return nullptr;

    jclass moveCls = env->FindClass("com/cereveon/myapp/AIMove");
    if (!moveCls) { LOGE("Could not find AIMove class"); return nullptr; }

    jmethodID ctor = env->GetMethodID(moveCls, "<init>", "(IIIII)V");
    if (!ctor) { LOGE("Could not find AIMove constructor"); return nullptr; }

    return env->NewObject(moveCls, ctor, m.fromX, m.fromY, m.toX, m.toY,
                          static_cast<jint>(m.promo));
}

} // extern "C"
