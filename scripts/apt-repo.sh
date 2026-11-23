#!/usr/bin/env bash
# Add a .deb to a signed apt repository directory (served from GitHub Pages).
#   scripts/apt-repo.sh <repo-dir> <file.deb>
# Needs apt-ftparchive (apt-utils) and gpg. Signing key: $APT_GPG_KEY_ID (must
# already be in the gpg keyring). Keeps every deb already in <repo-dir>/pool.
set -euo pipefail
repo="${1:?usage: apt-repo.sh <repo-dir> <file.deb>}"
deb="${2:?usage: apt-repo.sh <repo-dir> <file.deb>}"
: "${APT_GPG_KEY_ID:?set APT_GPG_KEY_ID}"

mkdir -p "$repo/pool/main" "$repo/dists/stable/main/binary-amd64"
cp "$deb" "$repo/pool/main/"
cd "$repo"
apt-ftparchive packages pool > dists/stable/main/binary-amd64/Packages
gzip -9kf dists/stable/main/binary-amd64/Packages
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=ContextGarden \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures=amd64 \
  -o APT::FTPArchive::Release::Components=main \
  release dists/stable > dists/stable/Release
gpg --batch --yes --default-key "$APT_GPG_KEY_ID" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg --batch --yes --default-key "$APT_GPG_KEY_ID" -abs -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --yes --export "$APT_GPG_KEY_ID" > contextgarden.gpg
