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
# Merges animation-only FBX/GLB files onto a character and exports one GLB
# with embedded clips (for packs like Kenney's where animations are separate).
# With -Clips fighter, generates the 7-clip bound-to-rot fighter set on the
# rig instead (no anims folder needed).
# Usage: .\merge.ps1 <character-file> <anims-folder> <out.glb>
#        .\merge.ps1 -Character CHAR.fbx -Clips fighter -Out out.glb
param(
    [Parameter(Mandatory = $true)][string]$Character,
    [string]$Anims = '',
    [Parameter(Mandatory = $true)][string]$Out,
    [ValidateSet('', 'fighter')][string]$Clips = '',
    [string]$BoneMap = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Anims -and -not $Clips) {
    Write-Error "Provide -Anims <folder> or -Clips fighter"
    exit 1
}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$blender = Get-ChildItem "C:\Program Files\Blender Foundation\*\blender.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]($_.Directory.Name -replace 'Blender ', '') } -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $blender) { Write-Error "Blender not found in C:\Program Files\Blender Foundation"; exit 1 }

$blendArgs = @('--character', $Character, '--out', $Out)
if ($Anims) { $blendArgs += @('--anims', $Anims) }
if ($Clips) { $blendArgs += @('--clips', $Clips) }
if ($BoneMap) { $blendArgs += @('--bone-map', $BoneMap) }

& $blender --background --python (Join-Path $here 'merge_anims.py') -- @blendArgs
