<#
.SYNOPSIS
  Build the Windows packages: a portable zip and, when Inno Setup is installed, the installer.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Python "py -3.12"
.NOTES
  Output goes to dist\. Uses an isolated .venv-build so the build only contains the runtime dependencies.
  The installer needs Inno Setup 6 (https://jrsoftware.org/isinfo.php); without it only the zip is made.
#>
param(
    [string]$Python = "python",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

# 1. isolated build environment
$venv = Join-Path $root ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating build environment in $venv ..."
    Invoke-Checked "creating the virtual environment" { & $Python -m venv $venv }
}
Invoke-Checked "installing build requirements" { & $py -m pip install --disable-pip-version-check -q -r requirements-build.txt }

$env:PYTHONPATH = Join-Path $root "src"
# The version is the one written in src/klotse/_version.py (changed only by a release PR); the app reports it with +g<commit>
# added when this commit is not exactly the tagged release (written to the git-ignored src/klotse/_build_info.py)
Invoke-Checked "writing the build info" { & $py (Join-Path $root "scripts/release.py") write-build-info }
$version = (& $py -c "import klotse; print(klotse.__version__)").Trim()
$numeric = [regex]::Match($version, '^\d+(\.\d+){0,2}').Value
Write-Host "Building Klotse $version"

# 2. freeze
Remove-Item -Recurse -Force (Join-Path $root "dist\Klotse") -ErrorAction SilentlyContinue
Invoke-Checked "PyInstaller" { & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\klotse.spec }
$exe = Join-Path $root "dist\Klotse\Klotse.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }

# 3. check the frozen app itself: Qt plugins, assets and imports must all be inside the bundle
$report = Join-Path $root "build\self-test.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$run = Start-Process -FilePath $exe -ArgumentList @("--self-test", "`"$report`"") -Wait -PassThru
if (Test-Path $report) { Get-Content $report }
if ($run.ExitCode -ne 0) { throw "The packaged app failed its self-test (exit code $($run.ExitCode))." }

# 4. portable zip
$zip = Join-Path $root "dist\Klotse-$version-windows-x64-portable.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $root "dist\Klotse") -DestinationPath $zip
Write-Host "Created $zip"

# 5. installer
if ($SkipInstaller) { return }
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found, so no installer was built (the zip above is complete). Install it with: winget install JRSoftware.InnoSetup"
    return
}
Invoke-Checked "Inno Setup" { & $iscc "/DAppVersion=$version" "/DNumericVersion=$numeric" "/DIconFile=$root\build\icons\klotse.ico" "packaging\windows\klotse.iss" }
Write-Host "Created $(Join-Path $root "dist\Klotse-$version-windows-x64-setup.exe")"
