#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -z "${FPR:-}" ]; then
  if [ -x ../fprisc/fpr ]; then FPR=../fprisc/fpr; else FPR=fpr; fi
fi
crypto=
case "$(uname -s)" in Darwin) ;; *) crypto=-lcrypto ;; esac
if [ -n "$crypto" ]; then set -- --link "$crypto" "$@"; fi
exec "$FPR" build qr.fpr --system=posix --with posix.c --harts 2 -o qr "$@"
