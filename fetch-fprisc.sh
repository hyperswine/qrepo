#!/bin/sh
# Fetches FP-RISC into deps/fprisc (ignored by git) and builds its compiler,
# which build.sh then uses.  fprisc publishes no releases, so this is the
# newest main; FPRISC_REF picks another branch or tag, FPRISC_URL another
# repository.  Run it again to update.
set -eu
cd "$(dirname "$0")"
url=${FPRISC_URL:-git@github.com:hyperswine/fprisc.git}
ref=${FPRISC_REF:-main}
if [ -d deps/fprisc/.git ]; then
  git -C deps/fprisc fetch --depth 1 origin "$ref"
  git -C deps/fprisc checkout -q --detach FETCH_HEAD
else
  mkdir -p deps
  git clone --depth 1 --branch "$ref" "$url" deps/fprisc
fi
echo "fprisc at $(git -C deps/fprisc log -1 --format='%h %ad %s' --date=short)"
make -C deps/fprisc fpr
