#!/usr/bin/env bash
# Print the Homebrew cask for a release: scripts/homebrew-cask.sh 0.7.0 > Casks/contextgarden.rb
set -euo pipefail
version="${1:?usage: homebrew-cask.sh <version, no leading v>}"
here="$(cd "$(dirname "$0")/.." && pwd)"
url="https://github.com/oliverweissl/context-garden/releases/download/v${version}/ContextGarden_${version}_universal.dmg"
sha="$(curl -fsSL "$url" | shasum -a 256 | cut -d' ' -f1)"
sed -e "s/@VERSION@/${version}/" -e "s/@SHA256@/${sha}/" "$here/packaging/homebrew/contextgarden.rb"
