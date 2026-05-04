package ai.chesscoach.app

/**
 * In-memory chat session store for the long-form coaching mode.
 *
 * Holds the ordered conversation history for the current session.
 * The history is capped at [maxMessages] entries; when the cap is exceeded
 * the oldest message is dropped from the front to keep memory bounded.
 *
 * Pure Kotlin — no Android framework dependencies, fully JVM-testable.
 *
 * Thread safety: NOT thread-safe. Access only from the main thread.
 */
class ChatSessionStore(val maxMessages: Int = 50) {

    private val _messages = mutableListOf<ChatMessage>()

    /** Snapshot of the current message history (immutable copy). */
    val messages: List<ChatMessage> get() = _messages.toList()

    /** Number of messages currently in the store. */
    val size: Int get() = _messages.size

    /** True when no messages are stored. */
    val isEmpty: Boolean get() = _messages.isEmpty()

    /**
     * Append a message to the history and return it.
     *
     * @param role    Must be "user" or "assistant".
     * @param text    Must not be blank.
     * @throws IllegalArgumentException if [role] or [text] are invalid.
     */
    fun addMessage(role: String, text: String): ChatMessage {
        require(role == "user" || role == "assistant") {
            "role must be 'user' or 'assistant', got: '$role'"
        }
        require(text.isNotBlank()) {
            "message text must not be blank"
        }
        val msg = ChatMessage(role = role, text = text)
        _messages.add(msg)
        if (_messages.size > maxMessages) {
            _messages.removeAt(0)
        }
        return msg
    }

    /** Remove all messages. */
    fun clear() {
        _messages.clear()
    }

    /** All user messages in insertion order. */
    fun userMessages(): List<ChatMessage> = _messages.filter { it.role == "user" }

    /** All assistant messages in insertion order. */
    fun assistantMessages(): List<ChatMessage> = _messages.filter { it.role == "assistant" }
}
