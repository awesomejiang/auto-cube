#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [int]$MaxExitCode = 0
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -lt 0 -or $LASTEXITCODE -gt $MaxExitCode) {
        throw "$Executable exited with code $LASTEXITCODE."
    }
}

$go = (Get-Command go.exe -CommandType Application).Source
$npm = (Get-Command npm.cmd -CommandType Application).Source
$cargo = (Get-Command cargo.exe -CommandType Application).Source
$python = (Get-Command py.exe -CommandType Application).Source
$sevenZip = Get-Command 7z.exe, 7zz.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty Source
if (-not $sevenZip) {
    $sevenZip = Join-Path $env:ProgramFiles '7-Zip\7z.exe'
    if (-not (Test-Path -LiteralPath $sevenZip -PathType Leaf)) {
        throw 'Install 7-Zip or add 7z.exe or 7zz.exe to PATH.'
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$build = Join-Path ([System.IO.Path]::GetTempPath()) ('quanah-build-' + [guid]::NewGuid())
$package = Join-Path $build 'Quanah-Cube'
$runtime = Join-Path $package 'runtime'
$ocr = Join-Path $package 'ocr'
$pythonZip = Join-Path $build 'python.zip'
$tesseract = Join-Path $build 'tesseract.exe'
$zip = Join-Path $build 'Quanah-Cube-Windows-x64.zip'
$dist = Join-Path $PSScriptRoot 'dist'

try {
    New-Item -ItemType Directory -Path $runtime, $ocr | Out-Null
    Invoke-Checked 'curl.exe' @('-fsSL', 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip', '-o', $pythonZip)
    Invoke-Checked 'curl.exe' @('-fsSL', 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe', '-o', $tesseract)
    if ((Get-FileHash -LiteralPath $tesseract -Algorithm SHA256).Hash -ne
        'c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9') {
        throw 'Tesseract installer SHA256 does not match.'
    }

    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    Invoke-Checked $python @('-3', '-m', 'pip', 'download', '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '312', '--implementation', 'cp', '--abi', 'cp312', '-r', $requirements, '-d', $build)
    [System.IO.Compression.ZipFile]::ExtractToDirectory($pythonZip, $runtime)
    foreach ($wheel in Get-ChildItem -LiteralPath $build -Filter '*.whl' -File) {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $runtime)
    }
    Set-Content -LiteralPath (Join-Path $runtime 'python312._pth') -Encoding ASCII -Value '.', 'python312.zip'

    # 7-Zip may report an NSIS warning. Check the required files before continuing.
    Invoke-Checked $sevenZip @('x', '-y', "-o$ocr", $tesseract, 'tesseract.exe', '*.dll', 'tessdata/eng.traineddata', 'doc/LICENSE') -MaxExitCode 1
    foreach ($file in 'tesseract.exe', 'libtesseract-5.dll', 'tessdata/eng.traineddata', 'doc/LICENSE') {
        $extracted = Get-Item -LiteralPath (Join-Path $ocr $file)
        if ($extracted.PSIsContainer -or $extracted.Length -eq 0) {
            throw "Invalid Tesseract file: $file"
        }
    }

    $launcher = Join-Path $PSScriptRoot 'launcher.go'
    $goEnvironment = @{ GOOS = $env:GOOS; GOARCH = $env:GOARCH; CGO_ENABLED = $env:CGO_ENABLED }
    try {
        $env:GOOS = 'windows'
        $env:GOARCH = 'amd64'
        $env:CGO_ENABLED = '0'
        Invoke-Checked $go @('build', '-trimpath', '-ldflags=-s -w', '-o', (Join-Path $package 'Quanah-Cube-CLI.exe'), $launcher)
    } finally {
        foreach ($name in $goEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $goEnvironment[$name], 'Process')
        }
    }
    $goRoot = Invoke-Checked $go @('env', 'GOROOT')
    Copy-Item -LiteralPath (Join-Path $goRoot 'LICENSE') -Destination (Join-Path $package 'Go-LICENSE.txt')
    Copy-Item -LiteralPath $launcher, (Join-Path $PSScriptRoot 'quanah_cube.py') -Destination $package
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'confirmation-templates') -Destination $package -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'potential-templates') -Destination $package -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $package 'README.txt')

    $desktop = Join-Path $PSScriptRoot 'desktop'
    $manifest = Join-Path $desktop 'src-tauri/Cargo.toml'
    $cargoTarget = Join-Path $desktop 'src-tauri/target'
    $windowsTarget = 'x86_64-pc-windows-msvc'
    Invoke-Checked $npm @('--prefix', $desktop, 'ci', '--no-audit', '--no-fund')
    Invoke-Checked $npm @('--prefix', $desktop, 'run', 'build')
    Invoke-Checked $cargo @('build', '--locked', '--release', '--manifest-path', $manifest,
        '--features', 'tauri/custom-protocol', '--target', $windowsTarget, '--target-dir', $cargoTarget)
    $desktopExe = Join-Path $cargoTarget "$windowsTarget/release/quanah-cube.exe"
    Copy-Item -LiteralPath $desktopExe -Destination (Join-Path $package 'Quanah-Cube.exe')

    [System.IO.Compression.ZipFile]::CreateFromDirectory($package, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    Invoke-Checked $sevenZip @('t', $zip)
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $output = Join-Path $dist (Split-Path -Leaf $zip)
    Move-Item -LiteralPath $zip -Destination $output -Force
    Write-Output "Created $output"
} finally {
    if (Test-Path -LiteralPath $build) {
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
        $resolvedBuild = (Resolve-Path -LiteralPath $build).ProviderPath
        if ((Split-Path -Parent $resolvedBuild) -eq $tempRoot) {
            Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
        }
    }
}
