#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd /data/project/brackelbot/brackelbot
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python >=3.12 required"'
python3 -m venv ../venv
../venv/bin/python -m pip install --upgrade pip
../venv/bin/python -m pip install '.[server]'
../venv/bin/python -m wiki_stats.server_cli init-db
