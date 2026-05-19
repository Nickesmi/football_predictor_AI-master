#!/bin/bash
# ============================================================
# build_desktop.sh — Football Predictor AI local macOS build
# ============================================================
# Builds a standalone macOS .app that any Mac user can run
# without Python, Node.js, or any other dependency.
# 
# Usage:
#   chmod +x build_desktop.sh
#   ./build_desktop.sh
# ============================================================

set -e  # Exit immediately on any error

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}=> $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $1${NC}"; }
error()   { echo -e "${RED}❌ $1${NC}"; exit 1; }

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Football Predictor AI — Desktop Builder  ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

# ── Step 1: Check tools ────────────────────────────────────
info "Checking prerequisites..."
command -v python3 >/dev/null 2>&1 || error "Python 3 not found. Install from https://python.org"
command -v npm     >/dev/null 2>&1 || error "npm not found. Install Node.js from https://nodejs.org"
success "Python $(python3 --version) ✓"
success "npm $(npm --version) ✓"

# ── Step 2: Install Python packaging tools ────────────────
info "Installing PyInstaller and PyWebView..."
pip3 install pywebview pyinstaller --quiet
success "PyInstaller and PyWebView installed."

# ── Step 3: Build React frontend ──────────────────────────
info "Building React frontend..."
cd frontend
npm install --silent
npm run build
cd ..
success "Frontend built to frontend/dist/"

# ── Step 4: Clean old builds ──────────────────────────────
info "Cleaning old build artifacts..."
rm -rf build dist
success "Clean."

# ── Step 5: Ensure models directory exists ───────────────
mkdir -p models

# ── Step 6: Run PyInstaller ───────────────────────────────
info "Bundling app with PyInstaller (this takes 2–5 minutes)..."
python3 -m PyInstaller football_predictor.spec --clean --noconfirm

# ── Step 7: Verify output ────────────────────────────────
APP_PATH="dist/Football Predictor AI.app"
if [ ! -d "$APP_PATH" ]; then
    error "Build failed — .app not found at: $APP_PATH"
fi
success "App bundle created: $APP_PATH"

# ── Step 8: Create DMG (optional, requires create-dmg) ───
if command -v create-dmg >/dev/null 2>&1; then
    info "Creating .dmg installer..."
    rm -f "Football-Predictor-AI.dmg"
    create-dmg \
        --volname "Football Predictor AI" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --app-drop-link 450 185 \
        "Football-Predictor-AI.dmg" \
        "$APP_PATH" \
    && success "DMG created: Football-Predictor-AI.dmg" \
    || warn "DMG creation had an issue (the .app still works fine)."
else
    warn "create-dmg not installed. Skipping DMG. Install with: brew install create-dmg"
    info "Your app is ready at: $APP_PATH"
fi

# ── Step 9: Copy to Desktop ──────────────────────────────
DESKTOP="$HOME/Desktop"
info "Copying app to Desktop..."
cp -R "$APP_PATH" "$DESKTOP/" && success "Copied to: $DESKTOP/Football Predictor AI.app"

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  🚀 BUILD COMPLETE!                       ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  📁 App bundle: dist/Football Predictor AI.app"
echo "  🖥️  Desktop:   ~/Desktop/Football Predictor AI.app"
if [ -f "Football-Predictor-AI.dmg" ]; then
    SIZE=$(du -sh "Football-Predictor-AI.dmg" | cut -f1)
    echo "  💿 DMG file:   Football-Predictor-AI.dmg ($SIZE)"
fi
echo ""
echo "  ℹ️  To distribute: send 'Football-Predictor-AI.dmg' to any Mac user."
echo "  ℹ️  For Windows:   push a git tag (e.g. git tag v1.0.0 && git push --tags)"
echo "                    GitHub Actions will auto-build the Windows .exe."
echo ""
