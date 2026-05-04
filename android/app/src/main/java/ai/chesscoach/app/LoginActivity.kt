package ai.chesscoach.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch

/**
 * Login screen that authenticates the user against the coach backend.
 *
 * Flow:
 *  1. User enters email + password and taps Sign In or Create Account.
 *  2. [AuthApiClient.login] / [AuthApiClient.register] is called; on success
 *     the JWT is stored in [AuthRepository] (backed by [EncryptedSharedPreferences]).
 *  3. Routing:
 *     - Login success → [HomeActivity] (or [OnboardingActivity] if a prior
 *       registration was abandoned before the calibration step completed).
 *     - Registration success → [OnboardingActivity] for skill calibration
 *       (handoff #2), then on Continue → [HomeActivity].
 *
 * Token expiry: [AuthRepository.isLoggedIn] checks the `exp` claim; if the
 * stored token is already expired when the app starts, [MainActivity] /
 * [HomeActivity] redirect back here automatically.
 */
class LoginActivity : AppCompatActivity() {

    private lateinit var etEmail: TextInputEditText
    private lateinit var etPassword: TextInputEditText
    private lateinit var btnLogin: Button
    private lateinit var btnRegister: Button
    private lateinit var tvError: TextView
    private lateinit var progressBar: ProgressBar

    private val authApiClient: AuthApiClient by lazy {
        HttpAuthApiClient(baseUrl = BuildConfig.COACH_API_BASE)
    }

    private val authRepository: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // If already logged in, go straight to the game — avoid showing the
        // login form when the user re-opens the app with a valid session.
        if (authRepository.isLoggedIn()) {
            launchPostAuth()
            return
        }

        setContentView(R.layout.activity_login)

        etEmail = findViewById(R.id.etEmail)
        etPassword = findViewById(R.id.etPassword)
        btnLogin = findViewById(R.id.btnLogin)
        btnRegister = findViewById(R.id.btnRegister)
        tvError = findViewById(R.id.tvError)
        progressBar = findViewById(R.id.progressBar)

        btnLogin.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter your email and password.")
                return@setOnClickListener
            }

            performLogin(email, password)
        }

        btnRegister.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter an email and password to create an account.")
                return@setOnClickListener
            }

            performRegister(email, password)
        }
    }

    // ---------------------------------------------------------------------------
    // Login logic
    // ---------------------------------------------------------------------------

    private fun performLogin(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.login(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    launchPostAuth()
                }

                is ApiResult.HttpError -> {
                    val message =
                        if (result.code == 401) {
                            "Invalid email or password."
                        } else {
                            "Server error (${result.code}). Please try again."
                        }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun performRegister(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        btnRegister.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.register(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    launchOnboarding()
                }

                is ApiResult.HttpError -> {
                    val message = when (result.code) {
                        409 -> "An account with this email already exists."
                        else -> "Registration failed (${result.code}). Please try again."
                    }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun showError(message: String) {
        progressBar.visibility = View.GONE
        btnLogin.isEnabled = true
        btnRegister.isEnabled = true
        tvError.text = message
        tvError.visibility = View.VISIBLE
    }

    /**
     * Decide where an authenticated user lands.  Newly-registered users that
     * abandoned the calibration flow (or any future case where onboarding is
     * incomplete) get routed through [OnboardingActivity] first; everyone else
     * goes to [HomeActivity], the post-auth landing.
     */
    private fun launchPostAuth() {
        if (OnboardingActivity.isCompleted(this)) {
            launchHome()
        } else {
            launchOnboarding()
        }
    }

    private fun launchHome() {
        startActivity(
            Intent(this, HomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }

    private fun launchOnboarding() {
        // Enter at step 1/3 (Welcome).  Step 2/3 (calibration) and
        // step 3/3 (completion) are reached by the Begin / Continue
        // buttons on each screen; HomeActivity is the post-onboarding
        // landing.
        startActivity(
            Intent(this, OnboardingWelcomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }
}
