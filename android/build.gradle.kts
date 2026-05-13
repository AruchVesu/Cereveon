// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    // ``kotlin.android`` alias is intentionally absent — AGP 9+ provides
    // built-in Kotlin support, the standalone plugin is redundant and
    // applying it produces an extension-already-registered error.  See
    // https://kotl.in/gradle/agp-built-in-kotlin and the matching comment
    // in app/build.gradle.kts.
    alias(libs.plugins.kotlin.serialization) apply false
}