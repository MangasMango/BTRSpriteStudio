# Copyright (C) 2026 MangasMango
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
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
