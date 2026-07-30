# SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
# SPDX-License-Identifier: Apache-2.0
#
# Build PipeHeatLoad.fmu (FMI 2.0 Co-Simulation, win64) with MSVC.
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
#
# Produces ..\..\example\PipeHeatLoad.fmu

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = "PipeHeatLoad"

# --- locate MSVC -------------------------------------------------------------
$vcvars = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $root = & $vswhere -latest -products * -property installationPath
        if ($root) { $vcvars = Join-Path $root "VC\Auxiliary\Build\vcvars64.bat" }
    }
}
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found - install Visual Studio C++ build tools." }

# --- compile the shared library ---------------------------------------------
$build = Join-Path $here "build"
New-Item -ItemType Directory -Force -Path $build | Out-Null
$src = Join-Path $here "$name.c"
$dll = Join-Path $build "$name.dll"

# Compile from inside the build dir so object/output paths stay relative
# (a trailing backslash before a closing quote would escape it under cmd).
cmd /c "`"$vcvars`" >nul && cd /d `"$build`" && cl /nologo /LD /O2 /W3 `"$src`" /Fe:$name.dll"
if (-not (Test-Path $dll)) { throw "Compilation failed - $dll was not produced." }
Write-Host "compiled: $dll"

# --- stage the FMU layout ----------------------------------------------------
$stage = Join-Path $build "stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "binaries\win64") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "sources") | Out-Null
Copy-Item (Join-Path $here "modelDescription.xml") $stage
Copy-Item $dll (Join-Path $stage "binaries\win64\")
Copy-Item $src (Join-Path $stage "sources\")

# --- zip it into a .fmu ------------------------------------------------------
$outDir = Join-Path $here "..\..\example"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$fmu = Join-Path (Resolve-Path $outDir) "$name.fmu"
if (Test-Path $fmu) { Remove-Item -Force $fmu }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $fmu)

Write-Host "built: $fmu"
