#!/usr/bin/env bash
# Install a desktop launcher for the pet (and optionally autostart it).
#
#   tools/install_desktop_entry.sh              -> launcher in the app menu
#   tools/install_desktop_entry.sh --autostart  -> also start her at login
#
# Nothing here needs root; both files live in your own home directory and can be
# removed again with --uninstall.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
ENTRY="$APPS/tsunade-pet.desktop"
AUTOSTART_ENTRY="$AUTOSTART/tsunade-pet.desktop"

# % is the field-code escape character in .desktop files, so it must be doubled.
escape() { printf '%s' "${1//%/%%}"; }
APP_PATH="$(escape "$HERE")"

uninstall() {
    rm -f "$ENTRY" "$AUTOSTART_ENTRY"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
    echo "Removed the Tsunade Pet launcher and autostart entry."
}

write_entry() {
    local target="$1" extra="$2"
    mkdir -p "$(dirname "$target")"
    cat > "$target" <<EOF
[Desktop Entry]
Type=Application
Name=Tsunade Pet
Comment=A desktop companion with a to-do list
Exec="$APP_PATH/run.sh"
Path=$APP_PATH
Icon=$APP_PATH/assets/tsunade_icon.png
Terminal=false
Categories=Utility;
$extra
EOF
}

case "${1:-}" in
    --uninstall)
        uninstall
        exit 0
        ;;
    --help|-h)
        sed -n '2,9p' "$0"
        exit 0
        ;;
    --autostart)
        write_entry "$ENTRY" ""
        write_entry "$AUTOSTART_ENTRY" "X-GNOME-Autostart-enabled=true"
        echo "Installed: $ENTRY"
        echo "Installed: $AUTOSTART_ENTRY (she will start with your session)"
        ;;
    "")
        write_entry "$ENTRY" ""
        echo "Installed: $ENTRY"
        echo "Tsunade Pet now appears in your application menu."
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Use --autostart, --uninstall or --help." >&2
        exit 2
        ;;
esac

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
