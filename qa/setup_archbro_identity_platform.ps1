param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')]
    [string]$AuthDomain,
    [string]$PublicHost = "archbro.magicdala.com",
    [string]$StagingHost = "archbro-dev.magicdala.com"
)

$ErrorActionPreference = "Stop"
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

function Get-RequiredSetupEnvironmentValue {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name is required to provision Firebase Authentication providers."
    }
    return $value.Trim()
}

function Set-DefaultIdentityProvider {
    param(
        [string]$ProviderId,
        [string]$ClientIdEnvironmentVariable,
        [string]$ClientSecretEnvironmentVariable
    )

    $clientId = Get-RequiredSetupEnvironmentValue -Name $ClientIdEnvironmentVariable
    $clientSecret = Get-RequiredSetupEnvironmentValue -Name $ClientSecretEnvironmentVariable
    $providerName = "projects/$ProjectId/defaultSupportedIdpConfigs/$ProviderId"
    $providerUri = "https://identitytoolkit.googleapis.com/admin/v2/$providerName"
    $providerBody = @{
        name = $providerName
        enabled = $true
        clientId = $clientId
        clientSecret = $clientSecret
    }

    try {
        return Invoke-GoogleJson `
            -Method "Patch" `
            -Uri ($providerUri + "?updateMask=enabled,clientId,clientSecret") `
            -Body $providerBody
    } catch {
        if ($_.Exception.Message -notmatch "HTTP 404") { throw }
        $createUri = "https://identitytoolkit.googleapis.com/admin/v2/projects/$ProjectId/defaultSupportedIdpConfigs?idpId=$ProviderId"
        return Invoke-GoogleJson -Method "Post" -Uri $createUri -Body $providerBody
    }
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
foreach ($domain in @($PublicHost, $StagingHost, $AuthDomain)) {
    if ($domain -and $domains -notcontains $domain) { $domains += $domain }
}

$updateUri = $configUri + "?updateMask=signIn.anonymous.enabled,signIn.email.enabled,signIn.email.passwordRequired,authorizedDomains"
$config = Invoke-GoogleJson -Method "Patch" -Uri $updateUri -Body @{
    name = "projects/$ProjectId/config"
    signIn = @{
        anonymous = @{ enabled = $false }
        email = @{
            enabled = $true
            passwordRequired = $true
        }
    }
    authorizedDomains = $domains
}

$googleProvider = Set-DefaultIdentityProvider `
    -ProviderId "google.com" `
    -ClientIdEnvironmentVariable "ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_ID" `
    -ClientSecretEnvironmentVariable "ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_SECRET"
$githubProvider = Set-DefaultIdentityProvider `
    -ProviderId "github.com" `
    -ClientIdEnvironmentVariable "ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_ID" `
    -ClientSecretEnvironmentVariable "ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_SECRET"

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
$allowedReferrers = @()
foreach ($domain in @($PublicHost, $StagingHost, $AuthDomain)) {
    if ($domain) { $allowedReferrers += "https://$domain/*" }
}

if (-not $keyName) {
    # Do not capture create output: gcloud may emit the completed operation payload,
    # including the key string. Resolve the resource name separately via JSON list.
    & $gcloud services api-keys create `
        --project $ProjectId `
        --display-name="ArchBro Browser Auth" `
        --api-target=service=identitytoolkit.googleapis.com `
        --api-target=service=securetoken.googleapis.com `
        --allowed-referrers=($allowedReferrers -join ",") `
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

# Reconcile restrictions on every run. This also upgrades a key created before
# AuthDomain became part of the browser popup contract.
& $gcloud services api-keys update $keyName `
    --project $ProjectId `
    --api-target=service=identitytoolkit.googleapis.com `
    --api-target=service=securetoken.googleapis.com `
    --allowed-referrers=($allowedReferrers -join ",") `
    --quiet 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw "ArchBro browser API key restrictions could not be updated." }

$apiKey = ((& $gcloud services api-keys get-key-string $keyName --format='value(keyString)') -join "").Trim()
if ($LASTEXITCODE -ne 0 -or -not $apiKey) { throw "ArchBro browser API key string could not be read." }

@{
    apiKey = $apiKey
    authDomain = $AuthDomain
    projectId = $ProjectId
    appId = ""
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 ".archbro-firebase-public.json"

[pscustomobject]@{
    projectId = $ProjectId
    authDomain = $AuthDomain
    emailPasswordEnabled = [bool]$config.signIn.email.enabled -and [bool]$config.signIn.email.passwordRequired
    anonymousEnabled = [bool]$config.signIn.anonymous.enabled
    googleEnabled = [bool]$googleProvider.enabled
    githubEnabled = [bool]$githubProvider.enabled
    authorizedPublicDomain = @($config.authorizedDomains) -contains $PublicHost
    authorizedStagingDomain = (-not $StagingHost) -or (@($config.authorizedDomains) -contains $StagingHost)
    authorizedAuthDomain = @($config.authorizedDomains) -contains $AuthDomain
    apiKeyRestricted = $true
    configSaved = $true
} | ConvertTo-Json -Compress
