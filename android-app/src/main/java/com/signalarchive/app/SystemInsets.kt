package com.signalarchive.app

import android.app.Activity
import android.os.Build
import android.view.View
import android.view.WindowInsets

/** Keep app content clear of status/navigation bars and the keyboard. */
internal object SystemInsets {
    fun apply(activity: Activity, root: View) {
        if (Build.VERSION.SDK_INT < 30) return // Older systems fit content by default.
        val left = root.paddingLeft; val top = root.paddingTop
        val right = root.paddingRight; val bottom = root.paddingBottom
        activity.window.setDecorFitsSystemWindows(false)
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val keyboard = insets.getInsets(WindowInsets.Type.ime())
            view.setPadding(left + bars.left, top + bars.top, right + bars.right, bottom + maxOf(bars.bottom, keyboard.bottom))
            insets
        }
        root.requestApplyInsets()
    }
}
