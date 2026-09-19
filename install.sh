#!/usr/bin/env bash
# Feed Tray installer — RSS panel button + AI summaries for GNOME 42-44
# Usage: ./install.sh   (env: FEED_TRAY_LLM_URL, FEED_TRAY_LLM_KEY,
#                        FEED_TRAY_SKIP_SYSTEMD=1)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
EXT_DIR="$XDG_DATA/gnome-shell/extensions/feed-tray@local"
BIN_DIR="$HOME/.local/bin"
FEEDS_DIR="$XDG_CONF/feeds"

echo "==> Feed Tray installer"

command -v python3 >/dev/null || { echo "ERROR: python3 is required"; exit 1; }

if command -v gnome-shell >/dev/null; then
    GS_MAJOR="$(gnome-shell --version | grep -oE '[0-9]+\.' | head -1 | tr -d '.')"
    echo "==> Detected GNOME $GS_MAJOR"
    if [ "$GS_MAJOR" -ge 45 ] || [ "$GS_MAJOR" -lt 42 ]; then
        echo "WARNING: this extension targets GNOME 42-44 (legacy API)."
        echo "         GNOME $GS_MAJOR needs an ESM rewrite; it may not load."
    fi
fi

mkdir -p "$EXT_DIR" "$BIN_DIR" "$FEEDS_DIR" "$XDG_CONF/systemd/user"

echo "==> Installing panel extension to $EXT_DIR"
cp "$SRC/extension/metadata.json" "$EXT_DIR/"
cp "$SRC/extension/extension.js" "$EXT_DIR/"
cp "$SRC/extension/stylesheet.css" "$EXT_DIR/"

echo "==> Installing tracker to $BIN_DIR/feed-tracker.py"
cp "$SRC/feed-tracker.py" "$BIN_DIR/feed-tracker.py"
chmod +x "$BIN_DIR/feed-tracker.py"

if [ -f "$FEEDS_DIR/feeds.opml" ]; then
    echo "==> Keeping existing $FEEDS_DIR/feeds.opml"
else
    cp "$SRC/feeds.opml" "$FEEDS_DIR/feeds.opml"
    echo "==> Sample feed list installed: $FEEDS_DIR/feeds.opml (edit freely)"
fi

# LLM gateway used for AI summaries (any OpenAI-compatible /chat/completions)
LLM_CFG="$FEEDS_DIR/llm.json"
if [ -f "$LLM_CFG" ]; then
    echo "==> Keeping existing $LLM_CFG"
else
    URL="${FEED_TRAY_LLM_URL:-}"
    KEY="${FEED_TRAY_LLM_KEY:-}"
    if [ -z "$URL" ]; then
        read -rp "LLM gateway base URL (e.g. https://your-gateway/v1): " URL
    fi
    if [ -z "$KEY" ]; then
        read -rsp "LLM API key: " KEY; echo
    fi
    if [ -n "$URL" ] && [ -n "$KEY" ]; then
        printf '{"baseURL": "%s", "apiKey": "%s"}\n' "$URL" "$KEY" > "$LLM_CFG"
        chmod 600 "$LLM_CFG"
    else
        echo "WARNING: missing gateway URL or key — summaries will fail until"
        echo "         you export FEED_TRAY_LLM_URL/FEED_TRAY_LLM_KEY or fill $LLM_CFG"
    fi
fi

if [ "${FEED_TRAY_SKIP_SYSTEMD:-0}" = "1" ] || ! command -v systemctl >/dev/null; then
    echo "==> Skipping systemd timer (manual or no systemctl)"
else
    cp "$SRC/feed-notify.service" "$XDG_CONF/systemd/user/"
    cp "$SRC/feed-notify.timer" "$XDG_CONF/systemd/user/"
    systemctl --user daemon-reload
    systemctl --user enable --now feed-notify.timer
    echo "==> Fetch timer active: systemctl --user status feed-notify.timer"
fi

echo "==> Attempting to enable extension (may need a shell restart first)"
gnome-extensions enable feed-tray@local 2>/dev/null \
    || echo "    Not found yet — run 'gnome-extensions enable feed-tray@local' after restarting the shell."

echo "==> Done. Restart the shell to load the extension:"
echo "    X11:     Alt+F2, type 'r', Enter   (or: killall -3 gnome-shell)"
echo "    Wayland: log out and back in"
echo "Optional: install 'glow' to render summaries in the terminal."
