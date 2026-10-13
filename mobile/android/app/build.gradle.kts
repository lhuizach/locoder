plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.locoder.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.locoder.mobile"
        minSdk = 26
        // 34, not 35: Android 15 forces edge-to-edge for apps targeting 35,
        // which the WebView layout doesn't handle yet.
        targetSdk = 34
        versionCode = 7
        versionName = "0.6.0"
    }

    // The web app is the single source of truth: package the one
    // `locoder serve` serves (index.html + fonts/) instead of keeping a copy.
    sourceSets["main"].assets.srcDirs("../../../locoder/serve/web")

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // QR scanner bundled in the app (ZXing): works offline, on any phone,
    // with or without Google Play.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    // ...which uses AndroidX Core without bringing it along.
    implementation("androidx.core:core:1.13.1")
}
