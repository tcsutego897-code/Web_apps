#!/usr/bin/env bash
set -eux

pwd
ls -la

if [ -f requirements.txt ]; then
  echo "requirements.txt found"
  cat requirements.txt
else
  echo "requirements.txt not found" >&2
  exit 1
fi

python -m pip install --upgrade pip setuptools wheel
pip install -r ./requirements.txt
