#!/usr/bin/env bash
# The Node/Python launcher is shared by the POSIX platforms.
exec bash "$(dirname "$0")/start-macos.command" "$@"
