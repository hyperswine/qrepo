#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -z "${FPR:-}" ]; then
  if [ -x ../fprisc/fpr ]; then FPR=../fprisc/fpr; else FPR=fpr; fi
fi
export FPR
./build.sh
mkdir -p build
./build.sh --cflag -DQREPO_TEST_FAULTS -o build/qr-fault
QR_FAULT="$PWD/build/qr-fault" python3 tests/check.py
"$FPR" build tests/merge.fpr --system=posix --harts 2 -o build/merge-test
build/merge-test
"$FPR" build tests/text.fpr --system=posix --harts 2 -o build/text-test
build/text-test

QR_FAULT="$PWD/build/qr-fault" python3 tests/remote.py
QR_FAULT="$PWD/build/qr-fault" python3 tests/fixes.py
QR_FAULT="$PWD/build/qr-fault" python3 tests/sync.py
QR_FAULT="$PWD/build/qr-fault" python3 tests/textsync.py
