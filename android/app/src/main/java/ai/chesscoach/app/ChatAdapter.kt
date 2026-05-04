package ai.chesscoach.app

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

data class ChatMessage(
    val role: String,
    val text: String,
)

/**
 * Atrium chat adapter — inflates `item_chat_coach.xml` for assistant
 * messages and `item_chat_user.xml` for everything else.
 *
 * Per the Atrium handoff, voices are separated by typography rather
 * than by bubble background:
 *   - Coach: left-aligned, mono cyan COACH kicker, italic Cormorant
 *     body 16sp, 1px gradient gutter on the leading edge.
 *   - User:  right-aligned, mono dim YOU kicker, Inter italic 14sp
 *     muted, no fill.
 *
 * Feedback (👍 / 👎) is preserved on coach messages — the layout uses
 * star drawables tinted to cyan / amber / dim depending on tap state.
 * The onFeedback callback shape is unchanged so ChatBottomSheet's
 * existing wiring keeps working.
 */
class ChatAdapter : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private val messages = mutableListOf<ChatMessage>()

    /** Called when the user taps the helpful / not-helpful icon on a coach message. */
    var onFeedback: ((position: Int, isHelpful: Boolean) -> Unit)? = null

    fun addMessage(msg: ChatMessage) {
        messages.add(msg)
        notifyItemInserted(messages.size - 1)
    }

    fun updateLastMessage(text: String) {
        if (messages.isEmpty()) return
        val lastIndex = messages.size - 1
        messages[lastIndex] = messages[lastIndex].copy(text = text)
        notifyItemChanged(lastIndex)
    }

    fun clear() {
        val count = messages.size
        messages.clear()
        notifyItemRangeRemoved(0, count)
    }

    override fun getItemCount() = messages.size

    override fun getItemViewType(position: Int): Int =
        if (messages[position].role == "assistant") TYPE_COACH else TYPE_USER

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == TYPE_COACH) {
            CoachVH(inflater.inflate(R.layout.item_chat_coach, parent, false))
        } else {
            UserVH(inflater.inflate(R.layout.item_chat_user, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val msg = messages[position]
        when (holder) {
            is CoachVH -> {
                holder.body.text = msg.text
                // Reset feedback tint on rebind so a recycled view from
                // a previously rated row doesn't carry the colour over.
                val ctx = holder.itemView.context
                val dim = ContextCompat.getColor(ctx, R.color.atrium_dim)
                holder.up.setColorFilter(dim)
                holder.down.setColorFilter(dim)
                holder.up.setOnClickListener {
                    onFeedback?.invoke(holder.bindingAdapterPosition, true)
                    holder.up.setColorFilter(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
                    holder.down.setColorFilter(dim)
                }
                holder.down.setOnClickListener {
                    onFeedback?.invoke(holder.bindingAdapterPosition, false)
                    holder.down.setColorFilter(ContextCompat.getColor(ctx, R.color.atrium_accent_amber))
                    holder.up.setColorFilter(dim)
                }
            }
            is UserVH -> {
                holder.body.text = msg.text
            }
        }
    }

    private class CoachVH(view: View) : RecyclerView.ViewHolder(view) {
        val body: TextView = view.findViewById(R.id.coachBody)
        val up: ImageButton = view.findViewById(R.id.btnFeedbackUp)
        val down: ImageButton = view.findViewById(R.id.btnFeedbackDown)
    }

    private class UserVH(view: View) : RecyclerView.ViewHolder(view) {
        val body: TextView = view.findViewById(R.id.userBody)
    }

    companion object {
        private const val TYPE_COACH = 1
        private const val TYPE_USER = 0
    }
}
