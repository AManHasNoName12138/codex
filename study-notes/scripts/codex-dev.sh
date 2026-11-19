#!/usr/bin/env bash
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf '%s\n' 'Execute this launcher; do not source it.' >&2
  return 2
fi
set -euo pipefail
codex_dev_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 -I "$codex_dev_script_dir/codex-dev.py" "$@"
