import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// Release signing key (git-ignored). See keystore.properties.
val signingProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.feeflow.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.feeflow.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 21
        versionName = "2.1.0"
        // Emulator -> host machine. Change in the app's sign-in screen for a real phone.
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"http://10.0.2.2:8000\"")
        manifestPlaceholders["cleartext"] = "true"
    }

    signingConfigs {
        if (signingProps.getProperty("storeFile") != null) {
            create("feeflow") {
                storeFile = rootProject.file(signingProps.getProperty("storeFile"))
                storePassword = signingProps.getProperty("storePassword")
                keyAlias = signingProps.getProperty("keyAlias")
                keyPassword = signingProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("feeflow")
            // Production: HTTPS only.
            manifestPlaceholders["cleartext"] = "false"
            // Set the deployed backend with: ./gradlew assembleRelease -PreleaseServerUrl=https://feeflow-api-iy66.onrender.com
            val releaseUrl = providers.gradleProperty("releaseServerUrl").getOrElse("https://feeflow-api-iy66.onrender.com")
            require(releaseUrl.startsWith("https://")) { "releaseServerUrl must be https://" }
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"$releaseUrl\"")
        }
        // Signed like release, but may use plain HTTP to a test server on the local Wi-Fi.
        create("pilot") {
            initWith(getByName("release"))
            matchingFallbacks += "release"
            manifestPlaceholders["cleartext"] = "true"
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"${providers.gradleProperty("pilotServerUrl").getOrElse("http://192.168.31.225:8000")}\"")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.3")
    implementation("androidx.work:work-runtime-ktx:2.9.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}

// Live end-to-end test against a running backend: FEEFLOW_E2E_URL=http://localhost:8000 gradlew test
tasks.withType<Test>().configureEach {
    listOf("FEEFLOW_E2E_URL", "FEEFLOW_E2E_PAY_TOKEN").forEach { k -> System.getenv(k)?.let { environment(k, it) } }
    testLogging { events("passed", "skipped", "failed"); showStandardStreams = false }
}
