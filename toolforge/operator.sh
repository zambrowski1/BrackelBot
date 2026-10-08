#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd /data/project/brackelbot/brackelbot
exec ../venv/bin/python -m wiki_stats.server_cli "$@"
