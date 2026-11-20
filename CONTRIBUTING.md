# Contributing

Python >= 3.11 (system python has no pytest):

```bash
pip install -e '.[dev]'
pytest
ruff check .
python scripts/validate-skills
bash skills/<name>/tests/smoke_test.sh   # offline; CI runs these too
```

## Before opening a PR

1. Open an issue first for substantial Skill work.
2. Keep infra changes and Skill changes in separate PRs.
3. Add/update tests for anything under `benchmarks/harness/`.
4. Don't add functionality a benchmark hasn't shown to help ([`docs/architecture.md`](docs/architecture.md#design-principles)).

## Signed macOS releases

`app-release.yml` signs and notarizes the macOS build when the repository has these
Actions secrets (Settings → Secrets and variables → Actions). Without
`APPLE_CERTIFICATE` the build stays unsigned (with a warning). A partial setup fails the
release job, because a signed but un-notarized app is still blocked by Gatekeeper.

| Secret | What |
|---|---|
| `APPLE_CERTIFICATE` | base64 of a `.p12` export of the **Developer ID Application** certificate and its private key (`base64 -i cert.p12 \| pbcopy`) |
| `APPLE_CERTIFICATE_PASSWORD` | the password chosen when exporting the `.p12` |
| `APPLE_SIGNING_IDENTITY` | the certificate's name, e.g. `Developer ID Application: Jane Doe (AB12CD34EF)` (`security find-identity -v -p codesigning`) |
| `APPLE_API_KEY` | key ID of an App Store Connect API key (Users and Access → Integrations → Team Keys, role Developer) |
| `APPLE_API_ISSUER` | that page's issuer ID |
| `APPLE_API_KEY_P8` | the contents of the downloaded `AuthKey_<key id>.p8` |

Instead of the three API key secrets, `APPLE_ID`, `APPLE_PASSWORD` (an app-specific
password from appleid.apple.com) and `APPLE_TEAM_ID` work too. All of this needs a paid
Apple Developer Program membership.

The job imports the certificate into a temporary keychain. Tauri signs the app with the
hardened runtime, notarizes it and staples the ticket.
[`scripts/macos-notarize-dmg.sh`](scripts/macos-notarize-dmg.sh) then signs, notarizes
and staples the `.dmg` too, checks both with `codesign`, `stapler` and `spctl`, and
replaces the `.dmg` in the draft release. To check a downloaded build on a Mac:

```bash
spctl --assess --type open --context context:primary-signature -v ContextGarden_*.dmg   # "accepted"
xcrun stapler validate ContextGarden_*.dmg
spctl --assess --type execute -v /Applications/ContextGarden.app   # "source=Notarized Developer ID"
```
