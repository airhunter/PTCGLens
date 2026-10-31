#!/bin/sh
PTCGLENS_PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if [ ! -x "$PTCGLENS_PROJECT/.venv/bin/python" ]; then
    echo "Python environment missing. Run uv sync in this project first."
    exit 1
fi
exec "$PTCGLENS_PROJECT/.venv/bin/python" "$PTCGLENS_PROJECT/ptcglens.py"
