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
# Converts a rigged model with animations into 16-direction sprite clips.
# Usage: .\run.ps1 --input "<model file or folder>" [extra convert.py args,
#        e.g. --category enemy --name skeleton --frames 8 --char-px 50]
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$blender = Get-ChildItem "C:\Program Files\Blender Foundation\*\blender.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]($_.Directory.Name -replace 'Blender ', '') } -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $blender) { Write-Error "Blender not found in C:\Program Files\Blender Foundation"; exit 1 }

& $blender --background --python (Join-Path $here 'convert.py') -- @args
