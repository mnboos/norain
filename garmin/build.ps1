#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SdkPath,
    [string]$DeveloperKey = "$PSScriptRoot/private/developer_key.der",
    [string]$SettingsFile,
    [switch]$Package
)
$ErrorActionPreference = 'Stop'
$compiler = Join-Path (Resolve-Path -LiteralPath $SdkPath) 'bin/monkeyc.bat'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'SDK must contain bin/monkeyc.bat.' }
$private = Join-Path $PSScriptRoot 'private'
$output = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force -Path $private, $output | Out-Null
if (-not (Test-Path -LiteralPath $DeveloperKey)) {
    $rsa = [System.Security.Cryptography.RSA]::Create(4096)
    try { [IO.File]::WriteAllBytes($DeveloperKey, $rsa.ExportPkcs8PrivateKey()) }
    finally { $rsa.Dispose() }
}
$jungle = Join-Path $PSScriptRoot 'monkey.jungle'
if ($SettingsFile) {
    if ($Package) { throw 'Do not package a personal watch token for distribution.' }
    $config = Get-Content -LiteralPath $SettingsFile -Raw | ConvertFrom-Json
    $server = [uri]$config.server
    $localServer = $server.Scheme -eq 'http' -and $server.Host -in @('localhost', '127.0.0.1') -and $server.Port -eq 8000 -and $server.AbsolutePath -eq '/' -and -not $server.Query -and -not $server.Fragment -and -not $server.UserInfo
    if (($server.Scheme -ne 'https' -and -not $localServer) -or -not $config.token) {
        throw 'Settings need HTTPS (or localhost:8000 for the simulator) and a watch token.'
    }
    $resources = Join-Path $private 'resources'
    New-Item -ItemType Directory -Force -Path $resources | Out-Null
    Copy-Item -Path "$PSScriptRoot/resources/*" -Destination $resources -Recurse -Force
    $properties = [xml](Get-Content -LiteralPath "$resources/properties/properties.xml" -Raw)
    ($properties.properties.property | Where-Object id -eq 'server').InnerText = $config.server.TrimEnd('/')
    ($properties.properties.property | Where-Object id -eq 'token').InnerText = $config.token
    $properties.Save("$resources/properties/properties.xml")
    $root = $PSScriptRoot.Replace('\', '/')
    $jungle = Join-Path $private 'personal.jungle'
    @"
project.manifest = $root/manifest.xml
base.sourcePath = $root/source
base.resourcePath = $root/private/resources
"@ | Set-Content -LiteralPath $jungle -Encoding utf8NoBOM
}
$artifact = Join-Path $output $(if ($Package) { 'MeteoLane.iq' } else { 'MeteoLane.prg' })
$arguments = @('-f', $jungle, '-o', $artifact, '-y', $DeveloperKey, '-w', '-r')
if ($Package) { $arguments += '-e' } else { $arguments += @('-d', 'fr255') }
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "Connect IQ build failed ($LASTEXITCODE)." }
Write-Output $artifact
