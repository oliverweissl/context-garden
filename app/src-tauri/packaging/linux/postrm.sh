#!/bin/sh
# Remove the AppArmor profile on uninstall (deb: remove/purge; rpm: 0 = erase).
case "$1" in
  remove|purge|0)
    DST=/etc/apparmor.d/context-garden-app
    if [ -f "$DST" ]; then
      if command -v apparmor_parser >/dev/null 2>&1; then
        apparmor_parser -R "$DST" >/dev/null 2>&1 || true
      fi
      rm -f "$DST"
    fi
    ;;
esac
exit 0
