# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Python = "python",
    [string]$SignedFirefoxXpi = "",
    [string]$OutputDirectory = "",
    [switch]$SkipDependencies,
    [switch]$SkipFrontend,
    [switch]$SkipMsi
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = [IO.Path]::GetFullPath((Join-Path $RepositoryRoot "build\windows"))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $RepositoryRoot "dist\windows"
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

function Reset-WorkspaceDirectory([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    $AllowedPrefix = $RepositoryRoot.TrimEnd('\') + '\'
    if (-not $FullPath.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset a directory outside the repository: $FullPath"
    }
    if ($FullPath -eq $RepositoryRoot) {
        throw "Refusing to reset the repository root."
    }
    if (Test-Path -LiteralPath $FullPath) {
        Remove-Item -LiteralPath $FullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $FullPath -Force | Out-Null
}

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Compress-ArchiveWithRetry(
    [string]$SourcePath,
    [string]$DestinationPath,
    [int]$MaximumAttempts = 5
) {
    for ($Attempt = 1; $Attempt -le $MaximumAttempts; $Attempt++) {
        if (Test-Path -LiteralPath $DestinationPath) {
            Remove-Item -LiteralPath $DestinationPath -Force
        }
        try {
            Compress-Archive `
                -Path $SourcePath `
                -DestinationPath $DestinationPath `
                -CompressionLevel Optimal `
                -ErrorAction Stop
            return
        } catch {
            if ($Attempt -eq $MaximumAttempts) {
                throw
            }
            Write-Warning "Portable ZIP attempt $Attempt failed; retrying after a transient file lock."
            Start-Sleep -Milliseconds 1000
        }
    }
}

function Invoke-AuthenticodeSign([string]$Path) {
    $CertificatePath = [string]$env:WLIB_SIGNING_CERT_PATH
    if (-not $CertificatePath) {
        Write-Warning "Unsigned Windows artifact: $Path"
        return
    }
    if (-not (Test-Path -LiteralPath $CertificatePath)) {
        throw "WLIB_SIGNING_CERT_PATH does not exist: $CertificatePath"
    }
    $SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source
    $TimestampUrl = if ($env:WLIB_SIGNING_TIMESTAMP_URL) {
        $env:WLIB_SIGNING_TIMESTAMP_URL
    } else {
        "http://timestamp.digicert.com"
    }
    $Arguments = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl, "/f", $CertificatePath)
    if ($env:WLIB_SIGNING_CERT_PASSWORD) {
        $Arguments += @("/p", $env:WLIB_SIGNING_CERT_PASSWORD)
    }
    $Arguments += $Path
    & $SignTool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed for $Path"
    }
}

if (-not $Version) {
    $ApiSource = Get-Content -LiteralPath (Join-Path $RepositoryRoot "core\api.py") -Raw
    $VersionMatch = [regex]::Match($ApiSource, 'APP_VERSION\s*=\s*"(?<version>[0-9]+(?:\.[0-9]+){2})"')
    if (-not $VersionMatch.Success) {
        throw "Could not read APP_VERSION from core/api.py"
    }
    $Version = $VersionMatch.Groups["version"].Value
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Version must contain exactly three numeric parts: $Version"
}
$VersionParts = @($Version.Split('.') | ForEach-Object { [int]$_ })
if ($VersionParts.Count -eq 3) {
    $VersionParts += 0
}
if ($VersionParts[0] -gt 255 -or $VersionParts[1] -gt 255 -or $VersionParts[2] -gt 65535) {
    throw "Version is outside Windows Installer limits: $Version"
}
$MsiVersion = "$($VersionParts[0]).$($VersionParts[1]).$($VersionParts[2])"
$FileVersion = ($VersionParts -join ', ')
$VersionDotted = ($VersionParts -join '.')

Reset-WorkspaceDirectory $BuildRoot
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

if (-not $SkipDependencies) {
    Invoke-Checked "Install Windows Python dependencies" {
        & $Python -m pip install --disable-pip-version-check -r (Join-Path $RepositoryRoot "requirements-windows.txt")
    }
}
if (-not $SkipFrontend) {
    Push-Location (Join-Path $RepositoryRoot "ui")
    try {
        Invoke-Checked "Install frontend dependencies" { npm ci }
        Invoke-Checked "Type-check frontend" { npm run typecheck }
        Invoke-Checked "Build frontend" { npm run build }
    } finally {
        Pop-Location
    }
}

$ExtensionStage = Join-Path $BuildRoot "extension"
New-Item -ItemType Directory -Path $ExtensionStage -Force | Out-Null
Copy-Item -Path (Join-Path $RepositoryRoot "extension\*") -Destination $ExtensionStage -Recurse -Force
$FirefoxStage = Join-Path $ExtensionStage "firefox"
New-Item -ItemType Directory -Path $FirefoxStage -Force | Out-Null
if ($SignedFirefoxXpi) {
    $ResolvedXpi = (Resolve-Path -LiteralPath $SignedFirefoxXpi).Path
    Copy-Item -LiteralPath $ResolvedXpi -Destination (Join-Path $FirefoxStage "wLib.xpi") -Force
} else {
    $UnsignedZip = Join-Path $BuildRoot "wLib-firefox-unsigned.zip"
    Compress-Archive -Path (Join-Path $ExtensionStage "*") -DestinationPath $UnsignedZip -CompressionLevel Optimal
    Move-Item -LiteralPath $UnsignedZip -Destination (Join-Path $FirefoxStage "wLib.xpi") -Force
    Write-Warning "No signed Firefox XPI supplied; packaged fallback XPI is unsigned."
}

$VersionFile = Join-Path $BuildRoot "version-info.txt"
$VersionResource = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($FileVersion), prodvers=($FileVersion), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'wLib Project'),
    StringStruct('FileDescription', 'wLib game library manager'),
    StringStruct('FileVersion', '$VersionDotted'),
    StringStruct('InternalName', 'wLib'),
    StringStruct('LegalCopyright', 'Copyright wLib contributors'),
    StringStruct('OriginalFilename', 'wLib.exe'),
    StringStruct('ProductName', 'wLib'),
    StringStruct('ProductVersion', '$VersionDotted')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@
Set-Content -LiteralPath $VersionFile -Value $VersionResource -Encoding UTF8

$env:WLIB_EXTENSION_STAGE = $ExtensionStage
$env:WLIB_VERSION_FILE = $VersionFile
$FrozenParent = Join-Path $BuildRoot "frozen"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller"
Invoke-Checked "Freeze wLib.exe" {
    & $Python -m PyInstaller --clean --noconfirm `
        --distpath $FrozenParent `
        --workpath $PyInstallerWork `
        (Join-Path $RepositoryRoot "packaging\windows\wlib.spec")
}
$CanonicalDirectory = Join-Path $FrozenParent "wLib"
$Executable = Join-Path $CanonicalDirectory "wLib.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller did not create $Executable"
}
Invoke-AuthenticodeSign $Executable

$SmokeData = Join-Path $BuildRoot "smoke-data"
Reset-WorkspaceDirectory $SmokeData
$PreviousDataDir = $env:WLIB_DATA_DIR
try {
    $env:WLIB_DATA_DIR = $SmokeData
    Invoke-Checked "Run frozen smoke test" { & $Executable --smoke-test }
} finally {
    $env:WLIB_DATA_DIR = $PreviousDataDir
}

$PortableZip = Join-Path $OutputDirectory "wLib-$Version-windows-x64-portable.zip"
if (Test-Path -LiteralPath $PortableZip) {
    Remove-Item -LiteralPath $PortableZip -Force
}
Compress-ArchiveWithRetry `
    -SourcePath (Join-Path $CanonicalDirectory "*") `
    -DestinationPath $PortableZip

$Artifacts = @($PortableZip)
if (-not $SkipMsi) {
    $Wix = (Get-Command wix.exe -ErrorAction SilentlyContinue)
    if (-not $Wix) {
        $Wix = (Get-Command wix -ErrorAction SilentlyContinue)
    }
    if (-not $Wix) {
        throw "WiX v6 is required. Install it with: dotnet tool install --global wix --version 6.0.2"
    }
    Invoke-Checked "Install WiX UI extension" {
        & $Wix.Source extension add -g WixToolset.UI.wixext/6.0.2
    }
    $Msi = Join-Path $OutputDirectory "wLib-$Version-windows-x64.msi"
    Invoke-Checked "Build per-user MSI" {
        & $Wix.Source build `
            -arch x64 `
            -ext WixToolset.UI.wixext `
            -pdbtype none `
            -d "PayloadDir=$CanonicalDirectory" `
            -d "ProductVersion=$MsiVersion" `
            -out $Msi `
            (Join-Path $RepositoryRoot "packaging\windows\wLib.wxs")
    }
    $WixPdb = [IO.Path]::ChangeExtension($Msi, ".wixpdb")
    if (Test-Path -LiteralPath $WixPdb) {
        Remove-Item -LiteralPath $WixPdb -Force
    }
    Invoke-AuthenticodeSign $Msi
    $Artifacts += $Msi
}

$ChecksumFile = Join-Path $OutputDirectory "wLib-$Version-windows-x64-SHA256SUMS.txt"
$ChecksumLines = foreach ($Artifact in $Artifacts) {
    $Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $([IO.Path]::GetFileName($Artifact))"
}
Set-Content -LiteralPath $ChecksumFile -Value $ChecksumLines -Encoding ASCII
$Artifacts += $ChecksumFile

Write-Host "Windows artifacts:"
$Artifacts | ForEach-Object { Write-Host "  $_" }
