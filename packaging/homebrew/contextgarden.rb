# Homebrew cask template. Copy to Casks/contextgarden.rb in the tap repo
# (oliverweissl/homebrew-tap) and run scripts/homebrew-cask.sh to fill
# version and sha256 for a release.
cask "contextgarden" do
  version "@VERSION@"
  sha256 "@SHA256@"

  url "https://github.com/oliverweissl/context-garden/releases/download/v#{version}/ContextGarden_#{version}_universal.dmg"
  name "ContextGarden"
  desc "Gardens of repos, context skills and agent terminals"
  homepage "https://github.com/oliverweissl/context-garden"

  app "ContextGarden.app"

  # Ad-hoc signed, not notarized: drop the quarantine flag so Gatekeeper
  # does not block the first start.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/ContextGarden.app"]
  end

  zap trash: [
    "~/Library/Application Support/dev.contextgarden.app",
    "~/Library/Caches/dev.contextgarden.app",
  ]
end
