#!/usr/bin/env bash
# Sign, notarize and staple the macOS .dmg that `tauri build` made, then check
# that Gatekeeper accepts both the app and the dmg. Tauri already signs,
# notarizes and staples ContextGarden.app; this covers the disk image around it,
# so a downloaded .dmg opens without "Apple could not verify ..." (#52).
#
# Usage: scripts/macos-notarize-dmg.sh <bundle dir>   (…/release/bundle)
# Needs, in the environment:
#   APPLE_SIGNING_IDENTITY  "Developer ID Application: <name> (<team id>)", in a keychain
#   and either an App Store Connect API key
#     APPLE_API_KEY (key id), APPLE_API_ISSUER (issuer id), APPLE_API_KEY_PATH (.p8 file)
#   or an Apple ID
#     APPLE_ID, APPLE_PASSWORD (app-specific password), APPLE_TEAM_ID
set -euo pipefail

bundle="${1:?usage: $0 <bundle dir>}"
: "${APPLE_SIGNING_IDENTITY:?APPLE_SIGNING_IDENTITY is not set}"

shopt -s nullglob
dmgs=("$bundle"/dmg/*.dmg)
apps=("$bundle"/macos/*.app)
if [ "${#dmgs[@]}" -ne 1 ] || [ "${#apps[@]}" -ne 1 ]; then
  echo "expected one .dmg and one .app under $bundle, found ${#dmgs[@]} and ${#apps[@]}" >&2
  exit 1
fi
dmg="${dmgs[0]}"
app="${apps[0]}"

if [ -n "${APPLE_API_KEY:-}" ]; then
  : "${APPLE_API_ISSUER:?APPLE_API_ISSUER is not set}" "${APPLE_API_KEY_PATH:?APPLE_API_KEY_PATH is not set}"
  auth=(--key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER")
elif [ -n "${APPLE_ID:-}" ]; then
  : "${APPLE_PASSWORD:?APPLE_PASSWORD is not set}" "${APPLE_TEAM_ID:?APPLE_TEAM_ID is not set}"
  auth=(--apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID")
else
  echo "no notarization credentials: set APPLE_API_KEY/APPLE_API_ISSUER/APPLE_API_KEY_PATH or APPLE_ID/APPLE_PASSWORD/APPLE_TEAM_ID" >&2
  exit 1
fi

echo "== app: $app"
codesign --verify --deep --strict --verbose=2 "$app"
xcrun stapler validate "$app"
spctl --assess --type execute --verbose=4 "$app"

echo "== dmg: $dmg"
codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$dmg"
xcrun notarytool submit "$dmg" "${auth[@]}" --wait --timeout 30m
xcrun stapler staple "$dmg"
xcrun stapler validate "$dmg"
codesign --verify --strict --verbose=2 "$dmg"
spctl --assess --type open --context context:primary-signature --verbose=4 "$dmg"
echo "$dmg"
