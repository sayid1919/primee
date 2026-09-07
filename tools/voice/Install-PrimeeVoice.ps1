<#
.SYNOPSIS
  Install the Primee voice runtime and the Haaniye model from ONE approved manifest.

.DESCRIPTION
  Windows PowerShell 5.1. No Administrator rights. Everything it creates lives
  outside Git: an isolated Python environment (RuntimePath), a model folder
  (ModelsPath) and one git-ignored configuration file (config\voice.local.toml).

  Modes (exactly one):
    -DryRun    print every planned action, URL, size and hash. Change nothing.
    -Approve   perform the installation described by the manifest, and nothing
               else. Every download is verified against the manifest hash
               BEFORE it is used; any mismatch stops the run.
    -Rollback  remove only what a previous -Approve run recorded as created.
               Pre-existing folders, files and settings are never touched.

  Rules enforced here:
    * pinned versions from the manifest only; pip never resolves "latest";
    * pip runs with --no-index --no-deps --require-hashes on local wheel files,
      so no dependency, no pip upgrade and no undeclared package is installed;
    * only https URLs on files.pythonhosted.org and huggingface.co;
    * ExecutionPolicy, PATH, scheduled tasks and startup entries are untouched;
    * models never enter the repository; the destination is refused if inside it;
    * third-party LICENSE/README/SOURCE provenance files are copied next to the
      model, so the licence record travels with the installed bytes.

.NOTES
  A matching hash means the bytes match what the publisher indexed. It is not a
  security guarantee and not an independent signature.
#>
[CmdletBinding()]
param(
    [string]$ManifestPath = '',
    [string]$ModelsPath = '',
    [string]$RuntimePath = '',
    [string]$PythonExe = '',
    [switch]$DryRun,
    [switch]$Approve,
    [switch]$Rollback
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$SchemaName    = 'primee-voice-manifest'
$SchemaVersion = 1
$MarkerName    = '.primee-installed.json'
$ManifestCopy  = 'primee-manifest.json'
$ProvenanceDir = 'PROVENANCE'
$DownloadHosts = @('files.pythonhosted.org', 'huggingface.co', 'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.huggingface.co', 'cdn-lfs-eu-1.huggingface.co', 'cas-bridge.xethub.hf.co')
$RequiredPythonMajorMinor = '3.11'
$RequestTimeoutSec = 60
$MaxRedirects = 5
$MaxFileBytes = 2147483648

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { Write-Output '  (note: could not raise TLS level for this process)' }

function Stop-WithReason {
    param([string]$Reason)
    Write-Output ''
    Write-Output (' STOPPED: ' + $Reason)
    Write-Output '====================================================================='
    exit 1
}

function Has-Property { param($Object, [string]$Name); if ($null -eq $Object) { return $false }; return ($Object.PSObject.Properties.Name -contains $Name) }

function Test-DownloadUri {
    param([AllowEmptyString()][string]$Uri)
    if ([string]::IsNullOrWhiteSpace($Uri) -or $Uri.Length -gt 2000) { return $false }
    foreach ($character in $Uri.ToCharArray()) { if ([int][char]$character -lt 33) { return $false } }
    $parsed = $null
    if (-not [System.Uri]::TryCreate($Uri, [System.UriKind]::Absolute, [ref]$parsed)) { return $false }
    if ($parsed.Scheme -ne 'https' -or $parsed.Port -ne 443) { return $false }
    if (-not [string]::IsNullOrEmpty($parsed.UserInfo)) { return $false }
    return ($DownloadHosts -contains $parsed.Host)
}

function ConvertTo-AbsoluteUri {
    param([AllowEmptyString()][string]$BaseUri, [AllowEmptyString()][string]$Location)
    if ([string]::IsNullOrWhiteSpace($Location) -or $Location.Length -gt 2000) { return $null }
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

function Get-FileSha256 { param([string]$Path); return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() }

function Get-GitBlobSha1 {
    <# sha1("blob <size>\0" + content), the object id Hugging Face publishes for non-LFS files. #>
    param([string]$Path)
    $bytes  = [System.IO.File]::ReadAllBytes($Path)
    $header = [System.Text.Encoding]::ASCII.GetBytes('blob ' + $bytes.Length + [char]0)
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try {
        $sha.TransformBlock($header, 0, $header.Length, $null, 0) | Out-Null
        $sha.TransformFinalBlock($bytes, 0, $bytes.Length) | Out-Null
        return (($sha.Hash | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally { $sha.Dispose() }
}

function Test-FileAgainstEntry {
    param([string]$Path, $Entry)
    $actualSize = (Get-Item -LiteralPath $Path).Length
    if ($actualSize -ne [int64]$Entry.size) { return ('size ' + $actualSize + ' differs from manifest ' + $Entry.size) }
    switch ([string]$Entry.hash_type) {
        'publisher-sha256'                 { $actual = Get-FileSha256 -Path $Path }
        'computed-sha256-at-manifest-time' { $actual = Get-FileSha256 -Path $Path }
        'publisher-git-blob-sha1'          { $actual = Get-GitBlobSha1 -Path $Path }
        default { return ('no verifiable hash type (' + $Entry.hash_type + ')') }
    }
    if (-not [string]::Equals($actual, ([string]$Entry.hash).ToLower(), [System.StringComparison]::Ordinal)) {
        return ('HASH MISMATCH: computed ' + $actual + ' vs manifest ' + $Entry.hash)
    }
    return $null
}

function Save-VerifiedDownload {
    <# Download to a temp name, verify against the manifest, then move into place. Any failure removes the temp file. #>
    param([string]$Uri, [string]$Destination, $Entry, [int]$RedirectsLeft = $MaxRedirects)
    if (-not (Test-DownloadUri -Uri $Uri)) { throw ('Refused: download URL is not on the allowlist: ' + $Uri) }
    if ($RedirectsLeft -lt 0) { throw 'Refused: too many redirects.' }
    if ([int64]$Entry.size -gt $MaxFileBytes) { throw 'Refused: manifest size exceeds the 2 GiB limit.' }

    $temp = $Destination + '.part'
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    $request = $null; $response = $null; $inStream = $null; $outStream = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Method = 'GET'
        $request.AllowAutoRedirect = $false
        $request.Timeout = $RequestTimeoutSec * 1000
        $request.ReadWriteTimeout = $RequestTimeoutSec * 1000
        $request.UserAgent = 'primee-voice-install/1'
        try { $response = $request.GetResponse() } catch [System.Net.WebException] {
            $response = $null
            try { $response = $_.Exception.Response } catch { $response = $null }
            if ($null -eq $response) { throw 'Request failed (network or TLS error).' }
        }
        $status = [int]$response.StatusCode
        if ($status -ge 300 -and $status -lt 400) {
            $location = $null
            try { $location = $response.Headers['Location'] } catch { $location = $null }
            $target = ConvertTo-AbsoluteUri -BaseUri $Uri -Location $location
            try { $response.Close() } catch { }
            $response = $null
            if ($null -eq $target) { throw 'Refused: redirect destination could not be resolved.' }
            if (-not (Test-DownloadUri -Uri $target)) {
                $targetHost = 'unparsable'
                $parsedTarget = $null
                if ([System.Uri]::TryCreate($target, [System.UriKind]::Absolute, [ref]$parsedTarget)) { $targetHost = $parsedTarget.Host }
                throw ('Refused: redirect to a host outside the download allowlist (' + $targetHost + '). Add it deliberately if you trust it.')
            }
            return Save-VerifiedDownload -Uri $target -Destination $Destination -Entry $Entry -RedirectsLeft ($RedirectsLeft - 1)
        }
        if ($status -lt 200 -or $status -ge 300) { throw ('HTTP status ' + $status) }

        $inStream  = $response.GetResponseStream()
        $outStream = [System.IO.File]::Open($temp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        $buffer = New-Object byte[] 1048576
        $total  = [int64]0
        $limit  = [int64]$Entry.size
        while ($true) {
            $read = $inStream.Read($buffer, 0, $buffer.Length)
            if ($read -le 0) { break }
            $total = $total + $read
            if ($total -gt $limit) { throw ('Refused: server sent more bytes than the manifest size ' + $limit) }
            $outStream.Write($buffer, 0, $read)
        }
        $outStream.Close(); $outStream = $null

        $problem = Test-FileAgainstEntry -Path $temp -Entry $Entry
        if ($null -ne $problem) { throw ('Verification failed for ' + $Entry.path + ': ' + $problem) }
        Move-Item -LiteralPath $temp -Destination $Destination
    } catch {
        if (Test-Path -LiteralPath $temp) { try { Remove-Item -LiteralPath $temp -Force } catch { } }
        throw
    } finally {
        if ($null -ne $outStream) { try { $outStream.Close() } catch { } }
        if ($null -ne $inStream)  { try { $inStream.Close() }  catch { } }
        if ($null -ne $response)  { try { $response.Close() }  catch { } }
    }
}

function Invoke-Python {
    <#
      Run the given interpreter with an ARGUMENT ARRAY through PowerShell's call
      operator. PowerShell quotes each element itself; no command string is ever
      built and no command interpreter or expression evaluator is involved.
      (PowerShell 5.1 on .NET Framework lacks the newer argument-list API, so
      this is the safe idiom.)
    #>
    param([string]$Interpreter, [string[]]$Arguments)
    foreach ($argument in $Arguments) {
        if ($argument -match '[\r\n]') { throw 'Refused: a process argument contains a line break.' }
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = @()
    try {
        $output = & $Interpreter @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $stdout = @(); $stderr = @()
    foreach ($line in @($output)) {
        if ($line -is [System.Management.Automation.ErrorRecord]) { $stderr += [string]$line } else { $stdout += [string]$line }
    }
    $result = New-Object psobject
    Add-Member -InputObject $result -MemberType NoteProperty -Name 'ExitCode' -Value $exitCode
    Add-Member -InputObject $result -MemberType NoteProperty -Name 'StdOut'   -Value ($stdout -join "`n")
    Add-Member -InputObject $result -MemberType NoteProperty -Name 'StdErr'   -Value ($stderr -join "`n")
    return $result
}

# Child-process environment for pip and python: scoped to THIS PowerShell process
# and its children only. Nothing is persisted; nothing touches PATH.
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'
$env:PIP_REQUIRE_VIRTUALENV = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

# ---------------------------------------------------------------- mode
$modes = @(@($DryRun.IsPresent, $Approve.IsPresent, $Rollback.IsPresent) | Where-Object { $_ })
if ($modes.Count -ne 1) { Stop-WithReason 'Pass exactly one of -DryRun, -Approve or -Rollback.' }
$mode = $(if ($Rollback) { 'rollback' } elseif ($Approve) { 'approve' } else { 'dry-run' })

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$localAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($ModelsPath)) {
    $fromEnv = $env:PRIMEE_MODELS_PATH
    if (-not [string]::IsNullOrWhiteSpace($fromEnv)) { $ModelsPath = $fromEnv }
    elseif (-not [string]::IsNullOrWhiteSpace($localAppData)) { $ModelsPath = Join-Path (Join-Path $localAppData 'Primee') 'models' }
    else { Stop-WithReason 'Neither -ModelsPath, PRIMEE_MODELS_PATH nor LOCALAPPDATA is available.' }
}
if ([string]::IsNullOrWhiteSpace($RuntimePath)) { $RuntimePath = Join-Path $repoRoot '.venv-voice' }
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    if ([string]::IsNullOrWhiteSpace($localAppData)) { Stop-WithReason 'Pass -ManifestPath explicitly.' }
    $ManifestPath = Join-Path (Join-Path $localAppData 'Primee') (Join-Path 'manifests' 'haaniye-sherpa.manifest.json')
}
$ModelsPath   = [System.IO.Path]::GetFullPath($ModelsPath)
$RuntimePath  = [System.IO.Path]::GetFullPath($RuntimePath)
$ManifestPath = [System.IO.Path]::GetFullPath($ManifestPath)
$ConfigPath   = Join-Path (Join-Path $repoRoot 'config') 'voice.local.toml'

if ($ModelsPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) { Stop-WithReason 'ModelsPath is inside the repository. Models must never enter Git.' }
if ($RuntimePath -ne (Join-Path $repoRoot '.venv-voice') -and $RuntimePath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Stop-WithReason 'RuntimePath inside the repository must be exactly <repo>\.venv-voice, which .gitignore excludes.'
}

Write-Output '====================================================================='
Write-Output (' PRIMEE - VOICE INSTALL  mode: ' + $mode.ToUpper())
Write-Output (' Repository : ' + $repoRoot)
Write-Output (' Manifest   : ' + $ManifestPath)
Write-Output (' Models     : ' + $ModelsPath)
Write-Output (' Runtime    : ' + $RuntimePath)
Write-Output (' Config     : ' + $ConfigPath)
Write-Output '====================================================================='

# ---------------------------------------------------------------- rollback
if ($mode -eq 'rollback') {
    $markers = @()
    foreach ($candidate in @((Join-Path $RuntimePath $MarkerName), (Join-Path $ModelsPath $MarkerName))) {
        if (Test-Path -LiteralPath $candidate) { $markers = $markers + $candidate }
    }
    if ($markers.Count -eq 0) { Stop-WithReason ('No ' + $MarkerName + ' marker found under the runtime or models path. Nothing recorded as installed by this tool; nothing removed.') }
    $removed = 0
    foreach ($markerPath in $markers) {
        $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        if (-not (Has-Property $marker 'created')) { Write-Output (' skipping malformed marker ' + $markerPath); continue }
        # Longest paths first so files go before their folders.
        $created = @($marker.created | Sort-Object -Property { $_.Length } -Descending)
        foreach ($path in $created) {
            $full = [System.IO.Path]::GetFullPath([string]$path)
            $insideModels  = $full.StartsWith($ModelsPath,  [System.StringComparison]::OrdinalIgnoreCase)
            $insideRuntime = $full.StartsWith($RuntimePath, [System.StringComparison]::OrdinalIgnoreCase)
            $isConfig      = [string]::Equals($full, $ConfigPath, [System.StringComparison]::OrdinalIgnoreCase)
            if (-not ($insideModels -or $insideRuntime -or $isConfig)) { Write-Output (' refusing to remove a path outside the install roots: ' + $full); continue }
            if (-not (Test-Path -LiteralPath $full)) { continue }
            Remove-Item -LiteralPath $full -Recurse -Force
            $removed = $removed + 1
            Write-Output (' removed ' + $full)
        }
        Remove-Item -LiteralPath $markerPath -Force
        Write-Output (' removed ' + $markerPath)
    }
    Write-Output ''
    Write-Output (' Rollback complete: ' + $removed + ' recorded item(s) removed. Pre-existing files were not touched.')
    Write-Output ' Git was not used. Your repository, Vault and other settings are unchanged.'
    Write-Output '====================================================================='
    exit 0
}

# ---------------------------------------------------------------- manifest
if (-not (Test-Path -LiteralPath $ManifestPath)) { Stop-WithReason 'Manifest not found. Run Get-PrimeeVoiceManifest.ps1 first and review its output.' }
$manifestHash = Get-FileSha256 -Path $ManifestPath
$manifest = $null
try { $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Stop-WithReason 'Manifest is not valid JSON.' }
if ([string]$manifest.schema -ne $SchemaName -or [int]$manifest.schema_version -ne $SchemaVersion) { Stop-WithReason 'Manifest schema is not recognised.' }
if ([string]$manifest.profile -ne 'haaniye') { Stop-WithReason ('Manifest profile is ' + $manifest.profile + '; this installer handles haaniye only.') }

$runtimes = @($manifest.components | Where-Object { $_.component -eq 'runtime' })
$models   = @($manifest.components | Where-Object { $_.component -eq 'model' })
$provs    = @($manifest.components | Where-Object { $_.component -eq 'provenance' })
if ($runtimes.Count -lt 1 -or $models.Count -ne 1) { Stop-WithReason 'Manifest must contain runtime components and exactly one model.' }
$model = $models[0]
if (([string]$model.revision) -notmatch '^[0-9a-f]{40}$') { Stop-WithReason 'Model revision in the manifest is not a full commit id.' }
foreach ($file in @($model.files)) {
    if ([string]$file.hash_type -eq 'none') { Stop-WithReason ('Model file ' + $file.path + ' carries no verifiable hash; refusing to install.') }
    if (([string]$file.url) -notmatch ('^https://huggingface\.co/.+/resolve/' + [string]$model.revision + '/')) { Stop-WithReason ('Model file ' + $file.path + ' URL is not pinned to the manifest revision.') }
}
foreach ($runtime in $runtimes) {
    foreach ($file in @($runtime.files)) {
        if ([string]$file.hash_type -ne 'publisher-sha256') { Stop-WithReason ('Wheel ' + $file.path + ' lacks a publisher SHA-256.') }
        if (([string]$file.url) -notmatch '^https://files\.pythonhosted\.org/') { Stop-WithReason ('Wheel ' + $file.path + ' URL is not on files.pythonhosted.org.') }
        if (([string]$file.path) -notmatch ('^' + [regex]::Escape(([string]$runtime.name).Replace('-', '_')) + '-' + [regex]::Escape([string]$runtime.version) + '-')) { Stop-WithReason ('Wheel ' + $file.path + ' does not match the pinned version of ' + $runtime.name) }
    }
}

$modelDir  = Join-Path $ModelsPath ([string]$model.name)
$wheelDir  = Join-Path $ModelsPath '.wheels'
$totalBytes = [int64]0
foreach ($file in @($model.files)) { $totalBytes = $totalBytes + [int64]$file.size }
foreach ($runtime in $runtimes) { foreach ($file in @($runtime.files)) { $totalBytes = $totalBytes + [int64]$file.size } }

Write-Output ''
Write-Output (' Manifest SHA-256 : ' + $manifestHash)
Write-Output (' Generated        : ' + $manifest.generated_at + ' by ' + $manifest.generated_by)
Write-Output (' Model            : ' + $model.name + ' @ ' + $model.revision + '  (' + @($model.files).Count + ' files)')
foreach ($runtime in $runtimes) { Write-Output (' Runtime          : ' + $runtime.name + ' ' + $runtime.version + '  licence ' + $runtime.license) }
Write-Output (' Total download   : ' + $totalBytes + ' bytes (' + [math]::Round($totalBytes / 1MB, 1) + ' MB)')
Write-Output ''
Write-Output ' Licence and provenance recorded in the manifest:'
foreach ($prov in $provs) {
    Write-Output ('   voice licence   : ' + $(if ($null -eq $prov.license) { 'not stated' } else { $prov.license }) + '  (' + $prov.source + ')')
    Write-Output ('   ' + $prov.license_note)
    Write-Output ('   speaker gender  : ' + $prov.speaker_gender)
    Write-Output ('   redistribution  : ' + $prov.redistribution)
    Write-Output ('   approved use    : ' + $prov.approved_use)
}
Write-Output ('   converted repo  : ' + $(if ($null -eq $model.license) { 'no licence metadata declared; none assigned' } else { $model.license }))

# ---------------------------------------------------------------- python
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($null -eq $command) { Stop-WithReason 'python.exe was not found. Pass -PythonExe with the full path to Python 3.11.' }
    $PythonExe = $command.Source
}
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
if (-not (Test-Path -LiteralPath $PythonExe)) { Stop-WithReason ('Python interpreter not found at ' + $PythonExe) }
$versionProbe = Invoke-Python -Interpreter $PythonExe -Arguments @('-I', '-c', 'import sys; print("%d.%d" % sys.version_info[:2]); print(sys.maxsize > 2**32)')
$probeLines = @($versionProbe.StdOut -split "`r?`n" | Where-Object { $_ -ne '' })
if ($versionProbe.ExitCode -ne 0 -or $probeLines.Count -lt 2) { Stop-WithReason 'Could not query the Python interpreter.' }
if ($probeLines[0] -ne $RequiredPythonMajorMinor) { Stop-WithReason ('The wheels are pinned for Python ' + $RequiredPythonMajorMinor + ' but ' + $PythonExe + ' is ' + $probeLines[0] + '.') }
if ($probeLines[1] -ne 'True') { Stop-WithReason 'A 64-bit Python is required for the win_amd64 wheels.' }
Write-Output (' Python           : ' + $PythonExe + ' (' + $probeLines[0] + ', 64-bit)')

# ---------------------------------------------------------------- plan
$plan = @()
$plan += ('create folder     ' + $wheelDir)
foreach ($runtime in $runtimes) { foreach ($file in @($runtime.files)) { $plan += ('download+verify   ' + $file.url + '  -> ' + (Join-Path $wheelDir $file.path) + '  [' + $file.size + ' B, ' + $file.hash_type + ' ' + $file.hash + ']') } }
$plan += ('create venv       ' + $RuntimePath + '   (python -m venv; no pip upgrade)')
$plan += ('pip install       --no-index --no-deps --require-hashes --find-links ' + $wheelDir + '  (pinned wheels only)')
$plan += ('create folder     ' + $modelDir)
foreach ($file in @($model.files)) { $plan += ('download+verify   ' + $file.path + '  [' + $file.size + ' B, ' + $file.hash_type + ']') }
$plan += ('write             ' + (Join-Path $modelDir $ManifestCopy) + '  (copy of the approved manifest)')
$plan += ('write             ' + (Join-Path $modelDir $ProvenanceDir) + '\  (Mycroft LICENSE, README.md, SOURCE, ALIASES + PROVENANCE.json)')
$plan += ('write             ' + $ConfigPath + '  (only if it does not exist)')
$plan += ('write             ' + (Join-Path $modelDir $MarkerName) + ' and ' + (Join-Path $RuntimePath $MarkerName) + '  (rollback records)')

Write-Output ''
Write-Output ' Planned actions:'
foreach ($line in $plan) { Write-Output ('   ' + $line) }
Write-Output ''
Write-Output ' Never done by this tool: ExecutionPolicy change, PATH change, scheduled task,'
Write-Output ' startup entry, pip upgrade, dependency resolution, git command, write into Git.'

if ($mode -eq 'dry-run') {
    Write-Output ''
    Write-Output ' DRY RUN: nothing was downloaded, created or changed.'
    Write-Output ' To install exactly this, review the manifest and run again with -Approve.'
    Write-Output '====================================================================='
    exit 0
}

# ---------------------------------------------------------------- pre-flight for approve
if (Test-Path -LiteralPath $modelDir)   { Stop-WithReason ('Model folder already exists: ' + $modelDir + '. Use -Rollback (if this tool created it) or choose another -ModelsPath. Nothing is overwritten.') }
if (Test-Path -LiteralPath $RuntimePath) { Stop-WithReason ('Runtime folder already exists: ' + $RuntimePath + '. Use -Rollback (if this tool created it) or choose another -RuntimePath. Nothing is overwritten.') }
if ((Test-Path -LiteralPath $ConfigPath)) { Write-Output (' NOTE: ' + $ConfigPath + ' exists and will NOT be changed; the required lines are printed at the end.') }

$createdModels  = @()
$createdRuntime = @()
try {
    if (-not (Test-Path -LiteralPath $ModelsPath)) { New-Item -ItemType Directory -Path $ModelsPath | Out-Null; $createdModels += $ModelsPath }
    if (-not (Test-Path -LiteralPath $wheelDir))   { New-Item -ItemType Directory -Path $wheelDir | Out-Null;   $createdModels += $wheelDir }

    # 1. wheels, verified before anything is installed
    $requirements = @()
    foreach ($runtime in $runtimes) {
        foreach ($file in @($runtime.files)) {
            $destination = Join-Path $wheelDir ([string]$file.path)
            if (Test-Path -LiteralPath $destination) {
                $problem = Test-FileAgainstEntry -Path $destination -Entry $file
                if ($null -ne $problem) { throw ('Existing wheel ' + $file.path + ' failed verification (' + $problem + '); remove it and retry.') }
                Write-Output (' reuse    ' + $file.path + ' (already present, verified)')
            } else {
                Write-Output (' download ' + $file.path)
                Save-VerifiedDownload -Uri ([string]$file.url) -Destination $destination -Entry $file
                $createdModels += $destination
                Write-Output (' verified ' + $file.path + '  ' + $file.hash_type)
            }
            $requirements += ([string]$runtime.name + '==' + [string]$runtime.version + ' --hash=sha256:' + [string]$file.hash)
        }
    }
    $requirementsPath = Join-Path $wheelDir 'requirements.lock.txt'
    [System.IO.File]::WriteAllLines($requirementsPath, $requirements, (New-Object System.Text.UTF8Encoding($false)))
    $createdModels += $requirementsPath

    # 2. isolated runtime
    Write-Output (' venv     ' + $RuntimePath)
    $venv = Invoke-Python -Interpreter $PythonExe -Arguments @('-I', '-m', 'venv', $RuntimePath)
    if ($venv.ExitCode -ne 0) { throw ('python -m venv failed: ' + $venv.StdErr) }
    $createdRuntime += $RuntimePath
    $venvPython = Join-Path (Join-Path $RuntimePath 'Scripts') 'python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) { throw 'The virtual environment has no Scripts\python.exe.' }

    Write-Output ' pip      install pinned wheels (offline, hashes required, no deps)'
    $pip = Invoke-Python -Interpreter $venvPython -Arguments @('-I', '-m', 'pip', 'install', '--no-index', '--no-deps', '--require-hashes', '--no-cache-dir', '--disable-pip-version-check', '--find-links', $wheelDir, '-r', $requirementsPath)
    if ($pip.ExitCode -ne 0) { throw ('pip install failed: ' + $pip.StdErr) }
    $check = Invoke-Python -Interpreter $venvPython -Arguments @('-I', '-c', 'import sherpa_onnx, sys; print(getattr(sherpa_onnx, "__version__", "unknown"))')
    if ($check.ExitCode -ne 0) { throw ('sherpa_onnx does not import in the new runtime: ' + $check.StdErr) }
    Write-Output (' runtime  sherpa_onnx ' + $check.StdOut.Trim() + ' imports')

    # 3. model files
    New-Item -ItemType Directory -Path $modelDir | Out-Null
    $createdModels += $modelDir
    $index = 0
    foreach ($file in @($model.files)) {
        $index = $index + 1
        $relative = ([string]$file.path) -replace '/', '\'
        $destination = Join-Path $modelDir $relative
        $parent = [System.IO.Path]::GetDirectoryName($destination)
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Write-Output (' [' + $index + '/' + @($model.files).Count + '] ' + $file.path)
        Save-VerifiedDownload -Uri ([string]$file.url) -Destination $destination -Entry $file
    }

    # 4. manifest copy + provenance next to the model
    Copy-Item -LiteralPath $ManifestPath -Destination (Join-Path $modelDir $ManifestCopy)
    $provDir = Join-Path $modelDir $ProvenanceDir
    New-Item -ItemType Directory -Path $provDir | Out-Null
    foreach ($prov in $provs) {
        if (Has-Property $prov 'documents') {
            foreach ($property in $prov.documents.PSObject.Properties) {
                $target = Join-Path $provDir ($property.Name)
                [System.IO.File]::WriteAllText($target, [string]$property.Value, (New-Object System.Text.UTF8Encoding($false)))
            }
        }
        $record = [ordered]@{
            original_repository = $prov.repository
            original_path       = $prov.path
            original_revision   = $prov.revision
            voice_license       = $prov.license
            license_note        = $prov.license_note
            speaker_gender      = $prov.speaker_gender
            redistribution      = $prov.redistribution
            approved_use        = $prov.approved_use
            converted_repository = $model.repository
            converted_revision   = $model.revision
            converted_license    = $model.license
            converted_license_note = $model.license_note
            installed_at        = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
            manifest_sha256     = $manifestHash
        }
        [System.IO.File]::WriteAllText((Join-Path $provDir 'PROVENANCE.json'), ($record | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    }

    # 5. local configuration (never overwrite)
    $configLines = @(
        '# Written by tools\voice\Install-PrimeeVoice.ps1. Git-ignored. Edit freely.',
        '[voice]',
        'enabled = true',
        'tts_engine = "sherpa-onnx"',
        'profile = "haaniye"',
        ('models_dir = "' + ($ModelsPath -replace '\\', '/') + '"'),
        ('runtime_dir = "' + ($RuntimePath -replace '\\', '/') + '"'),
        'player = "winsound"',
        'speak_summary_only = true',
        'max_spoken_chars = 240',
        'timeout_seconds = 60',
        'save_transcripts = false'
    )
    $wroteConfig = $false
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        [System.IO.File]::WriteAllLines($ConfigPath, $configLines, (New-Object System.Text.UTF8Encoding($false)))
        $wroteConfig = $true
        $createdModels += $ConfigPath
    }

    # 6. rollback records
    $stamp = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
    $modelMarker = [ordered]@{ tool = 'Install-PrimeeVoice.ps1'; installed_at = $stamp; manifest_sha256 = $manifestHash; created = $createdModels }
    [System.IO.File]::WriteAllText((Join-Path $ModelsPath $MarkerName), ($modelMarker | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
    $runtimeMarker = [ordered]@{ tool = 'Install-PrimeeVoice.ps1'; installed_at = $stamp; manifest_sha256 = $manifestHash; created = $createdRuntime }
    [System.IO.File]::WriteAllText((Join-Path $RuntimePath $MarkerName), ($runtimeMarker | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
} catch {
    Write-Output ''
    Write-Output (' FAILED: ' + $_.Exception.Message)
    Write-Output ' Cleaning up what this run created so far (nothing pre-existing is touched)...'
    foreach ($path in @(($createdModels + $createdRuntime) | Sort-Object -Property { $_.Length } -Descending)) {
        if (Test-Path -LiteralPath $path) { try { Remove-Item -LiteralPath $path -Recurse -Force; Write-Output ('   removed ' + $path) } catch { Write-Output ('   could not remove ' + $path) } }
    }
    Write-Output '====================================================================='
    exit 1
}

Write-Output ''
Write-Output '====================================================================='
Write-Output ' INSTALLED from the approved manifest. Every file was verified before use.'
Write-Output (' Model    : ' + $modelDir)
Write-Output (' Runtime  : ' + $RuntimePath)
if ($wroteConfig) { Write-Output (' Config   : ' + $ConfigPath + ' (created)') }
else {
    Write-Output (' Config   : ' + $ConfigPath + ' already existed and was left alone. Make sure it contains:')
    foreach ($line in $configLines) { Write-Output ('           ' + $line) }
}
Write-Output ''
Write-Output ' Also set audio.playback in config\permissions.local.toml to "approval" (or "auto").'
Write-Output ' Then, from the repository root:'
Write-Output '   python run_primee.py voice status'
Write-Output '   python run_primee.py voice benchmark --output "%LOCALAPPDATA%\Primee\benchmarks" --approve audio.playback'
Write-Output ' Rollback (removes only what this run created):'
Write-Output '   powershell -NoProfile -ExecutionPolicy Bypass -File tools\voice\Install-PrimeeVoice.ps1 -Rollback'
Write-Output '====================================================================='
