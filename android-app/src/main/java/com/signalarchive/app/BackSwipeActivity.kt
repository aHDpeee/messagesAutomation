package com.signalarchive.app

import android.app.Activity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import kotlin.math.abs

/** A horizontal gesture on empty screen space follows Android's Back action. */
open class BackSwipeActivity : Activity() {
    protected open val emptySpaceBackSwipeEnabled: Boolean = true
    private var startX = 0f
    private var startY = 0f
    private var emptyStart = false

    private fun overControl(view: View, x: Float, y: Float): Boolean {
        if (view.visibility != View.VISIBLE) return false
        val pos = IntArray(2); view.getLocationOnScreen(pos)
        if (x < pos[0] || y < pos[1] || x >= pos[0] + view.width || y >= pos[1] + view.height) return false
        if (view is ViewGroup) {
            for (i in view.childCount - 1 downTo 0) if (overControl(view.getChildAt(i), x, y)) return true
            return false
        }
        return view.isClickable || view.isLongClickable || view is android.widget.EditText
    }

    @Suppress("DEPRECATION")
    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (!emptySpaceBackSwipeEnabled) return super.dispatchTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                startX = event.rawX; startY = event.rawY
                emptyStart = !overControl(window.decorView, startX, startY)
            }
            MotionEvent.ACTION_UP -> {
                val dx = event.rawX - startX; val dy = event.rawY - startY
                val threshold = 100 * resources.displayMetrics.density
                if (emptyStart && dx >= threshold && dx > abs(dy) * 1.5f &&
                    !overControl(window.decorView, event.rawX, event.rawY)) {
                    emptyStart = false
                    val cancel = MotionEvent.obtain(event).apply { action = MotionEvent.ACTION_CANCEL }
                    try { super.dispatchTouchEvent(cancel) } finally { cancel.recycle() }
                    onBackPressed()
                    return true
                }
                emptyStart = false
            }
            MotionEvent.ACTION_CANCEL -> emptyStart = false
        }
        return super.dispatchTouchEvent(event)
    }
}
