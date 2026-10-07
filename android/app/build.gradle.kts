import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
}

// Volc ASR credentials and the backend URL come from android/local.properties (gitignored)
// or environment variables, and are baked into BuildConfig.
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}

fun configValue(name: String, default: String = ""): String =
    (localProps.getProperty(name) ?: System.getenv(name) ?: default)
        .replace("\\", "\\\\").replace("\"", "\\\"")

android {
    namespace = "com.oops.app"
    // Current androidx.core / OkHttp require compiling against 37; runtime behaviour stays at targetSdk 36.
    compileSdk {
        version = release(37) {
            minorApiLevel = 2
        }
    }

    defaultConfig {
        applicationId = "com.oops.app"
        minSdk = 26
        // Deliberate Android 16 device baseline from the implementation plan.
        //noinspection OldTargetApi
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "com.oops.app.RemoteBackendSmoke"

        buildConfigField("String", "VOLC_API_KEY", "\"${configValue("VOLC_API_KEY")}\"")
        buildConfigField("String", "VOLC_APP_ID", "\"${configValue("VOLC_APP_ID")}\"")
        buildConfigField("String", "VOLC_ACCESS_TOKEN", "\"${configValue("VOLC_ACCESS_TOKEN")}\"")
        buildConfigField(
            "String", "VOLC_RESOURCE_ID",
            "\"${configValue("VOLC_RESOURCE_ID", "volc.seedasr.sauc.duration")}\"",
        )
        buildConfigField("String", "BACKEND_BASE_URL", "\"${configValue("OOPS_BACKEND_URL")}\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.androidx.webkit)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)

    testImplementation(libs.junit)
    testImplementation(libs.org.json)
    testImplementation(libs.kotlinx.coroutines.test)
}
