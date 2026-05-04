#include <jni.h>
#include <string>
#include <android/log.h>
#include "SachmatuLenta.h"

#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "AI_NATIVE", __VA_ARGS__)

/**
 * Helper: convert FEN -> engine board
 * Uses a LOCAL engine instance — never global.
 */
static void loadFenIntoEngine(SachmatuLenta& engine, const char* fen) {
    // Current SachmatuLenta::loadFromBoard64 handles FEN automatically
    engine.loadFromBoard64(fen);
}

extern "C" {

/**
 * The JNI function (PURE, SAFE, SINGLE MOVE)
 * Signature: Java_ai_chesscoach_app_ChessNative_getBestMove
 */
JNIEXPORT jobject JNICALL
Java_ai_chesscoach_app_ChessNative_getBestMove(
        JNIEnv* env,
        jobject /* this */,
        jstring fen
) {
    if (!fen) return nullptr;

    // 1️⃣ Convert FEN
    const char* fenStr = env->GetStringUTFChars(fen, nullptr);
    if (!fenStr) return nullptr;

    // 2️⃣ LOCAL engine instance (🔥 KEY FIX)
    SachmatuLenta engine;
    loadFenIntoEngine(engine, fenStr);

    env->ReleaseStringUTFChars(fen, fenStr);

    // 3️⃣ Ask engine for ONE move (BLACK ONLY)
    SachmatuLenta::Move m = engine.getBestMove(JUODA);
    if (!m.isValid()) {
        return nullptr;
    }

    // 4️⃣ Create AIMove Kotlin object
    // Note: FindClass needs the full package name with slashes
    jclass moveCls = env->FindClass("ai/chesscoach/app/AIMove");
    if (!moveCls) {
        LOGE("Could not find AIMove class");
        return nullptr;
    }

    // Get the constructor: AIMove(Int, Int, Int, Int)
    jmethodID ctor = env->GetMethodID(moveCls, "<init>", "(IIII)V");
    if (!ctor) {
        LOGE("Could not find AIMove constructor");
        return nullptr;
    }

    // Create the object using move coordinates from the engine
    // Engine uses fromX, fromY, toX, toY
    return env->NewObject(
        moveCls,
        ctor,
        m.fromX,
        m.fromY,
        m.toX,
        m.toY
    );
}

JNIEXPORT jobject JNICALL
Java_ai_chesscoach_app_ChessNative_getBestMoveWithStrength(
        JNIEnv* env,
        jobject /* this */,
        jstring fen,
        jint strengthLevel
) {
    if (!fen) return nullptr;

    const char* fenStr = env->GetStringUTFChars(fen, nullptr);
    if (!fenStr) return nullptr;

    SachmatuLenta engine;
    loadFenIntoEngine(engine, fenStr);
    env->ReleaseStringUTFChars(fen, fenStr);

    SachmatuLenta::Move m = engine.getBestMove(JUODA, static_cast<int>(strengthLevel));
    if (!m.isValid()) return nullptr;

    jclass moveCls = env->FindClass("ai/chesscoach/app/AIMove");
    if (!moveCls) { LOGE("Could not find AIMove class"); return nullptr; }

    jmethodID ctor = env->GetMethodID(moveCls, "<init>", "(IIII)V");
    if (!ctor) { LOGE("Could not find AIMove constructor"); return nullptr; }

    return env->NewObject(moveCls, ctor, m.fromX, m.fromY, m.toX, m.toY);
}

} // extern "C"
