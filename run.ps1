# Copyright (C) 2026 Stamatis Valis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Generates isometric sprites: scans models/ and writes PNGs to output/.
# Usage: .\run.ps1 [extra generate.py args, e.g. --frames 6 --dirs E,N,SE]
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$blender = Get-ChildItem "C:\Program Files\Blender Foundation\*\blender.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]($_.Directory.Name -replace 'Blender ', '') } -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $blender) { Write-Error "Blender not found in C:\Program Files\Blender Foundation"; exit 1 }

& $blender --background --python (Join-Path $here 'generate.py') -- --models (Join-Path $here 'models') --output (Join-Path $here 'output') @args
