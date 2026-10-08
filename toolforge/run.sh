#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd /data/project/brackelbot/brackelbot
if [[ "${BRACKELBOT_STORAGE:-}" != "toolsdb" ]]; then
  echo 'Set BRACKELBOT_STORAGE=toolsdb. Ephemeral container state is not allowed.' >&2
  exit 2
fi
exec ../venv/bin/python -m wiki_stats.server_cli run --season 2026 --report-dir ../reports "$@"
