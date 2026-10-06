package com.oops.app

import android.app.Application
import com.oops.app.engine.OopsEngine

class OopsApp : Application() {
    override fun onCreate() {
        super.onCreate()
        OopsEngine.init(this)
    }
}
