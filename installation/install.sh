#!/usr/bin/env bash
# Physiology Analysis Tools - first-time setup for macOS (and Linux).
#
# Run it from Terminal, inside the app folder:   bash installation/install.sh
# It checks for Python 3.12 and Node.js (and offers to install them with
# Homebrew), then installs everything the app needs into the app folder. Safe
# to run again: it only adds what is missing.

# This script lives in the installation folder; the app folder is one up.
cd "$(dirname "$0")/.." || exit 1

PY_VERSION="3.12"
MIN_NODE_MAJOR=22
MIN_NODE_MINOR=12
STEPS=5

# Where Homebrew and the python.org installer put things, in case this
# Terminal's PATH does not have them yet.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/$PY_VERSION/bin:$PATH"

step() { printf '\n\033[36m[%s/%s] %s\033[0m\n' "$1" "$STEPS" "$2"; }
ok() { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
info() { printf '       %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m    %s\n' "$1"; }
fail() {
  printf '\n  \033[31mX    %s\033[0m\n\n' "$1"
  printf '\033[31mSetup did not finish. Fix the problem above, then run "bash installation/install.sh" again.\033[0m\n'
  printf 'Everything that already worked is kept, so the next run is quicker.\n'
  exit 1
}
ask() {
  local answer
  read -r -p "       $1 [Y/n] " answer
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# Prints the command that runs Python 3.12, or nothing.
find_python() {
  local candidate version
  for candidate in "python$PY_VERSION" python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
    if [ "$version" = "$PY_VERSION" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# True when Node.js is installed and new enough.
node_ok() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || return 1
  local version major minor
  version=$(node -p 'process.versions.node' 2>/dev/null) || return 1
  major=${version%%.*}
  minor=${version#*.}
  minor=${minor%%.*}
  [ "$major" -gt "$MIN_NODE_MAJOR" ] || { [ "$major" -eq "$MIN_NODE_MAJOR" ] && [ "$minor" -ge "$MIN_NODE_MINOR" ]; }
}

# Installs a Homebrew package if Homebrew is there and the user agrees.
brew_install() {
  if ! command -v brew >/dev/null 2>&1; then
    warn 'It cannot be installed automatically (Homebrew is not installed).'
    return 1
  fi
  ask "Install $2 now with Homebrew?" || return 1
  info "Installing $2. This can take a few minutes..."
  brew install "$1"
}

printf '\n\033[36m==============================================\n'
printf '  Physiology Analysis Tools - first-time setup\n'
printf '==============================================\033[0m\n'
info "Folder: $(pwd)"

if [ ! -f package.json ] || [ ! -d backend ]; then
  fail 'This is not the Physiology-Analysis-Tools folder. Keep the installation folder inside the app folder.'
fi

# ---------------------------------------------------------------------------
step 1 "Checking for Python $PY_VERSION"
PYTHON=$(find_python)
if [ -z "$PYTHON" ]; then
  warn "Python $PY_VERSION was not found."
  brew_install "python@$PY_VERSION" "Python $PY_VERSION" && PYTHON=$(find_python)
fi
if [ -z "$PYTHON" ]; then
  echo
  info "Please install Python $PY_VERSION yourself:"
  info '  macOS: go to https://www.python.org/downloads/macos/ , download the latest'
  info '         "Python 3.12" macOS installer and run it.'
  info '  Linux: install python3.12 and python3.12-venv with your package manager.'
  info 'Then open a new Terminal window and run "bash installation/install.sh" again.'
  fail "Python $PY_VERSION is needed."
fi
ok "Python $PY_VERSION found ($PYTHON)"

# ---------------------------------------------------------------------------
step 2 'Checking for Node.js'
if ! node_ok; then
  if command -v node >/dev/null 2>&1; then
    warn "Node.js $(node --version) is too old ($MIN_NODE_MAJOR.$MIN_NODE_MINOR or newer is needed)."
  else
    warn 'Node.js was not found.'
  fi
  brew_install node 'Node.js'
fi
if ! node_ok; then
  echo
  info 'Please install Node.js yourself:'
  info '  Go to https://nodejs.org/ , download the "LTS" version and run the installer.'
  info 'Then open a new Terminal window and run "bash installation/install.sh" again.'
  fail "Node.js $MIN_NODE_MAJOR.$MIN_NODE_MINOR or newer is needed."
fi
ok "Node.js $(node --version) found"

# ---------------------------------------------------------------------------
step 3 'Installing the Python packages (analysis engine)'
VENV_PYTHON=".venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
  VENV_VERSION=$("$VENV_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
  if [ "$VENV_VERSION" != "$PY_VERSION" ]; then
    warn "The existing .venv folder uses a different Python version ($VENV_VERSION). Making a new one."
    rm -rf .venv
  fi
fi
if [ ! -x "$VENV_PYTHON" ]; then
  info 'Creating the private Python environment (.venv folder)...'
  "$PYTHON" -m venv .venv || fail 'Could not create the Python environment (.venv). On Linux, install python3.12-venv.'
fi
ok 'Python environment ready (.venv)'
info 'Downloading and installing packages. The first time this is several hundred MB'
info 'and can take 5-15 minutes. Lots of text scrolling past is normal.'
"$VENV_PYTHON" -m pip install --upgrade pip --disable-pip-version-check ||
  fail 'Could not update pip. Check your internet connection.'
"$VENV_PYTHON" -m pip install --disable-pip-version-check -e . -r backend/requirements.txt ||
  fail 'Installing the Python packages failed. Check your internet connection; the error is shown above.'
ok 'Python packages installed'

# ---------------------------------------------------------------------------
step 4 'Installing the app packages (window and screens)'
info 'Downloading and installing packages, including the app window (about 300 MB).'
info 'This can take a few minutes.'
npm install --no-fund --no-audit ||
  fail 'Installing the app packages failed. Check your internet connection; the error is shown above.'
chmod +x start-app.command 2>/dev/null
ok 'App packages installed'

# ---------------------------------------------------------------------------
step 5 'Done'
printf '\n  \033[32mSetup is complete.\033[0m\n\n'
info 'To start the app from now on: double-click start-app.command in this folder'
info '(or run "bash start-app.command" in Terminal). The very first start can take a minute or two.'
case "$(uname -s)" in
  Darwin) info 'Note: .adicht (LabChart) recordings can only be opened on Windows.' ;;
esac
echo
if ask 'Start the app now?'; then
  info 'Starting... keep this Terminal window open while you use the app.'
  npm run dev
fi
