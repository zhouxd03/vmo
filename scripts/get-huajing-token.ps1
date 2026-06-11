param(
    [string]$AppDataRoot = $env:APPDATA,
    [string[]]$SearchRoot,
    [string]$OutFile,
    [switch]$Reveal,
    [switch]$AllAppData,
    [switch]$VerifyRefresh,
    [int64]$MaxFileBytes = 100MB
)

$ErrorActionPreference = "Stop"

function Mask-Secret {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    if ($Value.Length -le 16) { return ("*" * $Value.Length) }
    return "$($Value.Substring(0, 8))...$($Value.Substring($Value.Length - 6))"
}

function Show-Secret {
    param([string]$Value)
    if ($Reveal) { return $Value }
    return Mask-Secret $Value
}

function ConvertFrom-Base64Url {
    param([string]$Value)
    $s = $Value.Replace('-', '+').Replace('_', '/')
    switch ($s.Length % 4) {
        2 { $s += '==' }
        3 { $s += '=' }
        1 { return $null }
    }
    try {
        return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
    } catch {
        return $null
    }
}

function Get-JwtInfo {
    param([string]$Token)
    if ([string]::IsNullOrWhiteSpace($Token) -or $Token.Split('.').Count -ne 3) { return $null }
    $payloadText = ConvertFrom-Base64Url $Token.Split('.')[1]
    if (-not $payloadText) { return $null }
    try {
        $payload = $payloadText | ConvertFrom-Json
    } catch {
        return $null
    }

    $expText = ""
    $isExpired = $null
    if ($payload.exp) {
        try {
            $expDate = [DateTimeOffset]::FromUnixTimeSeconds([int64]$payload.exp).LocalDateTime
            $expText = $expDate.ToString("yyyy-MM-dd HH:mm:ss")
            $isExpired = $expDate -lt (Get-Date)
        } catch {}
    }

    $uid = ""
    if ($payload.uid) { $uid = [string]$payload.uid }
    elseif ($payload.user_id) { $uid = [string]$payload.user_id }

    $sub = ""
    if ($payload.sub) { $sub = [string]$payload.sub }

    $role = ""
    if ($payload.role) { $role = [string]$payload.role }

    return [pscustomobject]@{
        TokenType = [string]$payload.token_type
        Exp = $expText
        Expired = $isExpired
        Uid = $uid
        Sub = $sub
        Role = $role
        RawPayload = $payloadText
    }
}

function Get-TextCandidates {
    param([string]$Path)
    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
        )
        $bytes = New-Object byte[] $stream.Length
        [void]$stream.Read($bytes, 0, $bytes.Length)
    } finally {
        if ($stream) { $stream.Dispose() }
    }
    @(
        [System.Text.Encoding]::UTF8.GetString($bytes),
        [System.Text.Encoding]::Unicode.GetString($bytes),
        [System.Text.Encoding]::ASCII.GetString($bytes)
    ) | Select-Object -Unique
}

function Add-Result {
    param(
        [System.Collections.Generic.List[object]]$Results,
        [string]$SourceFile,
        [string]$Key,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    $clean = $Value.Trim().Trim('"', "'", "`0", "`r", "`n", " ", "`t")
    if ($clean.Length -lt 12) { return }

    $dedupeKey = "$Key|$clean"
    if ($script:Seen.ContainsKey($dedupeKey)) { return }
    $script:Seen[$dedupeKey] = $true
    $script:ResultId++

    $Results.Add([pscustomobject]@{
        Id = $script:ResultId
        Key = $Key
        Value = Show-Secret $clean
        RawValue = $clean
        Length = $clean.Length
        Source = $SourceFile
    }) | Out-Null
}

function Test-RefreshToken {
    param([string]$RefreshToken)
    try {
        $body = @{ refresh_token = $RefreshToken } | ConvertTo-Json -Compress
        $response = Invoke-RestMethod -Method Post -Uri "https://aibac.lizer.cc/api/auth/refresh" -ContentType "application/json" -Body $body -TimeoutSec 15
        if ($response.success -eq $true -and $response.data.access_token) {
            return "refresh_ok"
        }
        if ($response.access_token) {
            return "refresh_ok"
        }
        return "refresh_unknown_response"
    } catch {
        $status = $null
        try { $status = $_.Exception.Response.StatusCode.value__ } catch {}
        if ($status) { return "refresh_failed_http_$status" }
        return "refresh_failed"
    }
}

function Find-Tokens-InFile {
    param(
        [string]$Path,
        [System.Collections.Generic.List[object]]$Results
    )

    try {
        foreach ($text in Get-TextCandidates $Path) {
            if ([string]::IsNullOrWhiteSpace($text)) { continue }

            $patterns = @(
                '(?i)"token"\s*:\s*"([^"]+)"',
                '(?i)"access_token"\s*:\s*"([^"]+)"',
                '(?i)"refresh_token"\s*:\s*"([^"]+)"',
                '(?i)token["'']?\s*[,=:]\s*["'']([^"'']{20,})["'']',
                '(?i)refresh_token["'']?\s*[,=:]\s*["'']([^"'']{20,})["'']',
                '(?i)access_token["'']?\s*[,=:]\s*["'']([^"'']{20,})["'']'
            )

            foreach ($pattern in $patterns) {
                foreach ($match in [regex]::Matches($text, $pattern)) {
                    $keyGuess = if ($pattern -match 'refresh') { 'refresh_token' }
                        elseif ($pattern -match 'access') { 'access_token' }
                        else { 'token' }
                    Add-Result -Results $Results -SourceFile $Path -Key $keyGuess -Value $match.Groups[1].Value
                }
            }

            foreach ($match in [regex]::Matches($text, 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}')) {
                Add-Result -Results $Results -SourceFile $Path -Key 'jwt_candidate' -Value $match.Value
            }

            foreach ($key in @('token', 'access_token', 'refresh_token')) {
                foreach ($keyMatch in [regex]::Matches($text, [regex]::Escape($key), 'IgnoreCase')) {
                    $start = [Math]::Max(0, $keyMatch.Index - 200)
                    $length = [Math]::Min(3000, $text.Length - $start)
                    $window = $text.Substring($start, $length)

                    foreach ($nearbyJwt in [regex]::Matches($window, 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}')) {
                        Add-Result -Results $Results -SourceFile $Path -Key "${key}_nearby_jwt" -Value $nearbyJwt.Value
                    }

                    $loosePatterns = @(
                        '[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{10,}',
                        '[A-Za-z0-9_-]{48,}'
                    )
                    foreach ($loosePattern in $loosePatterns) {
                        foreach ($nearbySecret in [regex]::Matches($window, $loosePattern)) {
                            $candidate = $nearbySecret.Value
                            if ($candidate -notmatch '^(token|refresh_token|access_token)$') {
                                Add-Result -Results $Results -SourceFile $Path -Key "${key}_nearby_candidate" -Value $candidate
                            }
                        }
                    }
                }
            }
        }
    } catch {
        Write-Verbose "Skip ${Path}: $($_.Exception.Message)"
    }
}

function Add-FileIfCandidate {
    param([System.IO.FileInfo]$File)

    if (-not $File) { return }
    $isCandidate =
        ($File.Length -gt 0 -and $File.Length -lt $MaxFileBytes -and $File.Extension -in @('.ldb', '.log', '.json', '.txt', '')) -or
        ($File.Name -in @('Preferences', 'Local State'))

    if ($isCandidate) {
        $candidateFiles.Add($File.FullName) | Out-Null
    }
}

function Add-FilesFromDirectory {
    param(
        [string]$Path,
        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $Path)) { return }
    $items = if ($Recurse) {
        Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue
    } else {
        Get-ChildItem -LiteralPath $Path -File -Force -ErrorAction SilentlyContinue
    }

    foreach ($item in $items) {
        Add-FileIfCandidate -File $item
    }
}

if (-not (Test-Path -LiteralPath $AppDataRoot)) {
    throw "AppDataRoot not found: $AppDataRoot"
}

$namePatterns = @(
    'huajing',
    'hjai',
    'hua-jing',
    'comic-generator',
    'comic-generator-electron',
    'comic_generator',
    'electron',
    'webview',
    'banana',
    'lizer'
)

$candidateFiles = New-Object System.Collections.Generic.List[string]

function Add-Candidate-FilesFromRoot {
    param([string]$Root)

    if ([string]::IsNullOrWhiteSpace($Root)) { return }
    $resolvedRoot = (Resolve-Path -LiteralPath $Root -ErrorAction SilentlyContinue | Select-Object -First 1).Path
    if (-not $resolvedRoot) {
        Write-Warning "Search root not found: $Root"
        return
    }

    $leaf = Split-Path -Leaf $resolvedRoot
    if ($leaf -in @('leveldb', 'Session Storage', 'IndexedDB', 'Cache_Data', 'Code Cache', 'Network', 'Cache')) {
        Add-FilesFromDirectory -Path $resolvedRoot -Recurse
        return
    }

    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'Local Storage\leveldb') -Recurse
    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'Session Storage') -Recurse
    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'IndexedDB') -Recurse
    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'Cache\Cache_Data') -Recurse
    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'Code Cache') -Recurse
    Add-FilesFromDirectory -Path (Join-Path $resolvedRoot 'Network') -Recurse
    Add-FilesFromDirectory -Path $resolvedRoot
}

if ($SearchRoot -and $SearchRoot.Count -gt 0) {
    foreach ($root in $SearchRoot) {
        Write-Host "Scanning search root: $root"
        Add-Candidate-FilesFromRoot -Root $root
    }
} else {
    $appDirs = if ($AllAppData) {
        Get-ChildItem -LiteralPath $AppDataRoot -Directory -Force
    } else {
        Get-ChildItem -LiteralPath $AppDataRoot -Directory -Force | Where-Object {
            $name = $_.Name.ToLowerInvariant()
            $matched = $false
            foreach ($pattern in $namePatterns) {
                if ($name -like "*$($pattern.ToLowerInvariant())*") {
                    $matched = $true
                    break
                }
            }
            $matched
        }
    }

    foreach ($dir in $appDirs) {
        Write-Host "Scanning app data folder: $($dir.FullName)"
        Add-Candidate-FilesFromRoot -Root $dir.FullName
    }
}

$script:Seen = @{}
$script:ResultId = 0
$results = New-Object System.Collections.Generic.List[object]
$filesToScan = @($candidateFiles | Select-Object -Unique)
Write-Host "Candidate files: $($filesToScan.Count)"

$index = 0
foreach ($file in $filesToScan) {
    $index++
    if ($index -eq 1 -or $index % 100 -eq 0) {
        Write-Host "Scanning file $index / $($filesToScan.Count)"
    }
    Find-Tokens-InFile -Path $file -Results $results
}

if ($results.Count -eq 0) {
    Write-Host "No token found. Try:"
    Write-Host "  1. Make sure Huajing is logged in, then run this script again."
    Write-Host "  2. Scan a copied user-data folder:"
    Write-Host "     powershell -ExecutionPolicy Bypass -File .\scripts\get-huajing-token.ps1 -SearchRoot `"D:\path\to\comic-generator-electron`" -Reveal"
    Write-Host "  3. Scan all AppData folders:"
    Write-Host "     powershell -ExecutionPolicy Bypass -File .\scripts\get-huajing-token.ps1 -AllAppData"
    Write-Host "  4. In Huajing DevTools Console, run:"
    Write-Host "     localStorage.getItem('token')"
    exit 1
}

$ranked = foreach ($result in $results) {
    $jwtInfo = Get-JwtInfo $result.RawValue
    $score = 0
    if ($result.Key -eq 'token') { $score += 100 }
    if ($result.Key -eq 'refresh_token') { $score += 90 }
    if ($result.Key -like '*nearby_jwt') { $score += 60 }
    if ($result.Key -eq 'jwt_candidate') { $score += 40 }
    if ($jwtInfo) { $score += 50 }
    if ($jwtInfo.TokenType -eq 'access') { $score += 80 }
    if ($jwtInfo.TokenType -eq 'refresh') { $score += 70 }
    if ($jwtInfo.Expired -eq $false) { $score += 30 }
    if ($jwtInfo.Expired -eq $true) { $score -= 80 }
    if ($result.RawValue -like 'auth1_*') { $score += 20 }
    if ($result.RawValue -notmatch '\.' -and $result.RawValue -notlike 'auth1_*') { $score -= 30 }

    $verify = ""
    if ($VerifyRefresh -and (($jwtInfo -and $jwtInfo.TokenType -eq 'refresh') -or $result.Key -eq 'refresh_token')) {
        $verify = Test-RefreshToken $result.RawValue
        if ($verify -eq 'refresh_ok') { $score += 200 }
    }

    [pscustomobject]@{
        Id = $result.Id
        Recommended = $score
        Key = $result.Key
        JwtType = if ($jwtInfo) { $jwtInfo.TokenType } else { "" }
        Exp = if ($jwtInfo) { $jwtInfo.Exp } else { "" }
        Expired = if ($jwtInfo -and $null -ne $jwtInfo.Expired) { $jwtInfo.Expired } else { "" }
        Verify = $verify
        Value = $result.Value
        Length = $result.Length
        Source = $result.Source
    }
}

$sortedRanked = @($ranked | Sort-Object Recommended -Descending)
$bestAccess = $sortedRanked | Where-Object { $_.JwtType -eq 'access' -and $_.Expired -ne $true } | Select-Object -First 1
if (-not $bestAccess) {
    $bestAccess = $sortedRanked | Where-Object { $_.Key -eq 'access_token' -or $_.Key -like '*nearby_jwt' -or $_.Key -eq 'jwt_candidate' } | Select-Object -First 1
}
$bestRefresh = $sortedRanked | Where-Object { $_.Key -eq 'refresh_token' -or $_.JwtType -eq 'refresh' } | Select-Object -First 1

Write-Host ""
Write-Host "Best guess:"
if ($bestAccess) {
    Write-Host "  Access token : Id=$($bestAccess.Id), Exp=$($bestAccess.Exp), Source=$($bestAccess.Source)"
} else {
    Write-Host "  Access token : not found"
}
if ($bestRefresh) {
    Write-Host "  Refresh token: Id=$($bestRefresh.Id), Verify=$($bestRefresh.Verify), Source=$($bestRefresh.Source)"
} else {
    Write-Host "  Refresh token: not found"
}
Write-Host ""

$sortedRanked | Format-Table -AutoSize

if ($OutFile) {
    $exportRows = foreach ($row in $sortedRanked) {
        $raw = ($results | Where-Object { $_.Id -eq $row.Id } | Select-Object -First 1).RawValue
        [pscustomobject]@{
            id = $row.Id
            recommended = $row.Recommended
            key = $row.Key
            jwtType = $row.JwtType
            exp = $row.Exp
            expired = $row.Expired
            verify = $row.Verify
            value = if ($Reveal) { $raw } else { $row.Value }
            length = $row.Length
            source = $row.Source
        }
    }

    function Get-ExportValueByRowId {
        param($Row)
        if (-not $Row) { return "" }
        $raw = ($results | Where-Object { $_.Id -eq $Row.Id } | Select-Object -First 1).RawValue
        if ($Reveal) { return $raw }
        return $Row.Value
    }

    $export = [pscustomobject]@{
        generatedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        baseUrl = "https://aibac.lizer.cc"
        authorizationHeader = "Authorization: Bearer <access_token>"
        refreshEndpoint = "POST https://aibac.lizer.cc/api/auth/refresh"
        bestAccessToken = Get-ExportValueByRowId $bestAccess
        bestRefreshToken = Get-ExportValueByRowId $bestRefresh
        bestAccessTokenSource = if ($bestAccess) { $bestAccess.Source } else { "" }
        bestRefreshTokenSource = if ($bestRefresh) { $bestRefresh.Source } else { "" }
        candidates = $exportRows
    }

    $export | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutFile -Encoding UTF8
    Write-Host ""
    Write-Host "Saved token report to: $OutFile"
}

if (-not $Reveal) {
    Write-Host ""
    Write-Host "Values are masked by default. To print full values, run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\get-huajing-token.ps1 -Reveal"
}

Write-Host ""
Write-Host "How to choose:"
Write-Host "  - Prefer JwtType=access with Expired=False for Authorization: Bearer <token>."
Write-Host "  - Prefer JwtType=refresh or Key=refresh_token for /api/auth/refresh."
Write-Host "  - If you used -VerifyRefresh, Verify=refresh_ok is the best refresh token."
