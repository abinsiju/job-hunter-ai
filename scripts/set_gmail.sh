#!/usr/bin/env bash
# Save your Gmail address and an APP PASSWORD in the macOS Keychain so the dashboard can create Gmail DRAFTS.
# Nothing is ever sent by this app. Create the app password at https://myaccount.google.com/apppasswords
# (needs 2-Step Verification). The password is typed at a hidden prompt and never written to a file or shown.
set -euo pipefail
read -r -p "Gmail address: " ADDR
[ -n "$ADDR" ] || { echo "No address given; nothing saved."; exit 1; }
echo "Paste the 16-character app password (hidden), then press Enter twice when asked:"
security add-generic-password -U -a "$ADDR" -s job-hunter-gmail -w
echo "Saved for $ADDR. Check it in the dashboard: Settings > Gmail drafts."
