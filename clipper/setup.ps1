# ZonClip setup - run once on the machine you'll clip on (an NVIDIA GPU is fastest).
# Creates a LOCAL venv + work dirs, installs core deps, copies Poppins fonts for libass,
# and pre-generates the watermark. AI/API extras remain opt-in and are never required.
# Local home defaults to %USERPROFILE%\clipforge; override with CLIPFORGE_HOME.
$ErrorActionPreference = "Stop"
$root = if ($env:CLIPFORGE_HOME) { $env:CLIPFORGE_HOME } else { Join-Path $env:USERPROFILE "clipforge" }
$code = $PSScriptRoot

Write-Host "ZonClip setup -> $root"
New-Item -ItemType Directory -Force -Path $root, "$root\work", "$root\fonts", "$root\models" | Out-Null

# 1) Create the venv. Prefer the documented, broadly supported Python 3.11,
# then newer runtimes. Do not silently accept an old system Python.
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @("3.11", "3.12", "3.13", "3.14")) {
            & py "-$version" -m venv $venv
            if ($LASTEXITCODE -eq 0 -and (Test-Path $py)) {
                Write-Host "Created environment with Python $version."
                $created = $true
                break
            }
        }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -m venv $venv
        if ($LASTEXITCODE -eq 0 -and (Test-Path $py)) { $created = $true }
    }
    if (-not $created) {
        throw "Could not create the virtual environment. Install Python 3.11 or newer, then rerun setup."
    }
}

$pythonVersion = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the virtual environment's Python version." }
$versionParts = $pythonVersion.Trim().Split('.')
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 11)) {
    throw "ZonClip requires Python 3.11 or newer. The existing environment uses Python $pythonVersion. Remove $venv and rerun setup after installing Python 3.11+."
}
Write-Host "Using Python $pythonVersion."

& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }
& $py -m pip install -r (Join-Path $code "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Core dependency installation failed. Check the error above and rerun setup." }

# 2) GPU wheels only when an NVIDIA GPU is present.
$nv = $false
try { nvidia-smi *> $null; if ($LASTEXITCODE -eq 0) { $nv = $true } } catch {}
if ($nv) {
    Write-Host "NVIDIA GPU detected: installing CUDA wheels (cuBLAS + cuDNN)."
    & $py -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    if ($LASTEXITCODE -ne 0) { throw "CUDA dependency installation failed." }
} else {
    Write-Host "No NVIDIA GPU detected: transcription will use an available Vulkan backend or CPU."
}

# 3) Poppins fonts for libass (captions) - copy from the per-user font store.
$fontSrc = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
Get-ChildItem -Path $fontSrc, "C:\Windows\Fonts" -Filter "Poppins*.ttf" -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName (Join-Path "$root\fonts" $_.Name) -Force -ErrorAction SilentlyContinue }
$nfonts = (Get-ChildItem "$root\fonts\Poppins*.ttf" -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Poppins fonts available for libass: $nfonts"
if ($nfonts -eq 0) { Write-Host "WARNING: no Poppins fonts found; captions will use a fallback font." }

# 4) Pre-generate the watermark.
$env:PYTHONPATH = $code
& $py -c "from pipeline import config, branding; print('watermark:', branding.render_watermark(config.load_config(), force=True))"
if ($LASTEXITCODE -ne 0) { throw "Watermark generation failed." }

# 5) Download the Twemoji emoji set (openly licensed) used by the editor + renderer.
& $py (Join-Path $code "tools\fetch_emoji.py")
if ($LASTEXITCODE -ne 0) { throw "Emoji asset setup failed. Check network access and rerun setup." }

Write-Host "`nCore setup complete."
Write-Host "Optional AI features (requires an external API key and may incur usage charges):"
Write-Host "  & '$py' -m pip install -r '$(Join-Path $code "requirements-ai.txt")'"
Write-Host "Launch the Studio with:"
Write-Host "  `$env:PYTHONPATH='$code'; & '$py' '$(Join-Path $code "dashboard.py")'"
Write-Host "Or run a one-off clip job from the CLI:"
Write-Host "  `$env:PYTHONPATH='$code'; & '$py' '$(Join-Path $code "clip.py")' '<path\to\your-vod.mp4>'"
