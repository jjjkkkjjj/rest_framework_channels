#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# NOTE: このMacに `python` は無く `python3` のみ。
# `setup.py sdist bdist_wheel` はsetuptoolsが非推奨にした呼び出しだが、
# `python3 -m build` を使うには別途 `pip install build` が必要なため現状はこちら。
rm -rf dist build ./*.egg-info
python3 setup.py sdist bdist_wheel

twine check dist/*
twine upload --repository pypi dist/*
