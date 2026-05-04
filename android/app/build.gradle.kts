plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing — read from Gradle properties (MYAPP_UPLOAD_*).
// Local dev: set real values in ~/.gradle/gradle.properties (never committed).
// CI: the workflow appends real values to android/gradle.properties before build.
// Empty placeholder values in android/gradle.properties resolve to null here,
// so the signingConfig block is skipped and Gradle produces an unsigned APK.
val releaseKeystoreFile: String? =
    (findProperty("MYAPP_UPLOAD_STORE_FILE") as String?).takeIf { !it.isNullOrBlank() }
val releaseKeyAlias: String? =
    (findProperty("MYAPP_UPLOAD_KEY_ALIAS") as String?).takeIf { !it.isNullOrBlank() }
val releaseKeyPassword: String? =
    (findProperty("MYAPP_UPLOAD_KEY_PASSWORD") as String?).takeIf { !it.isNullOrBlank() }
val releaseStorePassword: String? =
    (findProperty("MYAPP_UPLOAD_STORE_PASSWORD") as String?).takeIf { !it.isNullOrBlank() }
val hasReleaseSigningConfig: Boolean = listOf(
    releaseKeystoreFile, releaseKeyAlias, releaseKeyPassword, releaseStorePassword,
).all { it != null }

android {
    namespace = "ai.chesscoach.app"
    compileSdk = 36

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "ai.chesscoach.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Coach backend — points at the production server by default.
        // Override COACH_API_BASE / COACH_API_KEY env vars to redirect to a
        // local dev server (e.g. http://10.0.2.2:8000 for the Android emulator).
        buildConfigField("String", "COACH_API_BASE", "\"https://cereveon.com\"")
        buildConfigField("String", "COACH_API_KEY", "\"dev-key\"")

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
            // Force IPv4 so HttpURLConnection and MockWebServer use the same
            // loopback address on all platforms (avoids Windows IPv6/keep-alive races).
            all { it.jvmArgs("-Djava.net.preferIPv4Stack=true") }
        }
    }

    if (hasReleaseSigningConfig) {
        signingConfigs {
            create("release") {
                storeFile = file(releaseKeystoreFile!!)
                keyAlias = releaseKeyAlias!!
                keyPassword = releaseKeyPassword!!
                storePassword = releaseStorePassword!!
            }
        }
    }

    buildTypes {
        release {
            if (hasReleaseSigningConfig) {
                signingConfig = signingConfigs.getByName("release")
            }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // COACH_API_BASE is a plain configuration value (not a secret) — it is
            // visible in the decompiled APK regardless of obfuscation. Pass it as a
            // GitHub Actions variable (vars.COACH_API_BASE), not a secret.
            // COACH_API_KEY ends up in the APK binary and is therefore semi-public;
            // treat it as a rate-limit shield only, not real authentication. Real
            // per-user auth uses JWT tokens issued by /auth/login. Pass it as a
            // GitHub Actions secret (secrets.COACH_API_KEY).
            //
            // Falls back to dev defaults when env vars are absent or blank.
            // vars.COACH_API_BASE expands to "" (not null) when unset in GitHub Actions,
            // so treat blank the same as absent to avoid a spurious HTTPS guard failure.
            val rawProdApiBase: String? = System.getenv("COACH_API_BASE")?.takeIf { it.isNotBlank() }
            val prodApiBase: String = rawProdApiBase ?: "https://cereveon.com"
            val prodApiKey: String = System.getenv("COACH_API_KEY")?.takeIf { it.isNotBlank() } ?: "dev-key"
            // Hard-fail if COACH_API_BASE is explicitly provided but uses plain HTTP.
            if (rawProdApiBase != null && !prodApiBase.startsWith("https://")) {
                error(
                    "Release build requires COACH_API_BASE to start with https://. " +
                    "Got: $prodApiBase — set a valid TLS endpoint."
                )
            }
            buildConfigField("String", "COACH_API_BASE", "\"$prodApiBase\"")
            buildConfigField("String", "COACH_API_KEY", "\"$prodApiKey\"")
        }
        debug {
            // Allow developers to point at a remote server (e.g. Hetzner) without
            // modifying source code — export COACH_API_BASE / COACH_API_KEY in the
            // shell, then re-sync Gradle (Step 3.4).
            val debugApiBase: String = System.getenv("COACH_API_BASE") ?: "https://cereveon.com"
            val debugApiKey: String = System.getenv("COACH_API_KEY") ?: "dev-key"
            buildConfigField("String", "COACH_API_BASE", "\"$debugApiBase\"")
            buildConfigField("String", "COACH_API_KEY", "\"$debugApiKey\"")
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
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("com.google.android.material:material:1.13.0")

    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-ktx:1.12.2")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    // Real org.json implementation — overrides the Android stub (android.jar) so that
    // production clients that use JSONObject can be exercised in host JVM unit tests.
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
}
