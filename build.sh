#!/bin/sh
set -eu
cd "$(dirname "$0")"
# The compiler: $FPR, else the one fetch-fprisc.sh put in deps/ (not in
# version control), else a sibling checkout, else fpr on the PATH
if [ -z "${FPR:-}" ]; then
  if [ -x deps/fprisc/fpr ]; then FPR=deps/fprisc/fpr
  elif [ -x ../fprisc/fpr ]; then FPR=../fprisc/fpr
  else FPR=fpr; fi
fi
crypto=
case "$(uname -s)" in Darwin) ;; *) crypto=-lcrypto ;; esac
if [ -n "$crypto" ]; then set -- --link "$crypto" "$@"; fi
exec "$FPR" build qr.fpr --system=posix --with posix.c --harts 2 -o qr "$@"
