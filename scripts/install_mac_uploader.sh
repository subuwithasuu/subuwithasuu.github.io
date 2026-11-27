#!/usr/bin/env bash
# Installs the Mac video-pin uploader as a login agent: it runs when you log in to
# your Mac and then hourly; it only uploads once a week. Undo with:
#   launchctl bootout gui/$(id -u)/com.easyproteinkitchen.videoupload
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.easyproteinkitchen.videoupload
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$REPO/.venv/bin/python</string>
    <string>$REPO/scripts/mac_video_upload.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>3600</integer>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key><string>$LOGDIR/easyprotein-video-upload.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/easyprotein-video-upload.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed: $PLIST (log: $LOGDIR/easyprotein-video-upload.log)"

# Open the Claude app in the background at login, so the scheduled
# "Pinterest health check" task runs right after you log in.
OPEN_LABEL=com.easyproteinkitchen.openclaude
OPEN_PLIST="$HOME/Library/LaunchAgents/$OPEN_LABEL.plist"
cat > "$OPEN_PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$OPEN_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/open</string><string>-g</string><string>-a</string><string>Claude</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$OPEN_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$OPEN_PLIST"
echo "installed: $OPEN_PLIST"
