<#
.SYNOPSIS
  Build the pinned Primee voice manifest from PUBLIC METADATA ONLY.

.DESCRIPTION
  Windows PowerShell 5.1. Read-only with one exception: it writes ONE JSON file,
  the manifest, at -OutputPath (default: %LOCALAPPDATA%\Primee\manifests\...),
  which is outside the Git repository.

  It downloads NO model, NO wheel, NO archive and NO executable. It installs
  nothing, changes no setting, touches no PATH and creates no scheduled task.

  What it fetches (metadata JSON and four tiny text files only):
    * PyPI JSON for sherpa-onnx and sherpa-onnx-core at the EXACT pinned version,
      recording the two Windows wheel file names, sizes and publisher SHA-256.
      If PyPI's values differ from the values reviewed in the session, it STOPS.
    * Hugging Face model API for csukuangfj/vits-mimic3-fa-haaniye_low: the
      current commit id (the manifest pins it), licence metadata (recorded as
      absent when absent; never invented), and the recursive file tree with
      sizes, LFS SHA-256 for LFS files and Git blob SHA-1 for the rest.
    * GitHub: the current commit id of MycroftAI/mimic3-voices and the four
      provenance files of voices/fa/haaniye_low at that commit (LICENSE, README,
      SOURCE, ALIASES), recorded verbatim with a computed SHA-256.

  A hash in the manifest is a CONTENT MATCH against what the publisher indexed.
  It is not a security guarantee and not an independent signature.

.PARAMETER OutputPath
  Where to write the manifest. Must not be inside the Primee repository.

.PARAMETER Force
  Overwrite an existing manifest at OutputPath.
#>
[CmdletBinding()]
param(
    [string]$OutputPath = '',
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# ---------------------------------------------------------------- pinned facts
# Reviewed in the development session on 2026-09-02 from PyPI and the sherpa-onnx
# repository. The script re-fetches them and STOPS on any difference.
$SherpaVersion = '1.13.7'
$ExpectedWheels = @(
    @{ Package = 'sherpa-onnx';      FileName = 'sherpa_onnx-1.13.7-cp311-cp311-win_amd64.whl';    Size = 2277232;  Sha256 = '7b3bef2a558f57c403bb7afb994e081cd242e5df3384295b6738b198cb2f8382' },
    @{ Package = 'sherpa-onnx-core'; FileName = 'sherpa_onnx_core-1.13.7-py3-none-win_amd64.whl';  Size = 16526006; Sha256 = 'cbcb78ef8a3bb74acf0b9d0a8715e9b857491be8e2b1e8f589f09ecf3d2f2a0b' }
)
$ExpectedRequires = @('sherpa-onnx-core==1.13.7')
$EngineLicense    = 'Apache-2.0'

$ModelRepo     = 'csukuangfj/vits-mimic3-fa-haaniye_low'
$ModelDirName  = 'vits-mimic3-fa-haaniye_low'
$RequiredModelFiles = @('fa-haaniye_low.onnx', 'fa-haaniye_low.onnx.json', 'tokens.txt')
$RequiredModelDir   = 'espeak-ng-data'

$MycroftRepo   = 'MycroftAI/mimic3-voices'
$MycroftPath   = 'voices/fa/haaniye_low'
$MycroftFiles  = @('LICENSE', 'README.md', 'SOURCE', 'ALIASES')

$ProfileKey    = 'haaniye'
$SchemaName    = 'primee-voice-manifest'
$SchemaVersion = 1

# ---------------------------------------------------------------- network rules
$AllowedHosts = @('pypi.org', 'huggingface.co', 'api.github.com', 'raw.githubusercontent.com')
$MaxTextBytes      = 1048576
$RequestTimeoutSec = 30
$MaxRedirects      = 3
$MaxTreePages      = 50
$ReadChunkSize     = 8192

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Output '  (note: could not raise TLS level for this process; requests may fail)'
}

function Test-AllowedUri {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Uri)
    if ([string]::IsNullOrWhiteSpace($Uri)) { return $false }
    if ($Uri.Length -gt 2000) { return $false }
    foreach ($character in $Uri.ToCharArray()) {
        if ([int][char]$character -lt 32) { return $false }
        if ([int][char]$character -eq 127) { return $false }
    }
    $parsed = $null
    if (-not [System.Uri]::TryCreate($Uri, [System.UriKind]::Absolute, [ref]$parsed)) { return $false }
    if ($null -eq $parsed) { return $false }
    if ($parsed.Scheme -ne 'https') { return $false }
    if ($AllowedHosts -notcontains $parsed.Host) { return $false }
    if (-not [string]::IsNullOrEmpty($parsed.UserInfo)) { return $false }
    if ($parsed.Port -ne 443) { return $false }
    return $true
}

function ConvertTo-AbsoluteUri {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$BaseUri,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Location
    )
    if ([string]::IsNullOrWhiteSpace($Location)) { return $null }
    if ($Location.Length -gt 2000) { return $null }
    foreach ($character in $Location.ToCharArray()) {
        if ([int][char]$character -lt 32) { return $null }
        if ([int][char]$character -eq 127) { return $null }
    }
    $baseParsed = $null
    if (-not [System.Uri]::TryCreate($BaseUri, [System.UriKind]::Absolute, [ref]$baseParsed)) { return $null }
    $absolute = $null
    if ([System.Uri]::TryCreate($Location, [System.UriKind]::Absolute, [ref]$absolute)) { return $absolute.AbsoluteUri }
    $relative = $null
    if (-not [System.Uri]::TryCreate($Location, [System.UriKind]::Relative, [ref]$relative)) { return $null }
    $combined = $null
    if (-not [System.Uri]::TryCreate($baseParsed, $relative, [ref]$combined)) { return $null }
    if ($null -eq $combined) { return $null }
    return $combined.AbsoluteUri
}

function Get-BoundedText {
    <# GET a small text/JSON body with a real streamed byte limit. Never follows a redirect off the allowlist. #>
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$RedirectsLeft = $MaxRedirects,
        [string]$Accept = 'application/json, text/plain, */*'
    )
    if (-not (Test-AllowedUri -Uri $Uri)) { throw ('Refused: URL is not on the allowlist: ' + $Uri) }
    if ($RedirectsLeft -lt 0) { throw 'Refused: too many redirects.' }

    $request = $null; $response = $null; $stream = $null; $memory = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Method = 'GET'
        $request.AllowAutoRedirect = $false
        $request.Timeout = $RequestTimeoutSec * 1000
        $request.ReadWriteTimeout = $RequestTimeoutSec * 1000
        $request.Accept = $Accept
        $request.UserAgent = 'primee-voice-manifest/1'
        try { $response = $request.GetResponse() } catch [System.Net.WebException] {
            $response = $null
            try { $response = $_.Exception.Response } catch { $response = $null }
            if ($null -eq $response) { throw 'Request failed (network or TLS error).' }
        }
        $statusCode = [int]$response.StatusCode
        if ($statusCode -ge 300 -and $statusCode -lt 400) {
            $location = $null
            try { $location = $response.Headers['Location'] } catch { $location = $null }
            $target = ConvertTo-AbsoluteUri -BaseUri $Uri -Location $location
            if ($null -eq $target) { throw 'Refused: redirect destination could not be resolved.' }
            if (-not (Test-AllowedUri -Uri $target)) { throw ('Refused: redirect pointed off the allowlist and was not followed.') }
            try { $response.Close() } catch { }
            $response = $null
            return Get-BoundedText -Uri $target -RedirectsLeft ($RedirectsLeft - 1) -Accept $Accept
        }
        if ($statusCode -lt 200 -or $statusCode -ge 300) { throw ('HTTP status ' + $statusCode + ' for ' + $Uri) }

        $declared = -1
        try { $declared = [int64]$response.ContentLength } catch { $declared = -1 }
        if ($declared -gt $MaxTextBytes) { throw ('Refused: response larger than ' + $MaxTextBytes + ' bytes.') }

        $stream = $response.GetResponseStream()
        $memory = New-Object System.IO.MemoryStream
        $buffer = New-Object byte[] $ReadChunkSize
        $total  = 0
        while ($true) {
            $read = $stream.Read($buffer, 0, $ReadChunkSize)
            if ($read -le 0) { break }
            $memory.Write($buffer, 0, $read)
            $total = $total + $read
            if ($total -gt $MaxTextBytes) { throw ('Refused: response exceeded ' + $MaxTextBytes + ' bytes.') }
        }
        $bytes = $memory.ToArray()
        $encoding = New-Object System.Text.UTF8Encoding($false, $true)
        $result = New-Object psobject
        Add-Member -InputObject $result -MemberType NoteProperty -Name 'Text'  -Value ($encoding.GetString($bytes))
        Add-Member -InputObject $result -MemberType NoteProperty -Name 'Bytes' -Value $bytes
        Add-Member -InputObject $result -MemberType NoteProperty -Name 'Link'  -Value $null
        try { $result.Link = [string]$response.Headers['Link'] } catch { $result.Link = $null }
        return $result
    } finally {
        if ($null -ne $stream)   { try { $stream.Close() }   catch { } }
        if ($null -ne $memory)   { try { $memory.Dispose() } catch { } }
        if ($null -ne $response) { try { $response.Close() } catch { } }
    }
}

function Get-Json {
    param([Parameter(Mandatory = $true)][string]$Uri)
    $fetched = Get-BoundedText -Uri $Uri
    try { return ($fetched.Text | ConvertFrom-Json) } catch { throw ('Response from ' + $Uri + ' was not valid JSON.') }
}

function Test-CommitSha {
    param([AllowEmptyString()][string]$Sha)
    if ([string]::IsNullOrWhiteSpace($Sha)) { return $false }
    return ($Sha -match '^[0-9a-fA-F]{40}$')
}

function Test-Hex {
    param([AllowEmptyString()][string]$Value, [int]$Length)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value.Length -ne $Length) { return $false }
    return ($Value -match '^[0-9a-fA-F]+$')
}

function Test-RepoPath {
    <#
      Safe relative path inside the model repository. espeak-ng-data legitimately
      contains "voices/!v/" and a file called "Mr serious", so "!" and inner
      spaces are allowed; traversal, absolute paths, backslashes, control
      characters, Windows-illegal characters and reserved device names are not.
    #>
    param([AllowEmptyString()][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if ($Path.Length -gt 400) { return $false }
    if ($Path -notmatch '^[A-Za-z0-9 ._/!+@=,~-]+$') { return $false }
    if ($Path.StartsWith('/') -or $Path.Contains('//')) { return $false }
    $depth = 0
    foreach ($segment in ($Path -split '/')) {
        $depth = $depth + 1
        if ([string]::IsNullOrEmpty($segment)) { return $false }
        if ($segment -eq '.' -or $segment -eq '..') { return $false }
        if ($segment.StartsWith(' ') -or $segment.EndsWith(' ') -or $segment.EndsWith('.')) { return $false }
        if ($segment.Length -gt 100) { return $false }
        $stem = ($segment -split '\.')[0].ToUpper()
        if ($stem -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { return $false }
    }
    if ($depth -gt 8) { return $false }
    return $true
}

function ConvertTo-EncodedRepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $segments = @()
    foreach ($segment in ($Path -split '/')) { $segments = $segments + [System.Uri]::EscapeDataString($segment) }
    return ($segments -join '/')
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($Bytes)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally { $sha.Dispose() }
}

function Has-Property {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $false }
    return ($Object.PSObject.Properties.Name -contains $Name)
}

function Stop-WithReason {
    param([string]$Reason)
    Write-Output ''
    Write-Output (' STOPPED: ' + $Reason)
    Write-Output ' No manifest was written. Nothing was downloaded or installed.'
    Write-Output '====================================================================='
    exit 1
}

# ---------------------------------------------------------------- output path
$repoRoot = $null
try { $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } catch { $repoRoot = $null }

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $base = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($base)) { Stop-WithReason 'LOCALAPPDATA is not set; pass -OutputPath explicitly.' }
    $OutputPath = Join-Path (Join-Path $base 'Primee') (Join-Path 'manifests' 'haaniye-sherpa.manifest.json')
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ($null -ne $repoRoot -and $OutputPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Stop-WithReason 'OutputPath is inside the Primee repository. Manifests are local files and must live outside Git.'
}
if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    Stop-WithReason ('A manifest already exists at ' + $OutputPath + '. Pass -Force to overwrite it.')
}

Write-Output '====================================================================='
Write-Output ' PRIMEE - VOICE MANIFEST (public metadata only; downloads nothing)'
Write-Output (' Generated : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'))
Write-Output (' Output    : ' + $OutputPath)
Write-Output '====================================================================='
Write-Output ' This step WRITES ONE MANIFEST FILE. It does NOT download or install'
Write-Output ' any model, wheel, archive or executable.'
Write-Output ''

$components = @()

# ---------------------------------------------------------------- 1. runtime
Write-Output ('-- Runtime: sherpa-onnx on PyPI, exact version ' + $SherpaVersion)
foreach ($expected in $ExpectedWheels) {
    $url  = 'https://pypi.org/pypi/' + $expected.Package + '/' + $SherpaVersion + '/json'
    $json = $null
    try { $json = Get-Json -Uri $url } catch { Stop-WithReason ('PyPI metadata for ' + $expected.Package + ' could not be read: ' + $_.Exception.Message) }

    if (-not (Has-Property $json 'urls')) { Stop-WithReason ('PyPI response for ' + $expected.Package + ' has no file list.') }
    $wheel = $null
    foreach ($file in $json.urls) {
        if ((Has-Property $file 'filename') -and $file.filename -eq $expected.FileName) { $wheel = $file }
    }
    if ($null -eq $wheel) { Stop-WithReason ('PyPI no longer lists ' + $expected.FileName + '.') }

    $size = [int64]$wheel.size
    $sha  = [string]$wheel.digests.sha256
    $yanked = $false
    if (Has-Property $wheel 'yanked') { $yanked = [bool]$wheel.yanked }
    if ($yanked) { Stop-WithReason ($expected.FileName + ' has been yanked on PyPI.') }
    if ($size -ne $expected.Size) { Stop-WithReason ($expected.FileName + ': size on PyPI is ' + $size + ', reviewed value was ' + $expected.Size + '.') }
    if (-not [string]::Equals($sha, $expected.Sha256, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-WithReason ($expected.FileName + ': SHA-256 on PyPI differs from the reviewed value.')
    }
    if (-not ([string]$wheel.url).StartsWith('https://files.pythonhosted.org/', [System.StringComparison]::Ordinal)) {
        Stop-WithReason ($expected.FileName + ': download URL is not on files.pythonhosted.org.')
    }

    $requires = @()
    if ((Has-Property $json 'info') -and (Has-Property $json.info 'requires_dist') -and $null -ne $json.info.requires_dist) {
        foreach ($item in $json.info.requires_dist) { $requires = $requires + [string]$item }
    }
    if ($expected.Package -eq 'sherpa-onnx') {
        $joined = ($requires -join ',')
        if ($joined -ne ($ExpectedRequires -join ',')) {
            Stop-WithReason ('sherpa-onnx dependencies changed on PyPI: ' + $joined + ' (reviewed: ' + ($ExpectedRequires -join ',') + ').')
        }
    } elseif ($requires.Count -gt 0) {
        Stop-WithReason ('sherpa-onnx-core now declares dependencies: ' + ($requires -join ','))
    }

    $licenseText = ''
    if ((Has-Property $json 'info') -and (Has-Property $json.info 'license')) { $licenseText = [string]$json.info.license }

    $component = [ordered]@{
        component    = 'runtime'
        name         = $expected.Package
        version      = $SherpaVersion
        revision     = $null
        source       = $url
        license      = $EngineLicense
        license_note = ('PyPI license field: "' + $licenseText + '"; repository LICENSE is Apache License 2.0. Bundled libraries (onnxruntime, espeak-ng data) carry their own licences and are not assessed here.')
        requires     = $requires
        files        = @(
            [ordered]@{
                path        = $expected.FileName
                url         = [string]$wheel.url
                size        = $size
                hash_type   = 'publisher-sha256'
                hash        = $sha.ToLower()
                hash_source = 'PyPI JSON API digests.sha256'
            }
        )
    }
    $components = $components + $component
    Write-Output ('   OK ' + $expected.FileName + '  ' + $size + ' bytes  sha256 matches reviewed value')
}

# ---------------------------------------------------------------- 2. model
Write-Output ''
Write-Output ('-- Model: ' + $ModelRepo + ' on Hugging Face')
$modelApi = 'https://huggingface.co/api/models/' + $ModelRepo
$model = $null
try { $model = Get-Json -Uri $modelApi } catch { Stop-WithReason ('Hugging Face model metadata could not be read: ' + $_.Exception.Message) }

$revision = $null
if (Has-Property $model 'sha') { $revision = [string]$model.sha }
if (-not (Test-CommitSha -Sha $revision)) { Stop-WithReason 'Hugging Face did not return a 40-character commit id for the model.' }
$revision = $revision.ToLower()

$hfLicense = $null
if ((Has-Property $model 'cardData') -and $null -ne $model.cardData -and (Has-Property $model.cardData 'license')) {
    $candidate = [string]$model.cardData.license
    if (-not [string]::IsNullOrWhiteSpace($candidate)) { $hfLicense = $candidate }
}
$hfLicenseNote = 'The converted repository declares NO licence metadata. Primee assigns none. The original Mycroft voice licence (CC0) and provenance files are recorded in the provenance component and installed next to the model.'
if ($null -ne $hfLicense) { $hfLicenseNote = ('The converted repository declares licence metadata "' + $hfLicense + '". Recorded verbatim; not interpreted.') }
Write-Output ('   revision : ' + $revision)
Write-Output ('   licence  : ' + $(if ($null -eq $hfLicense) { 'not declared' } else { $hfLicense }))

$treeUrl = 'https://huggingface.co/api/models/' + $ModelRepo + '/tree/' + $revision + '?recursive=true'
$entries = @()
$pages = 0
while ($true) {
    if ($pages -ge $MaxTreePages) { Stop-WithReason 'Model file listing has too many pages.' }
    $fetched = $null
    try { $fetched = Get-BoundedText -Uri $treeUrl } catch { Stop-WithReason ('Model file listing could not be read: ' + $_.Exception.Message) }
    $pages = $pages + 1
    $batch = $null
    try { $batch = $fetched.Text | ConvertFrom-Json } catch { Stop-WithReason 'Model file listing was not valid JSON.' }
    if ($null -ne $batch) { $entries = $entries + $batch }
    if ([string]::IsNullOrWhiteSpace($fetched.Link)) { break }
    $next = $null
    foreach ($part in ($fetched.Link -split ',')) {
        if ($part -match '<([^>]+)>\s*;\s*rel\s*=\s*"?next"?') { $next = $Matches[1]; break }
    }
    if ([string]::IsNullOrWhiteSpace($next)) { break }
    if (-not (Test-AllowedUri -Uri $next)) { Stop-WithReason 'Model file listing pagination pointed off the allowlist.' }
    $treeUrl = $next
}

$modelFiles = @()
$totalBytes = [int64]0
foreach ($entry in $entries) {
    if (-not (Has-Property $entry 'type') -or [string]$entry.type -ne 'file') { continue }
    $path = [string]$entry.path
    if (-not (Test-RepoPath -Path $path)) { Stop-WithReason ('Model repository contains an unsafe path: ' + $path) }
    $size = [int64]$entry.size
    $hashType = 'none'; $hash = ''; $hashSource = ''
    if ((Has-Property $entry 'lfs') -and $null -ne $entry.lfs) {
        $lfsHash = ''
        foreach ($field in @('sha256', 'oid')) {
            if (Has-Property $entry.lfs $field) {
                $value = ([string]$entry.lfs.$field) -replace '^sha256:', ''
                if (Test-Hex -Value $value -Length 64) { $lfsHash = $value.ToLower(); break }
            }
        }
        if ($lfsHash -eq '') { Stop-WithReason ('LFS file without a SHA-256 in the listing: ' + $path) }
        $hashType = 'publisher-sha256'; $hash = $lfsHash; $hashSource = 'Hugging Face LFS metadata (lfs.sha256)'
        if ((Has-Property $entry.lfs 'size') -and [int64]$entry.lfs.size -ne $size) { Stop-WithReason ('LFS size disagrees with listing size for ' + $path) }
    } elseif (Has-Property $entry 'oid') {
        $oid = [string]$entry.oid
        if (-not (Test-Hex -Value $oid -Length 40)) { Stop-WithReason ('Non-LFS file without a 40-character blob id: ' + $path) }
        $hashType = 'publisher-git-blob-sha1'; $hash = $oid.ToLower(); $hashSource = 'Hugging Face tree oid (git blob sha1 = sha1("blob <size>\0" + content))'
    } else {
        Stop-WithReason ('File without any publisher hash in the listing: ' + $path)
    }
    $totalBytes = $totalBytes + $size
    $modelFiles = $modelFiles + [ordered]@{
        path        = $path
        url         = 'https://huggingface.co/' + $ModelRepo + '/resolve/' + $revision + '/' + (ConvertTo-EncodedRepoPath -Path $path)
        size        = $size
        hash_type   = $hashType
        hash        = $hash
        hash_source = $hashSource
    }
}
if ($modelFiles.Count -eq 0) { Stop-WithReason 'The model repository listing returned no files.' }

$paths = @($modelFiles | ForEach-Object { $_.path })
foreach ($required in $RequiredModelFiles) {
    if ($paths -notcontains $required) { Stop-WithReason ('Required model file is missing from the repository: ' + $required) }
}
$dataFiles = @($paths | Where-Object { $_.StartsWith($RequiredModelDir + '/') })
if ($dataFiles.Count -eq 0) { Stop-WithReason ('Required directory is missing from the repository: ' + $RequiredModelDir) }

$components = $components + [ordered]@{
    component    = 'model'
    name         = $ModelDirName
    version      = $null
    revision     = $revision
    source       = $modelApi
    license      = $hfLicense
    license_note = $hfLicenseNote
    repository   = $ModelRepo
    requires     = @()
    files        = $modelFiles
    total_bytes  = $totalBytes
    required_files = $RequiredModelFiles
    required_dir   = $RequiredModelDir
}
Write-Output ('   files    : ' + $modelFiles.Count + ' (' + $dataFiles.Count + ' under ' + $RequiredModelDir + '/), ' + $totalBytes + ' bytes total')
Write-Output ('   hashes   : ' + @($modelFiles | Where-Object { $_.hash_type -eq 'publisher-sha256' }).Count + ' publisher-sha256 (LFS), ' + @($modelFiles | Where-Object { $_.hash_type -eq 'publisher-git-blob-sha1' }).Count + ' publisher-git-blob-sha1')

# ---------------------------------------------------------------- 3. provenance
Write-Output ''
Write-Output ('-- Provenance: ' + $MycroftRepo + '/' + $MycroftPath)
$commitApi = 'https://api.github.com/repos/' + $MycroftRepo + '/commits/master'
$commit = $null
try { $commit = Get-Json -Uri $commitApi } catch { Stop-WithReason ('GitHub commit metadata could not be read: ' + $_.Exception.Message) }
$mycroftSha = $null
if (Has-Property $commit 'sha') { $mycroftSha = [string]$commit.sha }
if (-not (Test-CommitSha -Sha $mycroftSha)) { Stop-WithReason 'GitHub did not return a 40-character commit id for mimic3-voices.' }
$mycroftSha = $mycroftSha.ToLower()

$provFiles = @()
$provText  = [ordered]@{}
foreach ($name in $MycroftFiles) {
    $url = 'https://raw.githubusercontent.com/' + $MycroftRepo + '/' + $mycroftSha + '/' + $MycroftPath + '/' + $name
    $fetched = $null
    try { $fetched = Get-BoundedText -Uri $url -Accept 'text/plain' } catch { Stop-WithReason ('Provenance file ' + $name + ' could not be read: ' + $_.Exception.Message) }
    $provText[$name] = $fetched.Text
    $provFiles = $provFiles + [ordered]@{
        path        = $name
        url         = $url
        size        = $fetched.Bytes.Length
        hash_type   = 'computed-sha256-at-manifest-time'
        hash        = (Get-Sha256Hex -Bytes $fetched.Bytes)
        hash_source = 'computed by this script from the fetched bytes; GitHub publishes no per-file hash'
    }
}
$licenseLine = ($provText['LICENSE']).Trim()
$sourceLine  = ($provText['SOURCE']).Trim()
Write-Output ('   commit   : ' + $mycroftSha)
Write-Output ('   LICENSE  : "' + $licenseLine + '"')
Write-Output ('   SOURCE   : "' + $sourceLine + '"')
if ($licenseLine -ne 'CC-0') { Write-Output '   WARNING  : LICENSE text differs from the reviewed value "CC-0". Read it before approving.' }
if ($sourceLine -ne 'TBD')   { Write-Output '   NOTE     : SOURCE is no longer "TBD"; provenance may have improved. Read it.' }

$components = $components + [ordered]@{
    component    = 'provenance'
    name         = 'mycroft-haaniye_low'
    version      = $null
    revision     = $mycroftSha
    source       = ('https://github.com/' + $MycroftRepo + '/tree/' + $mycroftSha + '/' + $MycroftPath)
    license      = $(if ($licenseLine -eq 'CC-0') { 'CC0' } else { $null })
    license_note = ('Original voice LICENSE file content: "' + $licenseLine + '". README describes a public domain dataset. SOURCE file content: "' + $sourceLine + '" - exact dataset provenance is incomplete while it says TBD.')
    repository   = $MycroftRepo
    path         = $MycroftPath
    requires     = @()
    files        = $provFiles
    documents    = $provText
    speaker_gender = 'not stated in the official documentation; confirm by listening'
    redistribution = 'not assessed; no claim of redistribution or commercial clearance'
    approved_use   = 'private local benchmark only'
}

# ---------------------------------------------------------------- write
$manifest = [ordered]@{
    schema         = $SchemaName
    schema_version = $SchemaVersion
    profile        = $ProfileKey
    generated_at   = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
    generated_by   = 'tools/voice/Get-PrimeeVoiceManifest.ps1'
    statement      = 'This manifest lists public metadata only. No model, wheel, archive or executable was downloaded to create it. Nothing was installed.'
    hash_note      = 'A hash is a content match against what the publisher indexed: publisher-sha256 (PyPI digests, Hugging Face LFS) or publisher-git-blob-sha1 (Hugging Face tree oid). It is not a security guarantee and not an independent signature.'
    components     = $components
}

$directory = [System.IO.Path]::GetDirectoryName($OutputPath)
if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }
$jsonText = $manifest | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutputPath, $jsonText, $utf8NoBom)

Write-Output ''
Write-Output '====================================================================='
Write-Output (' Manifest written : ' + $OutputPath)
Write-Output (' Manifest SHA-256 : ' + (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLower())
Write-Output (' Components       : ' + $components.Count + ' (2 runtime wheels, 1 model, 1 provenance record)')
Write-Output ''
Write-Output ' Read the manifest. Installation happens ONLY from this exact file, only'
Write-Output ' with tools\voice\Install-PrimeeVoice.ps1 -Approve, and only after you'
Write-Output ' approved it. Changing the manifest means approving again.'
Write-Output ''
Write-Output ' What this run did and did not do:'
Write-Output '   - Wrote exactly one file: the manifest above.'
Write-Output '   - Downloaded NO model, wheel, archive or executable.'
Write-Output '   - Installed nothing; changed no PATH, policy, task or startup entry.'
Write-Output '   - Contacted only pypi.org, huggingface.co, api.github.com and'
Write-Output '     raw.githubusercontent.com, for metadata and four small text files.'
Write-Output '====================================================================='
