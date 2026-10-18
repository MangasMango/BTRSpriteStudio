# Copyright (C) 2026 Stamatis Valis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Launches Sprite Studio (raylib GUI for the sprite pipeline).
# See .ai/studio-spec.md. Usage: .\studio.ps1 [--selftest]
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
uv run --with 'raylib>=5.5.0' --with pillow python (Join-Path $here 'studio.py') @args
