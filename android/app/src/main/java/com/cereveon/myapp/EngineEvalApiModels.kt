package com.cereveon.myapp

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.nullable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/**
 * Serializer that maps an empty JSON string to a Kotlin null on
 * deserialization (and a Kotlin null to a JSON null on serialization).
 *
 * Preserves the pre-Sprint-4.3.C parser contract where
 * ``optString("best_move").takeIf { it.isNotEmpty() }`` folded the
 * empty-string sentinel into null — the engine emits ``""`` when no
 * legal move exists, and the UI relies on ``bestMove == null`` to
 * suppress the move arrow rather than rendering an empty hint.
 */
@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
private object EmptyStringAsNullSerializer : KSerializer<String?> {
    private val nullableDelegate = String.serializer().nullable
    override val descriptor: SerialDescriptor = nullableDelegate.descriptor
    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) encoder.encodeNull() else encoder.encodeString(value)
    }
    override fun deserialize(decoder: Decoder): String? =
        decoder.decodeSerializableValue(nullableDelegate)?.takeIf { it.isNotEmpty() }
}

/**
 * Request/response models for POST /engine/eval (server.py).
 *
 * Schema documented in docs/API_CONTRACTS.md §1.
 *
 * Migrated from hand-rolled ``org.json.JSONObject`` parsing onto
 * kotlinx-serialization in Sprint 4.3.C.  ``@SerialName("best_move")``
 * preserves the snake_case wire format that server.py emits while
 * keeping the camelCase Kotlin property name [bestMove].
 */

/**
 * Request body for POST /engine/eval.
 *
 * [fen] Current board position in Forsyth-Edwards Notation.  Note:
 * unlike the pre-Sprint-4.x host_app contract, the new server.py
 * route does NOT accept ``"startpos"`` — send a real FEN.
 */
@Serializable
data class EngineEvalRequest(val fen: String)

/**
 * Response from POST /engine/eval.
 *
 * [score]    Centipawn evaluation from White's perspective.
 *            Positive → White is ahead; negative → Black is ahead.
 *            Null when the engine is unavailable (fallback path).
 * [bestMove] Best move in UCI notation (e.g. "e2e4").
 *            Null when there are no legal moves or engine unavailable.
 * [source]   ``"engine"`` on the happy path; ``"unavailable"`` when
 *            the engine pool is down (post-host_app-retirement
 *            server.py contract — old ``"cache"`` / ``"book"`` source
 *            values are gone).
 */
@Serializable
data class EngineEvalResponse(
    val score: Int? = null,
    @SerialName("best_move")
    @Serializable(with = EmptyStringAsNullSerializer::class)
    val bestMove: String? = null,
    val source: String = "engine",
)
