#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Auto-Shredder Agent — One-Click Installer
# 
# Installs Python virtual environment dependencies, registers the systemd
# background user-space daemon, sets up the desktop launcher, and starts the system.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Design Colors
BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}🧹 Auto-Shredder Agent Installer${RESET}"
echo -e "Starting rootless local installation on Ubuntu...\n"

# 1. Environment Verification
PROJECT_DIR="/home/aegis-dev/dev-code/auto-shredder-agent"
VENV_DIR="$PROJECT_DIR/venv"

echo -e "${BOLD}[1/5] Checking system environment...${RESET}"
if ! command -v python3 &>/dev/null; then
    echo -e "❌ Python 3 is not installed. Please install it using: sudo apt install python3"
    exit 1
fi
echo -e "  ↳ Python 3 version: $(python3 --version)"
echo -e "  ↳ Project directory: $PROJECT_DIR"

# 2. Virtual Environment & Dependencies
echo -e "\n${BOLD}[2/5] Setting up Python virtual environment & dependencies...${RESET}"
mkdir -p "$PROJECT_DIR/data"

if [ ! -d "$VENV_DIR" ]; then
    echo -e "  ↳ Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

echo -e "  ↳ Upgrading pip..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

echo -e "  ↳ Installing requirements from requirements.txt..."
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" --quiet
else
    echo -e "  ↳ requirements.txt not found. Installing inline..."
    "$VENV_DIR/bin/pip" install fastapi==0.110.0 uvicorn==0.28.0 watchdog==4.0.0 send2trash==1.8.2 --quiet
fi
echo -e "  ↳ Dependencies installed successfully."

# 3. Create Systemd User Service
echo -e "\n${BOLD}[3/5] Registering background systemd user service...${RESET}"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

SERVICE_FILE="$SYSTEMD_USER_DIR/auto-shredder.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Auto-Shredder Agent Background Daemon & API Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python -m src.main
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

echo -e "  ↳ Reloading systemd user daemon..."
systemctl --user daemon-reload

echo -e "  ↳ Enabling and starting auto-shredder service..."
systemctl --user enable auto-shredder.service
systemctl --user restart auto-shredder.service

if systemctl --user is-active auto-shredder.service &>/dev/null; then
    echo -e "  ↳ Service status: ${GREEN}Active & Running${RESET}"
else
    echo -e "  ↳ ${YELLOW}Warning: service started but status is not active. Checking systemd journal...${RESET}"
    journalctl --user -n 20 -u auto-shredder.service
fi

# 4. Set Up Desktop Application Menu Launcher
echo -e "\n${BOLD}[4/5] Creating application menu shortcut...${RESET}"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"

cp "$PROJECT_DIR/auto-shredder.desktop" "$APPS_DIR/auto-shredder.desktop"
chmod +x "$APPS_DIR/auto-shredder.desktop"
echo -e "  ↳ Copied shortcut to: $APPS_DIR/auto-shredder.desktop"

# 5. Launch & WOW
echo -e "\n${BOLD}[5/5] Launching Auto-Shredder Dashboard...${RESET}"
echo -e "  ↳ Opening http://127.0.0.1:5050 in default web browser..."
sleep 1.5

if command -v xdg-open &>/dev/null; then
    xdg-open "http://127.0.0.1:5050" &>/dev/null || true
else
    echo -e "  ↳ xdg-open not found. Please navigate to http://127.0.0.1:5050 manually."
fi

echo -e "\n${BOLD}${GREEN}🎉 Installation Complete!${RESET}"
echo -e "================════════════════════════════════════════════"
echo -e "• Dashboard:     ${BOLD}http://127.0.0.1:5050${RESET}"
echo -e "• Background:    Managed rootless via ${BOLD}systemctl --user${RESET}"
echo -e "• Control:       ${BOLD}systemctl --user [start|stop|restart|status] auto-shredder${RESET}"
echo -e "• Logs:          ${BOLD}journalctl --user -u auto-shredder.service -f${RESET}"
echo -e "================════════════════════════════════════════════\n"
