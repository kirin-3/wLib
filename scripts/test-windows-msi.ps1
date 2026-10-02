# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MsiPath,
    [string]$UpgradeVersion = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$MsiPath = (Resolve-Path -LiteralPath $MsiPath).Path
if (-not $UpgradeVersion) {
    $VersionMatch = [regex]::Match(
        [IO.Path]::GetFileName($MsiPath),
        'wLib-(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)-windows-x64\.msi'
    )
    if (-not $VersionMatch.Success) {
        throw "Cannot infer upgrade version from MSI filename; pass -UpgradeVersion."
    }
    $NextPatch = [int]$VersionMatch.Groups["patch"].Value + 1
    $UpgradeVersion = "$($VersionMatch.Groups['major'].Value).$($VersionMatch.Groups['minor'].Value).$NextPatch"
}
$LifecycleRoot = [IO.Path]::GetFullPath(
    (Join-Path $RepositoryRoot "build\windows\msi-lifecycle")
)
$AllowedPrefix = $RepositoryRoot.TrimEnd('\') + '\'
if (-not $LifecycleRoot.StartsWith(
    $AllowedPrefix,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unsafe lifecycle directory: $LifecycleRoot"
}

$UninstallRoots = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$ExistingInstall = Get-ItemProperty $UninstallRoots -ErrorAction SilentlyContinue |
    Where-Object {
        $_.PSObject.Properties["DisplayName"] -and $_.DisplayName -eq "wLib"
    }
if ($ExistingInstall) {
    throw "A per-user wLib MSI is already installed; lifecycle test will not replace it."
}

if (Test-Path -LiteralPath $LifecycleRoot) {
    Remove-Item -LiteralPath $LifecycleRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $LifecycleRoot -Force | Out-Null
$SeedData = Join-Path $LifecycleRoot "seeded-user-data"
New-Item -ItemType Directory -Path $SeedData -Force | Out-Null
Set-Content `
    -LiteralPath (Join-Path $SeedData "preserve-me.txt") `
    -Value "keep across MSI lifecycle" `
    -Encoding UTF8

$Wix = Get-Command wix.exe -ErrorAction SilentlyContinue
if (-not $Wix) {
    $Wix = Get-Command wix -ErrorAction Stop
}
$PayloadDirectory = Join-Path $RepositoryRoot "build\windows\frozen\wLib"
$UpgradeMsi = Join-Path $LifecycleRoot "wLib-$UpgradeVersion-windows-x64.msi"
& $Wix.Source build `
    -arch x64 `
    -ext WixToolset.UI.wixext `
    -pdbtype none `
    -d "PayloadDir=$PayloadDirectory" `
    -d "ProductVersion=$UpgradeVersion" `
    -out $UpgradeMsi `
    (Join-Path $RepositoryRoot "packaging\windows\wLib.wxs")
if ($LASTEXITCODE -ne 0) {
    throw "Upgrade MSI build failed with exit code $LASTEXITCODE"
}

function Invoke-Msi(
    [string[]]$Arguments,
    [string]$Label,
    [int[]]$AllowedExitCodes = @(0, 3010)
) {
    $Process = Start-Process `
        -FilePath "msiexec.exe" `
        -ArgumentList $Arguments `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($Process.ExitCode -notin $AllowedExitCodes) {
        throw "$Label failed with exit code $($Process.ExitCode)"
    }
    Write-Host "$Label exit code: $($Process.ExitCode)"
}

function Quoted([string]$Value) {
    return '"' + $Value + '"'
}

$InstallDirectory = Join-Path $env:LOCALAPPDATA "Programs\wLib"
$StartMenuShortcut = Join-Path `
    $env:APPDATA `
    "Microsoft\Windows\Start Menu\Programs\wLib\wLib.lnk"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "wLib.lnk"
$UpgradeInstalled = $false

try {
    Invoke-Msi @(
        "/i", (Quoted $MsiPath), "/qn", "/norestart",
        "INSTALLDESKTOPSHORTCUT=1", "/l*v",
        (Quoted (Join-Path $LifecycleRoot "install.log"))
    ) "Fresh install"

    $InstalledExecutable = Join-Path $InstallDirectory "wLib.exe"
    if (-not (Test-Path -LiteralPath $InstalledExecutable)) {
        throw "Installed executable is missing: $InstalledExecutable"
    }
    if (-not (Test-Path -LiteralPath $StartMenuShortcut)) {
        throw "Start Menu shortcut was not created."
    }
    if (-not (Test-Path -LiteralPath $DesktopShortcut)) {
        throw "Desktop shortcut was not created."
    }

    $PreviousDataDirectory = $env:WLIB_DATA_DIR
    try {
        $env:WLIB_DATA_DIR = $SeedData
        $SmokeProcess = Start-Process `
            -FilePath $InstalledExecutable `
            -ArgumentList "--smoke-test" `
            -Wait `
            -PassThru `
            -WindowStyle Hidden
        if ($SmokeProcess.ExitCode -ne 0) {
            throw "Installed smoke test failed with exit code $($SmokeProcess.ExitCode)"
        }
    } finally {
        $env:WLIB_DATA_DIR = $PreviousDataDirectory
    }

    Invoke-Msi @(
        "/fa", (Quoted $MsiPath), "/qn", "/norestart",
        "INSTALLDESKTOPSHORTCUT=1", "/l*v",
        (Quoted (Join-Path $LifecycleRoot "repair.log"))
    ) "Repair"

    $UpgradeLog = Join-Path $LifecycleRoot "upgrade.log"
    Invoke-Msi @(
        "/i", (Quoted $UpgradeMsi), "/qn", "/norestart",
        "INSTALLDESKTOPSHORTCUT=1", "/l*v",
        (Quoted $UpgradeLog)
    ) "Major upgrade"
    $UpgradeInstalled = $true

    $UpgradeLogText = Get-Content -LiteralPath $UpgradeLog -Raw
    if (
        $UpgradeLogText -notmatch "ProductVersion\s*=\s*$([regex]::Escape($UpgradeVersion))" -or
        $UpgradeLogText -notmatch "Product: wLib -- Installation completed successfully"
    ) {
        throw "Upgrade log did not confirm installation of version $UpgradeVersion."
    }
} finally {
    $UninstallTarget = if ($UpgradeInstalled) { $UpgradeMsi } else { $MsiPath }
    Invoke-Msi @(
        "/x", (Quoted $UninstallTarget), "/qn", "/norestart", "/l*v",
        (Quoted (Join-Path $LifecycleRoot "uninstall.log"))
    ) "Uninstall" @(0, 1605, 3010)
}

if (Test-Path -LiteralPath $InstallDirectory) {
    throw "Install directory remained after uninstall: $InstallDirectory"
}
if (Test-Path -LiteralPath $StartMenuShortcut) {
    throw "Start Menu shortcut remained after uninstall."
}
if (Test-Path -LiteralPath $DesktopShortcut) {
    throw "Desktop shortcut remained after uninstall."
}
if (-not (Test-Path -LiteralPath (Join-Path $SeedData "preserve-me.txt"))) {
    throw "Seeded user data was removed during MSI lifecycle."
}

Write-Host "MSI lifecycle validation passed."
