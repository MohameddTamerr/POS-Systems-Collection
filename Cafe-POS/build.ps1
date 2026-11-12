param(
    [switch]$SkipTests,
    [switch]$CleanVenv
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "=== Building CafePOS Windows Desktop Executable ===" -ForegroundColor Cyan

$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if ($CleanVenv -and (Test-Path $VenvDir)) {
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv $VenvDir
}

& $VenvPython -c "import PySide6.QtWidgets, PySide6.QtPrintSupport, PySide6.QtSvg, PIL.Image, argon2, PyInstaller, pytest" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Yellow
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
}

if (-not $SkipTests) {
    Write-Host "Running automated test suite..." -ForegroundColor Yellow
    $env:PYTHONPATH = Join-Path $ProjectRoot "src"
    & $VenvPython -m pytest tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed. Aborting Windows EXE build."
    }
}

Write-Host "Cleaning previous build/dist artifacts..." -ForegroundColor Yellow
foreach ($dir in @("build", "dist")) {
    $target = Join-Path $ProjectRoot $dir
    if (Test-Path $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

Write-Host "Running PyInstaller..." -ForegroundColor Yellow
& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "CafePOS.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$ExePath = Join-Path $ProjectRoot "dist\CafePOS.exe"
if (-not (Test-Path $ExePath)) {
    throw "Expected output executable not found at $ExePath"
}

$SizeMB = [Math]::Round(((Get-Item $ExePath).Length / 1MB), 2)
Write-Host "Build succeeded: $ExePath ($SizeMB MB)" -ForegroundColor Green
