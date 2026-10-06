#!/bin/sh
set -eu
. /opt/specguard-lean/runtime-env.sh
export PATH="$LEAN_SYSROOT/bin:/usr/bin:/bin"
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec /opt/specguard-lean/repl/.lake/build/bin/repl
