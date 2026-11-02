#!/bin/sh
# Run as PUID:PGID (Unraid default 99:100 = nobody:users) and make sure /data is writable.
set -e

umask "${UMASK:-022}"
mkdir -p "$DATA_DIR"

if [ "$(id -u)" = "0" ]; then
    chown -R "${PUID}:${PGID}" "$DATA_DIR"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
