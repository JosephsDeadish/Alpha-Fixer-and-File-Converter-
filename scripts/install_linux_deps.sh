#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# install_linux_deps.sh
#
# Installs the system-level libraries that PyQt6 requires on Linux.
# Run once before launching the application for the first time.
#
# Usage:
#   bash scripts/install_linux_deps.sh
# ---------------------------------------------------------------------------
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Colour

echo -e "${GREEN}🐼 Alpha Fixer & File Converter – Linux dependency installer${NC}"
echo ""

# ---------- Detect distribution ----------
if command -v apt-get &>/dev/null; then
    DISTRO="debian"
elif command -v dnf &>/dev/null; then
    DISTRO="fedora"
elif command -v pacman &>/dev/null; then
    DISTRO="arch"
elif command -v zypper &>/dev/null; then
    DISTRO="opensuse"
else
    echo -e "${RED}ERROR: Could not detect package manager (apt/dnf/pacman/zypper).${NC}"
    echo "Please install the following libraries manually:"
    echo "  libEGL.so.1  (Mesa EGL / libegl1)"
    echo "  libGL.so.1   (Mesa GL  / libgl1)"
    exit 1
fi

echo "Detected package manager: ${DISTRO}"
echo ""

# ---------- Install ----------
case "$DISTRO" in
  debian)
    PKGS=(libegl1 libgl1 libgles2 libpulse0 libxkbcommon0 libdbus-1-3)
    echo "Running: sudo apt-get install -y ${PKGS[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${PKGS[@]}"
    ;;
  fedora)
    PKGS=(mesa-libEGL mesa-libGL mesa-libGLES pulseaudio-libs libxkbcommon dbus-libs)
    echo "Running: sudo dnf install -y ${PKGS[*]}"
    sudo dnf install -y "${PKGS[@]}"
    ;;
  arch)
    PKGS=(mesa libpulse libxkbcommon dbus)
    echo "Running: sudo pacman -S --noconfirm ${PKGS[*]}"
    sudo pacman -S --noconfirm "${PKGS[@]}"
    ;;
  opensuse)
    PKGS=(libEGL1 libGL1 Mesa-libEGL1 libpulse0 libxkbcommon0 libdbus-1-3)
    echo "Running: sudo zypper install -y ${PKGS[*]}"
    sudo zypper install -y "${PKGS[@]}"
    ;;
esac

echo ""
echo -e "${GREEN}✔ System dependencies installed.${NC}"

# ---------- Python deps ----------
echo ""
echo "Installing Python dependencies..."
pip install --upgrade -r "$(dirname "$0")/../requirements.txt"
echo -e "${GREEN}✔ Python dependencies installed.${NC}"

echo ""
echo -e "${GREEN}🐼 All done! Run the application with:  python main.py${NC}"
