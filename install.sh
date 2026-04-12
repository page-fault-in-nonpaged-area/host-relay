#!/usr/bin/env bash
# host-relay installer — one-line install for Linux and macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/page-fault-in-nonpaged-area/host-relay/main/install.sh | bash
set -euo pipefail

HR_DIR="$HOME/.host-relay"
MARKER="# host-relay"

# -------------------------------------------------------
# Helpers
# -------------------------------------------------------
info()  { printf '\033[1;34m[host-relay]\033[0m %s\n' "$1"; }
ok()    { printf '\033[1;32m[host-relay]\033[0m %s\n' "$1"; }
err()   { printf '\033[1;31m[host-relay]\033[0m %s\n' "$1" >&2; }

# -------------------------------------------------------
# Detect OS
# -------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *)
        err "Unsupported OS: $OS (only Linux and macOS are supported)"
        exit 1
        ;;
esac

# -------------------------------------------------------
# Install package
# -------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    info "Installing host-relay via uv..."
    uv tool install host-relay
elif command -v pip3 >/dev/null 2>&1; then
    info "Installing host-relay via pip3..."
    pip3 install --user host-relay
elif command -v pip >/dev/null 2>&1; then
    info "Installing host-relay via pip..."
    pip install --user host-relay
else
    err "Neither uv nor pip found. Please install one of:"
    err "  uv:  curl -LsSf https://astral.sh/uv/install.sh | sh"
    err "  pip: sudo apt install python3-pip  (or brew install python3)"
    exit 1
fi

# -------------------------------------------------------
# Create directories
# -------------------------------------------------------
info "Creating $HR_DIR..."
mkdir -p -m 700 "$HR_DIR/spool"
mkdir -p -m 700 "$HR_DIR/logs"

# -------------------------------------------------------
# Detect shell and RC file
# -------------------------------------------------------
USER_SHELL="$(basename "${SHELL:-/bin/bash}")"
RC_FILE=""

case "$USER_SHELL" in
    zsh)
        RC_FILE="$HOME/.zshrc"
        ;;
    fish)
        RC_FILE="$HOME/.config/fish/config.fish"
        ;;
    bash)
        if [ "$OS" = "Darwin" ]; then
            # macOS: prefer .bash_profile, fall back to .bashrc
            if [ -f "$HOME/.bash_profile" ]; then
                RC_FILE="$HOME/.bash_profile"
            else
                RC_FILE="$HOME/.bashrc"
            fi
        else
            RC_FILE="$HOME/.bashrc"
        fi
        ;;
    *)
        RC_FILE="$HOME/.bashrc"
        ;;
esac

# -------------------------------------------------------
# Append startup stanza (idempotent)
# -------------------------------------------------------
if [ -n "$RC_FILE" ]; then
    if [ -f "$RC_FILE" ] && grep -qF "$MARKER" "$RC_FILE"; then
        info "Startup stanza already present in $RC_FILE"
    else
        info "Adding startup stanza to $RC_FILE"

        if [ "$USER_SHELL" = "fish" ]; then
            mkdir -p "$(dirname "$RC_FILE")"
            cat >> "$RC_FILE" << 'FISH_EOF'

# host-relay
if not command hr status >/dev/null 2>&1
    command bash -c 'hr &' >/dev/null 2>&1
end
FISH_EOF
        else
            cat >> "$RC_FILE" << 'SH_EOF'

# host-relay
command -v hr >/dev/null 2>&1 && { hr status >/dev/null 2>&1 || hr & }
SH_EOF
        fi
    fi
fi

# -------------------------------------------------------
# Start hr now
# -------------------------------------------------------
if command -v hr >/dev/null 2>&1; then
    if hr status >/dev/null 2>&1; then
        info "hr is already running"
    else
        info "Starting hr in background..."
        hr &
        disown 2>/dev/null || true
    fi
else
    err "hr command not found in PATH after install."
    err "You may need to restart your shell or add ~/.local/bin to PATH."
fi

# -------------------------------------------------------
# Print MCP config snippet
# -------------------------------------------------------
ok "Installation complete!"
echo ""
echo "Add this to your MCP config (e.g. claude_desktop_config.json):"
echo ""
echo '  {'
echo '    "mcpServers": {'
echo '      "host-relay": {'
echo '        "command": "hr",'
echo '        "args": ["mcp"]'
echo '      }'
echo '    }'
echo '  }'
echo ""
ok "Run 'hr status' to verify the listener is running."
