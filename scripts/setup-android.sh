#!/usr/bin/env bash
# Generates android/local.properties with the Android SDK path for this machine.
# Run once after cloning. Android Studio also generates this file automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROPS="$SCRIPT_DIR/../android/local.properties"

if [ -f "$PROPS" ]; then
    echo "android/local.properties already exists — skipping."
    echo "Delete it and re-run if you need to update the SDK path."
    exit 0
fi

# Discovery order: env vars → platform defaults
if   [ -n "${ANDROID_HOME:-}"     ] && [ -d "$ANDROID_HOME"     ]; then SDK_DIR="$ANDROID_HOME"
elif [ -n "${ANDROID_SDK_ROOT:-}" ] && [ -d "$ANDROID_SDK_ROOT" ]; then SDK_DIR="$ANDROID_SDK_ROOT"
elif [ -d "$HOME/Library/Android/sdk" ]; then SDK_DIR="$HOME/Library/Android/sdk"   # macOS
elif [ -d "$HOME/Android/Sdk"         ]; then SDK_DIR="$HOME/Android/Sdk"           # Linux
else
    echo "ERROR: Android SDK not found."
    echo "Install Android Studio, or set ANDROID_HOME, then re-run."
    exit 1
fi

printf 'sdk.dir=%s\n' "$SDK_DIR" > "$PROPS"
echo "Created android/local.properties"
echo "  sdk.dir=$SDK_DIR"
