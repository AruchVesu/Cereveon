package com.cereveon.myapp

import android.app.Dialog
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import androidx.fragment.app.DialogFragment

class PromotionDialogFragment(
    private val onSelect: (Char) -> Unit
) : DialogFragment() {

    override fun onCreateDialog(savedInstanceState: Bundle?): Dialog {
        val dialog = Dialog(requireContext())
        dialog.setCancelable(false)

        val layout = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(24, 24, 24, 24)
            // Surface token (was a near-identical literal #0B0F14) so
            // bright mode flips it via values-notnight/colors.xml.
            setBackgroundColor(
                androidx.core.content.ContextCompat.getColor(
                    requireContext(), R.color.atrium_bg_surface,
                ),
            )
        }

        fun add(label: String, piece: Char) {
            val btn = Button(requireContext()).apply {
                text = label
                textSize = 20f
                minWidth = 120
                setOnClickListener {
                    onSelect(piece)
                    dismiss()
                }
            }
            layout.addView(btn)
        }

        add("Queen ♕", 'Q')
        add("Rook ♖", 'R')
        add("Bishop ♗", 'B')
        add("Knight ♘", 'N')   // ✅ now clearly visible

        dialog.setContentView(layout)
        return dialog
    }
}
