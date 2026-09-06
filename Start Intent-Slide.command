#!/bin/sh
set -eu
if [ -L "$0" ]; then printf '%s\n' 'Open the launcher in the real clone directory.' >&2; exit 2; fi
intent_root=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd -P)
exec "$intent_root/intent-slide" start --open "$@"
