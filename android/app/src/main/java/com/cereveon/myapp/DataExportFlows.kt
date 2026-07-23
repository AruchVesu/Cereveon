package com.cereveon.myapp

import android.util.Log
import android.widget.Toast
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import java.io.IOException
import java.time.LocalDate
import kotlinx.coroutines.launch

/**
 * "Download my data" flow shared by every host of [SettingsBottomSheet]
 * (GDPR Art. 15/20; GET /auth/me/export — docs/API_CONTRACTS.md §42).
 *
 * Mechanism: Storage Access Framework (ACTION_CREATE_DOCUMENT via
 * [ActivityResultContracts.CreateDocument]) — zero permissions on every
 * supported API level (minSdk 26 rules out MediaStore.Downloads, which
 * is Q+), and the user explicitly picks where their data lands: the
 * right agency model for a data-subject-rights surface.
 *
 * Order of operations is fetch-THEN-pick: the export document is
 * fetched first and held in [pendingJson]; only on success does the
 * system file picker open.  The alternative (pick first) would create
 * an empty file the flow then has to clean up whenever the network
 * call fails.  [pendingJson] is object state rather than plumbed
 * through both hosts because the picker is modal — exactly one
 * download can be in flight — and every result path clears it.
 * Process death between fetch and pick loses the pending document; the
 * result callback then asks the user to retry rather than writing an
 * empty file.  SettingsDownloadDataSourcePinTest pins the flow shape.
 */
object DataExportFlows {

    /** File-name prefix, pinned by SettingsDownloadDataSourcePinTest. */
    const val EXPORT_FILE_PREFIX = "cereveon-data-export-"

    private var pendingJson: String? = null

    /** Picker suggestion, e.g. ``cereveon-data-export-2026-07-17.json``. */
    fun suggestedFileName(date: LocalDate = LocalDate.now()): String =
        "$EXPORT_FILE_PREFIX$date.json"

    /**
     * Register the SAF save launcher for [activity].  MUST run before
     * the host reaches STARTED — both hosts call this from a property
     * initializer, the androidx-recommended registration site.
     */
    fun registerSaveLauncher(activity: AppCompatActivity): ActivityResultLauncher<String> =
        activity.registerForActivityResult(
            ActivityResultContracts.CreateDocument("application/json"),
        ) { uri ->
            val json = pendingJson
            pendingJson = null
            if (uri == null) {
                // Picker cancelled — nothing was written, nothing to say.
                return@registerForActivityResult
            }
            if (json == null) {
                // Process death (or a stale second result) between fetch
                // and pick — never write an empty file.
                Toast.makeText(
                    activity,
                    "Download expired — please try again.",
                    Toast.LENGTH_LONG,
                ).show()
                return@registerForActivityResult
            }
            try {
                val stream = activity.contentResolver.openOutputStream(uri)
                    ?: throw IOException("content resolver returned no stream")
                stream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
                Toast.makeText(activity, "Your data was saved.", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Log.w("DATA_EXPORT", "failed writing export document", e)
                Toast.makeText(
                    activity,
                    "Couldn't save the file. Please try again.",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }

    /**
     * Fetch the export document, then open the picker.  A
     * non-destructive read — no confirmation dialog, unlike
     * [AccountFlows.confirmAndDeleteAccount].
     */
    fun startDownload(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
        saveLauncher: ActivityResultLauncher<String>,
    ) {
        val token = authRepo.getToken() ?: return
        Toast.makeText(activity, "Preparing your data…", Toast.LENGTH_SHORT).show()
        activity.lifecycleScope.launch {
            when (val result = authApiClient.exportData(token)) {
                is ApiResult.Success -> {
                    pendingJson = result.data
                    saveLauncher.launch(suggestedFileName())
                }
                is ApiResult.HttpError -> {
                    Log.w("DATA_EXPORT", "export HTTP ${result.code}")
                    val message = if (result.code == 401) {
                        "Session expired — sign in and try again."
                    } else {
                        "Couldn't prepare your data. Please try again."
                    }
                    Toast.makeText(activity, message, Toast.LENGTH_LONG).show()
                }
                else ->
                    Toast.makeText(
                        activity,
                        "Network error. Please try again.",
                        Toast.LENGTH_LONG,
                    ).show()
            }
        }
    }
}
