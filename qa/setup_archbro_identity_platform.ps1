param(
    [string]$ProjectId = "keys-by-friday-2026-kbf"
)

$ErrorActionPreference = "Stop"
$publicHost = "archbro.hoson.xyz"
$runHost = "archbro-webmcp-23051378248.us-west1.run.app"
$alternateRunHost = "archbro-webmcp-fbfcgmlcsq-uw.a.run.app"
$gcloud = (Get-Command gcloud.cmd -ErrorAction Stop).Source

& $gcloud services enable identitytoolkit.googleapis.com apikeys.googleapis.com --project $ProjectId --quiet | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Required Google APIs could not be enabled." }

$token = ((& $gcloud auth print-access-token) -join "").Trim()
if ($LASTEXITCODE -ne 0 -or -not $token) { throw "Google access token could not be obtained." }
Add-Type -AssemblyName System.Net.Http
$client = [System.Net.Http.HttpClient]::new()
$client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $token)
$client.DefaultRequestHeaders.Add("x-goog-user-project", $ProjectId)

function Invoke-GoogleJson {
    param(
        [string]$Method,
        [string]$Uri,
        [object]$Body = $null
    )
    $httpMethod = [System.Net.Http.HttpMethod]::new($Method.ToUpperInvariant())
    $request = [System.Net.Http.HttpRequestMessage]::new($httpMethod, $Uri)
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 10 -Compress
        $request.Content = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, "application/json")
    }
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        throw "Google API $Method $Uri failed with HTTP $([int]$response.StatusCode): $text"
    }
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    return $text | ConvertFrom-Json
}

$configUri = "https://identitytoolkit.googleapis.com/admin/v2/projects/$ProjectId/config"
try {
    $config = Invoke-GoogleJson -Method "Get" -Uri $configUri
} catch {
    if ($_.Exception.Message -notmatch "HTTP 404") { throw }
    $initializeUri = "https://identitytoolkit.googleapis.com/v2/projects/$ProjectId/identityPlatform:initializeAuth"
    $null = Invoke-GoogleJson -Method "Post" -Uri $initializeUri -Body @{}
    $config = Invoke-GoogleJson -Method "Get" -Uri $configUri
}

$domains = @($config.authorizedDomains)
foreach ($domain in @($publicHost, $runHost, $alternateRunHost)) {
    if ($domains -notcontains $domain) { $domains += $domain }
}

$updateUri = $configUri + "?updateMask=signIn.anonymous.enabled,authorizedDomains"
$config = Invoke-GoogleJson -Method "Patch" -Uri $updateUri -Body @{
    name = "projects/$ProjectId/config"
    signIn = @{ anonymous = @{ enabled = $true } }
    authorizedDomains = $domains
}

function Get-ArchBroBrowserApiKeys {
    $json = ((& $gcloud services api-keys list --project $ProjectId --format=json) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not list ArchBro browser API keys." }
    if (-not $json) { return @() }
    $parsed = $json | ConvertFrom-Json
    return @(
        $parsed |
            Where-Object { $_.displayName -eq "ArchBro Browser Auth" } |
            Sort-Object createTime -Descending
    )
}

$matchingKeys = @(Get-ArchBroBrowserApiKeys)
$keyName = if ($matchingKeys.Count -gt 0) { [string]$matchingKeys[0].name } else { "" }

if (-not $keyName) {
    # Do not capture create output: gcloud may emit the completed operation payload,
    # including the key string. Resolve the resource name separately via JSON list.
    & $gcloud services api-keys create `
        --project $ProjectId `
        --display-name="ArchBro Browser Auth" `
        --api-target=service=identitytoolkit.googleapis.com `
        --api-target=service=securetoken.googleapis.com `
        --allowed-referrers="https://$publicHost/*,https://$runHost/*,https://$alternateRunHost/*" `
        --async `
        --quiet 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw "ArchBro browser API key creation failed." }

    for ($attempt = 0; $attempt -lt 15 -and -not $keyName; $attempt++) {
        Start-Sleep -Seconds 2
        $matchingKeys = @(Get-ArchBroBrowserApiKeys)
        if ($matchingKeys.Count -gt 0) { $keyName = [string]$matchingKeys[0].name }
    }
}

if (-not $keyName) { throw "ArchBro browser API key was not created." }
$apiKey = ((& $gcloud services api-keys get-key-string $keyName --format='value(keyString)') -join "").Trim()
if ($LASTEXITCODE -ne 0) { throw "ArchBro browser API key string could not be read." }
if (-not $apiKey) { throw "ArchBro browser API key string could not be read." }

@{
    apiKey = $apiKey
    authDomain = ""
    projectId = $ProjectId
    appId = ""
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 ".archbro-firebase-public.json"

[pscustomobject]@{
    projectId = $ProjectId
    anonymousEnabled = [bool]$config.signIn.anonymous.enabled
    authorizedPublicDomain = @($config.authorizedDomains) -contains $publicHost
    apiKeyRestricted = $true
    configSaved = $true
} | ConvertTo-Json -Compress
