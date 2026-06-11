param(
    [string]$TokenReport = (Join-Path (Resolve-Path "$PSScriptRoot\..").Path "token-result.json"),
    [string]$CredentialsFile = (Join-Path (Resolve-Path "$PSScriptRoot\..").Path "data\credentials.json"),
    [string]$Alias = "Huajing AI",
    [string]$Model = "foldin"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $TokenReport)) {
    throw "Token report not found: $TokenReport. Run run-huajing-token-check.bat first."
}

$report = Get-Content -LiteralPath $TokenReport -Raw | ConvertFrom-Json
$token = [string]$report.bestAccessToken
if ([string]::IsNullOrWhiteSpace($token) -or $token -notmatch '^eyJ') {
    throw "bestAccessToken is missing or invalid in: $TokenReport"
}

$dataDir = Split-Path -Parent $CredentialsFile
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}

if (Test-Path -LiteralPath $CredentialsFile) {
    $rawVault = Get-Content -LiteralPath $CredentialsFile -Raw
    try {
        $vault = $rawVault | ConvertFrom-Json
    } catch {
        $fixedVault = [regex]::Replace(
            $rawVault,
            '(?m)^(\s*"alias"\s*:\s*)"([^"\r\n]*),\s*$',
            '$1"Recovered alias",'
        )
        $vault = $fixedVault | ConvertFrom-Json
        $backup = "$CredentialsFile.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
        Copy-Item -LiteralPath $CredentialsFile -Destination $backup -Force
        $fixedVault | Set-Content -LiteralPath $CredentialsFile -Encoding UTF8
        Write-Host "Fixed malformed credential JSON. Backup: $backup"
    }
} else {
    $vault = [pscustomobject]@{
        image = @()
        video = @()
        llm = @()
        image_host = @()
    }
}

foreach ($category in @("image", "video", "llm", "image_host")) {
    if (-not ($vault.PSObject.Properties.Name -contains $category)) {
        $vault | Add-Member -MemberType NoteProperty -Name $category -Value @()
    }
}

$encodedKey = "b64:" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($token))
$existing = @($vault.video) | Where-Object { $_.provider -eq "huajing" -or $_.alias -eq $Alias } | Select-Object -First 1

if ($existing) {
    $existing.alias = $Alias
    $existing.base_url = "https://aibac.lizer.cc"
    $existing.model = $Model
    $existing.provider = "huajing"
    $existing.enabled = $true
    $existing.is_default = $true
    $existing.api_key = $encodedKey
    $existing.note = "Uses your Huajing account credits via Authorization Bearer token."
} else {
    $id = [Guid]::NewGuid().ToString("N").Substring(0, 12)
    $entry = [pscustomobject]@{
        id = $id
        alias = $Alias
        base_url = "https://aibac.lizer.cc"
        model = $Model
        provider = "huajing"
        enabled = $true
        is_default = $true
        note = "Uses your Huajing account credits via Authorization Bearer token."
        api_key = $encodedKey
    }
    $vault.video = @($vault.video) + $entry
}

$vault.video | ForEach-Object {
    if ($_.provider -ne "huajing" -and $_.alias -ne $Alias) {
        $_.is_default = $false
    }
}

$vault | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $CredentialsFile -Encoding UTF8

Write-Host "Installed Huajing video credential."
Write-Host "Credentials file: $CredentialsFile"
Write-Host "Provider: huajing"
Write-Host "Base URL: https://aibac.lizer.cc"
Write-Host "Model: $Model"
