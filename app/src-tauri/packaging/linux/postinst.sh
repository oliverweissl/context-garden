#!/bin/sh
# Install the AppArmor profile that lets the app's network sandbox create a user
# namespace (#81). Only on AppArmor 4 (Ubuntu >= 23.10), where the restriction
# exists and the profile syntax is understood; elsewhere it is not needed.
SRC=/usr/share/context-garden/apparmor/context-garden-app
DST=/etc/apparmor.d/context-garden-app
if [ -f "$SRC" ] && [ -e /etc/apparmor.d/abi/4.0 ]; then
  cp "$SRC" "$DST"
  if command -v apparmor_parser >/dev/null 2>&1 && [ -d /sys/kernel/security/apparmor ]; then
    apparmor_parser -r -W "$DST" || echo "ContextGarden: could not load $DST; the network sandbox falls back to Landlock" >&2
  fi
fi
exit 0
