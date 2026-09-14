<#
.SYNOPSIS
  One-click Primee Voice setup for Windows: Haaniye text-to-speech foundation.

.DESCRIPTION
  Started by START_PRIMEE_VOICE_WINDOWS.cmd. Windows PowerShell 5.1, no
  Administrator rights, no Git, no PATH or policy change, no scheduled task.

  Steps, in order:
    1. Windows 10/11, not elevated, repository located next to the launcher.
    2. Read-only preflight: Python 3.11 64-bit, free disk space, Primee Core
       runs in text-only mode.
    3. Pinned manifest generated from public metadata (nothing downloaded).
    4. Manifest validated automatically: runtime version, wheel names, sizes,
       hashes, 40-character model revision, every required model file
       including espeak-ng-data, licence and provenance records.
    5. Persian summary, then ONE local confirmation. Nothing is downloaded
       before it.
    6. tools\voice\Install-PrimeeVoice.ps1 -Approve, with the same manifest.
    7. Short Haaniye test through Primee itself: `primee voice speak`, which
       plays the audio and deletes the temporary WAV, then the benchmark set
       is written outside the repository for listening.
    8. A sanitised diagnostic report is written to %LOCALAPPDATA%\Primee\
       diagnostics\ on every run; on failure its path is shown.

  This covers the Haaniye TTS foundation and local benchmark only. Speech-to-
  text, push-to-talk and the complete voice loop are NOT part of it and Step
  Three is not complete.
#>
[CmdletBinding()]
param(
    [string]$ModelsPath = '',
    [switch]$SkipBenchmark
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------- pinned facts
$SherpaVersion = '1.13.7'
$ExpectedWheels = @(
    @{ Package = 'sherpa-onnx';      FileName = 'sherpa_onnx-1.13.7-cp311-cp311-win_amd64.whl';    Size = 2277232;  Sha256 = '7b3bef2a558f57c403bb7afb994e081cd242e5df3384295b6738b198cb2f8382' },
    @{ Package = 'sherpa-onnx-core'; FileName = 'sherpa_onnx_core-1.13.7-py3-none-win_amd64.whl';  Size = 16526006; Sha256 = 'cbcb78ef8a3bb74acf0b9d0a8715e9b857491be8e2b1e8f589f09ecf3d2f2a0b' }
)
$ModelDirName        = 'vits-mimic3-fa-haaniye_low'
$RequiredModelFiles  = @('fa-haaniye_low.onnx', 'fa-haaniye_low.onnx.json', 'tokens.txt')
$RequiredEspeakFiles = @('espeak-ng-data/phontab', 'espeak-ng-data/phonindex', 'espeak-ng-data/phondata')
$MinEspeakFiles      = 10
$MinFreeBytes        = 1073741824
$TestSentence        = 'سلام، من پرایمی هستم. صدای هانیه آماده است.'

# ---------------------------------------------------------------- state
$script:LogLines   = New-Object System.Collections.Generic.List[string]
$script:Started    = Get-Date
$script:ReportPath = $null

function Protect-Text {
    <# Remove the account name and home folder from anything that reaches the log. #>
    param([AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $clean = $Text
    foreach ($name in @('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $clean = $clean.Replace($value, ('%' + $name + '%')) }
    }
    $clean = [regex]::Replace($clean, '(?i)([A-Z]:\\Users\\)[^\\\r\n]+', '$1<user>')
    $userName = $env:USERNAME
    if (-not [string]::IsNullOrWhiteSpace($userName) -and $userName.Length -ge 3) { $clean = $clean.Replace($userName, '<user>') }
    return $clean
}

function Log {
    param([AllowEmptyString()][string]$Text, [switch]$Quiet)
    $safe = Protect-Text -Text $Text
    $script:LogLines.Add(('[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $safe))
    if (-not $Quiet) { Write-Host $safe }
}

function Say {
    <# Persian first, English second, both logged. #>
    param([string]$Persian, [string]$English = '')
    Log $Persian
    if ($English -ne '') { Log ('   ' + $English) }
}

function Write-Report {
    param([string]$Outcome)
    try {
        $dir = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'diagnostics'
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        $script:ReportPath = Join-Path $dir ('primee-voice-launcher-' + $script:Started.ToString('yyyyMMdd-HHmmss') + '.txt')
        $head = @(
            'PRIMEE VOICE LAUNCHER DIAGNOSTIC REPORT (sanitised: no account name, no home path, no spoken text)',
            ('outcome  : ' + $Outcome),
            ('started  : ' + $script:Started.ToString('yyyy-MM-dd HH:mm:ss')),
            ('windows  : ' + [Environment]::OSVersion.VersionString),
            ('psver    : ' + $PSVersionTable.PSVersion.ToString()),
            ''
        )
        [System.IO.File]::WriteAllLines($script:ReportPath, ($head + $script:LogLines.ToArray()), (New-Object System.Text.UTF8Encoding($false)))
    } catch { $script:ReportPath = $null }
}

function Stop-Launcher {
    param([string]$Persian, [string]$English)
    Write-Host ''
    Write-Host ('خطا: ' + $Persian) -ForegroundColor Red
    Write-Host ('ERROR: ' + $English) -ForegroundColor Red
    Log ('STOPPED: ' + $English) -Quiet
    Write-Report -Outcome 'failed'
    $reportNote = ''
    if ($null -ne $script:ReportPath) { $reportNote = "`r`n`r`n" + 'گزارش عیب‌یابی (بدون اطلاعات شخصی): ' + (Protect-Text -Text $script:ReportPath) }
    Show-Dialog -Title 'Primee Voice - خطا' -Text ($Persian + "`r`n`r`n" + 'پرایمی در حالت متنی همچنان کار می‌کند. چیزی خارج از پوشه‌های اعلام‌شده تغییر نکرد.' + $reportNote + "`r`n`r`n" + $English) -Buttons 'OK' -Icon 'Error' | Out-Null
    if ($null -ne $script:ReportPath) {
        Write-Host ''
        Write-Host ('گزارش عیب‌یابی (بدون اطلاعات شخصی) ذخیره شد: ' + (Protect-Text -Text $script:ReportPath))
        Write-Host ('Diagnostic report (sanitised): ' + (Protect-Text -Text $script:ReportPath))
    }
    Write-Host ''
    Write-Host 'پرایمی در حالت متنی همچنان کار می‌کند. هیچ تغییری خارج از پوشه‌های اعلام‌شده انجام نشد.'
    Write-Host 'Primee keeps working in text-only mode. Nothing outside the announced folders was changed.'
    exit 1
}

function Show-Dialog {
    <#
      The console cannot render Persian with its default font, so the summary,
      the confirmation and the final messages also go to a Windows dialog
      (System.Windows.Forms, part of Windows). Returns 'Yes', 'No' or 'OK';
      returns $null if no dialog can be shown, and the caller falls back to the console.
    #>
    param([string]$Title, [string]$Text, [string]$Buttons = 'OK', [string]$Icon = 'Information')
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $options = [System.Windows.Forms.MessageBoxOptions]::RtlReading -bor [System.Windows.Forms.MessageBoxOptions]::RightAlign
        $result = [System.Windows.Forms.MessageBox]::Show($Text, $Title, [System.Windows.Forms.MessageBoxButtons]::$Buttons, [System.Windows.Forms.MessageBoxIcon]::$Icon, [System.Windows.Forms.MessageBoxDefaultButton]::Button2, $options)
        return [string]$result
    } catch { return $null }
}

function Invoke-Child {
    <#
      Run one executable with an argument ARRAY through the call operator.
      No command string, no cmd.exe, no Invoke-Expression. stdout and stderr
      are both logged (sanitised); stdout lines are returned for parsing.
    #>
    param([string]$Exe, [string[]]$Arguments, [string]$Label, [switch]$HideOutput)
    foreach ($argument in $Arguments) {
        if ($argument -match '[\r\n]') { throw ('Refused: argument for ' + $Label + ' contains a line break.') }
    }
    Log ('> ' + $Label) -Quiet
    $errFile = [System.IO.Path]::GetTempFileName()
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $stdout = @()
    $code = -1
    try {
        $stdout = @(& $Exe @Arguments 2> $errFile)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $stderr = @()
    try { if (Test-Path -LiteralPath $errFile) { $stderr = @(Get-Content -LiteralPath $errFile) } } catch { $stderr = @() }
    try { Remove-Item -LiteralPath $errFile -Force } catch { }
    $lines = @()
    foreach ($line in $stdout) { $lines += [string]$line }
    if (-not $HideOutput) { foreach ($line in $lines) { Log ('  | ' + $line) } }
    else { Log ('  | (' + $lines.Count + ' output line(s) not logged)') -Quiet }
    foreach ($line in $stderr) { if (-not [string]::IsNullOrWhiteSpace([string]$line)) { Log ('  ! ' + [string]$line) } }
    if ($null -eq $code) { $code = -1 }
    Log ('< ' + $Label + ' exit ' + $code) -Quiet
    return @{ ExitCode = [int]$code; Lines = $lines }
}

function Has-Property { param($Object, [string]$Name); if ($null -eq $Object) { return $false }; return ($Object.PSObject.Properties.Name -contains $Name) }

function Test-Elevated {
    try {
        $identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Get-FreeBytes {
    param([string]$Path)
    try {
        $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
        $drive = New-Object System.IO.DriveInfo($root)
        return [int64]$drive.AvailableFreeSpace
    } catch { return -1 }
}

function Find-Python311 {
    param([string]$PowerShellExe)
    $candidates = @()
    $command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($null -ne $command) { $candidates += $command.Source }
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $probe = Invoke-Child -Exe $launcher.Source -Arguments @('-3.11', '-I', '-c', 'import sys; print(sys.executable)') -Label 'py -3.11 lookup'
        if ($probe.ExitCode -eq 0 -and $probe.Lines.Count -ge 1) { $candidates += [string]$probe.Lines[0] }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe') }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) { $candidates += (Join-Path $env:ProgramFiles 'Python311\python.exe') }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $full = [System.IO.Path]::GetFullPath($candidate)
        if ($seen.ContainsKey($full.ToLower())) { continue }
        $seen[$full.ToLower()] = $true
        if (-not (Test-Path -LiteralPath $full)) { continue }
        # No double quotes inside inline Python: Windows PowerShell 5.1 strips them when calling a native program.
        $probe = Invoke-Child -Exe $full -Arguments @('-I', '-c', 'import sys; print(sys.version_info[0], sys.version_info[1]); print(sys.maxsize > 2**32)') -Label ('python probe')
        if ($probe.ExitCode -ne 0 -or $probe.Lines.Count -lt 2) { continue }
        if (([string]$probe.Lines[0]).Trim() -eq '3 11' -and ([string]$probe.Lines[1]).Trim() -eq 'True') { return $full }
    }
    return $null
}

function Format-MB { param([int64]$Bytes); return ([math]::Round($Bytes / 1MB, 1).ToString() + ' MB') }

function Test-Manifest {
    <# Automatic validation. Returns a summary hashtable or throws with a plain-English reason. #>
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw 'manifest file was not written' }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $manifest = $null
    try { $manifest = $raw | ConvertFrom-Json } catch { throw 'manifest is not valid JSON' }
    if ([string]$manifest.schema -ne 'primee-voice-manifest' -or [int]$manifest.schema_version -ne 1) { throw 'manifest schema is not recognised' }
    if ([string]$manifest.profile -ne 'haaniye') { throw 'manifest profile is not haaniye' }

    $runtimes = @($manifest.components | Where-Object { $_.component -eq 'runtime' })
    $models   = @($manifest.components | Where-Object { $_.component -eq 'model' })
    $provs    = @($manifest.components | Where-Object { $_.component -eq 'provenance' })
    if ($models.Count -ne 1) { throw 'manifest must contain exactly one model component' }
    if ($provs.Count -lt 1) { throw 'manifest has no provenance component' }

    # runtime: exact version, exact file names, sizes, hashes
    $totalBytes = [int64]0
    foreach ($expected in $ExpectedWheels) {
        $runtime = $null
        foreach ($r in $runtimes) { if ([string]$r.name -eq $expected.Package) { $runtime = $r } }
        if ($null -eq $runtime) { throw ('runtime component ' + $expected.Package + ' is missing') }
        if ([string]$runtime.version -ne $SherpaVersion) { throw ($expected.Package + ' version is ' + $runtime.version + ', expected ' + $SherpaVersion) }
        $files = @($runtime.files)
        if ($files.Count -ne 1) { throw ($expected.Package + ' must list exactly one wheel') }
        $file = $files[0]
        if ([string]$file.path -ne $expected.FileName) { throw ('wheel file name differs: ' + $file.path) }
        if ([int64]$file.size -ne $expected.Size) { throw ('wheel size differs for ' + $file.path) }
        if ([string]$file.hash_type -ne 'publisher-sha256') { throw ('wheel hash type is not publisher-sha256 for ' + $file.path) }
        if (-not [string]::Equals(([string]$file.hash).ToLower(), $expected.Sha256, [System.StringComparison]::Ordinal)) { throw ('wheel SHA-256 differs from the reviewed value for ' + $file.path) }
        if (([string]$file.url) -notmatch '^https://files\.pythonhosted\.org/') { throw ('wheel URL is not on files.pythonhosted.org for ' + $file.path) }
        $totalBytes = $totalBytes + [int64]$file.size
    }

    # model: pinned revision, complete file list with verifiable hashes
    $model = $models[0]
    $revision = [string]$model.revision
    if ($revision -notmatch '^[0-9a-f]{40}$') { throw 'model revision is not a 40-character commit id' }
    if ([string]$model.name -ne $ModelDirName) { throw ('model name is ' + $model.name + ', expected ' + $ModelDirName) }
    $paths = @()
    $espeakCount = 0
    $lfsCount = 0
    foreach ($file in @($model.files)) {
        $p = [string]$file.path
        if ($p -match '\.\.|^/|^[A-Za-z]:|\\') { throw ('unsafe model path: ' + $p) }
        if ([string]$file.hash_type -eq 'none' -or [string]::IsNullOrWhiteSpace([string]$file.hash)) { throw ('model file without a verifiable hash: ' + $p) }
        if (([string]$file.url) -notmatch ('^https://huggingface\.co/.+/resolve/' + $revision + '/')) { throw ('model file URL is not pinned to the revision: ' + $p) }
        if ([int64]$file.size -lt 0) { throw ('model file has an invalid size: ' + $p) }
        if ($p.StartsWith('espeak-ng-data/')) { $espeakCount = $espeakCount + 1 }
        if ([string]$file.hash_type -eq 'publisher-sha256') { $lfsCount = $lfsCount + 1 }
        $paths += $p
        $totalBytes = $totalBytes + [int64]$file.size
    }
    foreach ($required in ($RequiredModelFiles + $RequiredEspeakFiles)) {
        if ($paths -notcontains $required) { throw ('required model file is not listed: ' + $required) }
    }
    if ($espeakCount -lt $MinEspeakFiles) { throw ('espeak-ng-data lists only ' + $espeakCount + ' files') }
    $onnx = $null
    foreach ($file in @($model.files)) { if ([string]$file.path -eq 'fa-haaniye_low.onnx') { $onnx = $file } }
    if ([string]$onnx.hash_type -ne 'publisher-sha256') { throw 'the ONNX model has no publisher SHA-256' }
    if ([int64]$onnx.size -lt 30000000 -or [int64]$onnx.size -gt 200000000) { throw ('the ONNX model size is implausible: ' + $onnx.size) }

    # provenance: keep the licence record and the warnings visible, never silently
    $prov = $provs[0]
    $licenseText = ''
    $sourceText  = ''
    if ((Has-Property $prov 'documents')) {
        if (Has-Property $prov.documents 'LICENSE') { $licenseText = ([string]$prov.documents.LICENSE).Trim() }
        if (Has-Property $prov.documents 'SOURCE')  { $sourceText  = ([string]$prov.documents.SOURCE).Trim() }
    }
    if ($licenseText -ne 'CC-0') { throw ('the upstream LICENSE file no longer reads CC-0 (it reads "' + $licenseText + '"); review before installing') }
    $convertedLicense = 'not declared'
    if ((Has-Property $model 'license') -and $null -ne $model.license -and -not [string]::IsNullOrWhiteSpace([string]$model.license)) { $convertedLicense = [string]$model.license }

    return @{
        Revision         = $revision
        ModelFiles       = $paths.Count
        EspeakFiles      = $espeakCount
        LfsFiles         = $lfsCount
        OnnxBytes        = [int64]$onnx.size
        TotalBytes       = $totalBytes
        LicenseText      = $licenseText
        SourceText       = $sourceText
        ConvertedLicense = $convertedLicense
        ProvenanceRev    = [string]$prov.revision
    }
}

function Write-LauncherConfig {
    <#
      A launcher-owned configuration folder OUTSIDE the repository, used only
      for the test and the benchmark. The repository's own config files are
      never modified by the launcher.
    #>
    param([string]$Directory, [string]$Models, [string]$Runtime)
    if (-not (Test-Path -LiteralPath $Directory)) { New-Item -ItemType Directory -Path $Directory -Force | Out-Null }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Join-Path $Directory 'primee.toml'), @('# launcher-owned; used only for the Haaniye test and benchmark', '[runtime]', 'dry_run = false'), $utf8)
    [System.IO.File]::WriteAllLines((Join-Path $Directory 'permissions.toml'), @('[permissions]', 'default = "never"', '[permissions.modes]', '"audio.playback" = "approval"'), $utf8)
    [System.IO.File]::WriteAllLines((Join-Path $Directory 'voice.toml'), @(
        '[voice]', 'enabled = true', 'tts_engine = "sherpa-onnx"', 'profile = "haaniye"',
        ('models_dir = "' + ($Models -replace '\\', '/') + '"'),
        ('runtime_dir = "' + ($Runtime -replace '\\', '/') + '"'),
        'player = "winsound"', 'speak_summary_only = true', 'max_spoken_chars = 240', 'timeout_seconds = 90', 'save_transcripts = false'
    ), $utf8)
    return @((Join-Path $Directory 'primee.toml'), (Join-Path $Directory 'permissions.toml'), (Join-Path $Directory 'voice.toml'), $Directory)
}

# ================================================================ main
try {
    Write-Host ''
    Write-Host '=================================================================' -ForegroundColor Cyan
    Write-Host ' Primee Voice - راه‌اندازی صدای هانیه (فقط بنیان متن‌به‌گفتار)' -ForegroundColor Cyan
    Write-Host ' Primee Voice - Haaniye text-to-speech foundation setup' -ForegroundColor Cyan
    Write-Host '=================================================================' -ForegroundColor Cyan
    Log ('launcher started ' + $script:Started.ToString('yyyy-MM-dd HH:mm:ss')) -Quiet

    # ---- 1. platform -------------------------------------------------
    if ($env:OS -ne 'Windows_NT') { Stop-Launcher 'این راه‌انداز فقط روی ویندوز اجرا می‌شود.' 'This launcher runs on Windows only.' }
    $osVersion = [Environment]::OSVersion.Version
    if ($osVersion.Major -lt 10) { Stop-Launcher ('ویندوز ۱۰ یا ۱۱ لازم است. نسخهٔ فعلی: ' + $osVersion.ToString()) ('Windows 10 or 11 is required; found ' + $osVersion.ToString()) }
    if ($PSVersionTable.PSVersion.Major -lt 5) { Stop-Launcher 'PowerShell 5.1 لازم است.' 'PowerShell 5.1 is required.' }
    if (Test-Elevated) { Stop-Launcher 'این فایل را به‌صورت عادی اجرا کنید، نه به‌عنوان Administrator. نصب به دسترسی مدیر نیاز ندارد.' 'Run this launcher normally, not as Administrator; the setup needs no elevated rights.' }
    Say 'مرحلهٔ ۱: ویندوز و PowerShell بررسی شد.' ('Step 1: Windows ' + $osVersion.ToString() + ', PowerShell ' + $PSVersionTable.PSVersion.ToString() + ', not elevated.')

    # ---- 2. repository -----------------------------------------------
    $repoRoot = $null
    try { $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } catch { $repoRoot = $null }
    $markers = @('run_primee.py', 'src\primee\cli.py', 'tools\voice\Get-PrimeeVoiceManifest.ps1', 'tools\voice\Install-PrimeeVoice.ps1', 'tools\voice\sherpa_tts_worker.py')
    $missingMarkers = @()
    if ($null -eq $repoRoot) { $missingMarkers += '(repository root)' }
    else { foreach ($marker in $markers) { if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $marker))) { $missingMarkers += $marker } } }
    if ($missingMarkers.Count -gt 0) {
        Stop-Launcher 'پوشهٔ پرایمی کامل نیست. فایل ZIP را کامل استخراج کنید و دوباره روی START_PRIMEE_VOICE_WINDOWS.cmd دوبار کلیک کنید.' ('Primee repository is incomplete next to the launcher; missing: ' + ($missingMarkers -join ', '))
    }
    Say 'مرحلهٔ ۲: پوشهٔ پرایمی پیدا شد.' ('Step 2: repository located at ' + (Protect-Text -Text $repoRoot))

    $powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $powerShellExe)) { Stop-Launcher 'PowerShell ویندوز پیدا نشد.' 'Windows PowerShell executable was not found.' }

    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { Stop-Launcher 'متغیر LOCALAPPDATA تنظیم نیست.' 'LOCALAPPDATA is not set.' }
    if ([string]::IsNullOrWhiteSpace($ModelsPath)) {
        if (-not [string]::IsNullOrWhiteSpace($env:PRIMEE_MODELS_PATH)) { $ModelsPath = $env:PRIMEE_MODELS_PATH }
        else { $ModelsPath = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'models' }
    }
    $ModelsPath    = [System.IO.Path]::GetFullPath($ModelsPath)
    $runtimePath   = Join-Path $repoRoot '.venv-voice'
    $manifestPath  = Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'manifests') 'haaniye-sherpa.manifest.json'
    $launcherDir   = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'launcher'
    $launcherCfg   = Join-Path $launcherDir 'config'
    $launcherMark  = Join-Path $launcherDir '.primee-launcher-created.json'
    $benchmarkDir  = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'benchmarks'
    $modelDir      = Join-Path $ModelsPath $ModelDirName
    $runtimeMarker = Join-Path $runtimePath '.primee-installed.json'
    $modelMarker   = Join-Path $ModelsPath '.primee-installed.json'
    $installer     = Join-Path $repoRoot 'tools\voice\Install-PrimeeVoice.ps1'
    $manifestTool  = Join-Path $repoRoot 'tools\voice\Get-PrimeeVoiceManifest.ps1'
    $runPrimee     = Join-Path $repoRoot 'run_primee.py'

    # ---- 3. read-only preflight --------------------------------------
    $python = Find-Python311 -PowerShellExe $powerShellExe
    if ($null -eq $python) {
        Stop-Launcher 'Python 3.11 (64 بیتی) پیدا نشد. Python 3.11 را از python.org نصب کنید و دوباره اجرا کنید.' 'Python 3.11 64-bit was not found (python.exe, py -3.11, or the default install folders).'
    }
    Say 'مرحلهٔ ۳: Python 3.11 پیدا شد.' ('Step 3: Python 3.11 64-bit at ' + (Protect-Text -Text $python))

    $doctor = Invoke-Child -Exe $python -Arguments @('-I', '-X', 'utf8', $runPrimee, 'doctor', '--json') -Label 'primee doctor (read-only)' -HideOutput
    if ($doctor.ExitCode -ne 0) { Stop-Launcher 'هستهٔ پرایمی در حالت متنی اجرا نشد.' 'Primee Core did not start in text-only mode (primee doctor failed).' }
    Say '       هستهٔ پرایمی در حالت متنی سالم است.' 'Primee Core runs in text-only mode.'

    foreach ($check in @(@{ Path = $ModelsPath; Label = 'models' }, @{ Path = $repoRoot; Label = 'runtime' })) {
        $free = Get-FreeBytes -Path $check.Path
        if ($free -ge 0 -and $free -lt $MinFreeBytes) { Stop-Launcher ('فضای دیسک برای ' + $check.Label + ' کافی نیست (کمتر از ۱ گیگابایت آزاد).') ('Not enough free disk space for ' + $check.Label + ': ' + (Format-MB $free)) }
    }

    $runtimeInstalled = (Test-Path -LiteralPath $runtimeMarker) -and (Test-Path -LiteralPath (Join-Path $runtimePath 'Scripts\python.exe'))
    $modelInstalled   = (Test-Path -LiteralPath $modelMarker) -and (Test-Path -LiteralPath $modelDir)
    $alreadyInstalled = $runtimeInstalled -and $modelInstalled
    if (-not $alreadyInstalled) {
        if ((Test-Path -LiteralPath $runtimePath) -or (Test-Path -LiteralPath $modelDir)) {
            if ($runtimeInstalled -or $modelInstalled) {
                Stop-Launcher 'نصب قبلی ناقص است. ابتدا ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd را اجرا کنید و سپس دوباره تلاش کنید.' 'A previous installation is incomplete; run ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd first.'
            }
            Stop-Launcher ('پوشه‌ای از قبل وجود دارد که این نصب‌کننده آن را نساخته است (' + (Protect-Text -Text $runtimePath) + ' یا ' + (Protect-Text -Text $modelDir) + '). هیچ پوشه‌ای حذف نمی‌شود؛ آن را خودتان جابه‌جا کنید.') 'A runtime or model folder exists that this installer did not create; nothing is deleted. Move it aside and retry.'
        }
    }

    $summary = $null
    if ($alreadyInstalled) {
        Say 'مرحلهٔ ۴: نصب قبلی این راه‌انداز پیدا شد؛ دانلود لازم نیست.' 'Step 4: an installation recorded by this launcher already exists; nothing to download.'
    } else {
        # ---- 4. manifest from public metadata (no download) ----------
        Say 'مرحلهٔ ۴: خواندن اطلاعات عمومی مدل و موتور (بدون دانلود)...' 'Step 4: reading public metadata only; nothing is downloaded in this step.'
        $manifestDir = [System.IO.Path]::GetDirectoryName($manifestPath)
        if (-not (Test-Path -LiteralPath $manifestDir)) { New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null }
        $gen = Invoke-Child -Exe $powerShellExe -Arguments @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $manifestTool, '-OutputPath', $manifestPath, '-Force') -Label 'Get-PrimeeVoiceManifest.ps1'
        if ($gen.ExitCode -ne 0) {
            $reason = ''
            foreach ($line in $gen.Lines) { if (([string]$line).Trim().StartsWith('STOPPED:')) { $reason = ([string]$line).Trim() } }
            if ($reason -eq '') { $reason = 'no STOPPED line; see the diagnostic report (network, or the published metadata drifted from the reviewed values)' }
            Stop-Launcher ('خواندن اطلاعات عمومی ناموفق بود. چیزی دانلود یا نصب نشد. علت: ' + $reason) ('Manifest generation failed; nothing was downloaded or installed. Reason: ' + $reason)
        }

        # ---- 5. automatic validation ---------------------------------
        try { $summary = Test-Manifest -Path $manifestPath } catch { Stop-Launcher ('اعتبارسنجی خودکار Manifest ناموفق بود: ' + $_.Exception.Message) ('Manifest validation failed: ' + $_.Exception.Message) }
        Say 'مرحلهٔ ۵: Manifest به‌صورت خودکار اعتبارسنجی شد.' ('Step 5: manifest validated: runtime ' + $SherpaVersion + ', model revision ' + $summary.Revision + ', ' + $summary.ModelFiles + ' model files (' + $summary.EspeakFiles + ' under espeak-ng-data/).')

        # ---- 6. Persian summary + ONE confirmation -------------------
        $summaryLines = @(
            'چه چیزی نصب می‌شود:',
            ('• موتور گفتار sherpa-onnx نسخهٔ ' + $SherpaVersion + ' (مجوز Apache-2.0) در پوشهٔ ' + (Protect-Text -Text $runtimePath)),
            ('• مدل صدای هانیه (فارسی، کیفیت پایین، تک‌گوینده) در پوشهٔ ' + (Protect-Text -Text $modelDir)),
            ('• حجم کل دانلود: حدود ' + (Format-MB $summary.TotalBytes) + ' (مدل ONNX: ' + (Format-MB $summary.OnnxBytes) + ')'),
            ('• ' + $summary.ModelFiles + ' فایل مدل شامل ' + $summary.EspeakFiles + ' فایل espeak-ng-data؛ همه با hash ناشر بررسی می‌شوند'),
            ('• نسخهٔ قفل‌شدهٔ مدل: ' + $summary.Revision),
            '',
            'مجوز و منشأ (همان‌طور که در منابع رسمی آمده):',
            ('• مجوز صدای اصلی (Mycroft): ' + $summary.LicenseText + ' = CC0'),
            '• Dataset: در README فقط «public domain» توصیف شده است',
            ('• فایل SOURCE: "' + $summary.SourceText + '" — منشأ دقیق Dataset ناقص است'),
            ('• مجوز مخزن تبدیل‌شده: ' + $summary.ConvertedLicense + ' — هیچ مجوزی به آن نسبت داده نمی‌شود'),
            '• جنسیت گوینده در مستندات رسمی ذکر نشده؛ فقط با شنیدن مشخص می‌شود',
            '• فقط برای آزمایش خصوصی محلی؛ هیچ ادعایی دربارهٔ بازتوزیع یا استفادهٔ تجاری نمی‌شود',
            '',
            'انجام نمی‌شود: تغییر PATH یا سیاست ویندوز، Task زمان‌بندی‌شده، اجرای خودکار، دسترسی مدیر، حذف پوشه‌های قبلی.',
            'این راه‌انداز فقط بنیان متن‌به‌گفتار و آزمایش محلی را نصب می‌کند. تشخیص گفتار، Push-to-Talk و گام سوم کامل نیستند.',
            '',
            'تا این لحظه هیچ چیزی دانلود نشده است.',
            '',
            'Yes = دانلود و نصب        No = انصراف',
            ('English: install sherpa-onnx ' + $SherpaVersion + ' (Apache-2.0) into .venv-voice and the Haaniye model (' + (Format-MB $summary.TotalBytes) + ' total). Voice licence CC0; dataset provenance incomplete (SOURCE=TBD); private local benchmark only. Nothing has been downloaded yet. Yes = install, No = cancel.')
        )
        Write-Host ''
        Write-Host '----------------------------- خلاصه / summary -----------------------------' -ForegroundColor Yellow
        foreach ($line in $summaryLines) { Log $line }
        Write-Host '---------------------------------------------------------------------------' -ForegroundColor Yellow
        Write-Host 'Nothing has been downloaded yet.' -ForegroundColor Green
        Write-Host ''
        $answer = Show-Dialog -Title 'Primee Voice - تأیید نصب / confirm installation' -Text ($summaryLines -join "`r`n") -Buttons 'YesNo' -Icon 'Question'
        if ($null -eq $answer) {
            Write-Host '(The Persian summary is in the lines above; the console font may show it as question marks. It is also written to the diagnostic report.)'
            $typed = Read-Host 'برای شروع دانلود و نصب، کلمهٔ YES را تایپ کنید و Enter بزنید (هر چیز دیگر = انصراف) / Type YES to install'
            if (([string]$typed).Trim().ToUpper() -ne 'YES') { $answer = 'No' } else { $answer = 'Yes' }
        }
        Log ('confirmation: ' + $answer) -Quiet
        if ($answer -ne 'Yes') {
            Say 'انصراف داده شد. چیزی دانلود یا نصب نشد.' 'Cancelled by the user. Nothing was downloaded or installed.'
            Write-Report -Outcome 'cancelled'
            exit 0
        }

        # ---- 7. install from exactly this manifest -------------------
        Say 'مرحلهٔ ۶: دانلود و نصب از همان Manifest تأییدشده... (هر فایل قبل از استفاده با hash بررسی می‌شود)' 'Step 6: installing from the approved manifest; every file is verified before use.'
        $install = Invoke-Child -Exe $powerShellExe -Arguments @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-Approve', '-ManifestPath', $manifestPath, '-ModelsPath', $ModelsPath, '-RuntimePath', $runtimePath, '-PythonExe', $python) -Label 'Install-PrimeeVoice.ps1 -Approve'
        if ($install.ExitCode -ne 0) { Stop-Launcher 'نصب ناموفق بود. نصب‌کننده هر چیزی را که در همین اجرا ساخته بود پاک کرد؛ پوشه‌های قبلی دست نخورده‌اند.' 'Installation failed; the installer removed what it had created in this run. Nothing pre-existing was touched.' }
        Say 'مرحلهٔ ۷: نصب کامل شد و همهٔ فایل‌ها با hash بررسی شدند.' 'Step 7: installed; every file verified.'
    }

    # ---- 8. runtime version check ------------------------------------
    $venvPython = Join-Path $runtimePath 'Scripts\python.exe'
    # Package names travel as separate arguments so the inline Python needs no quotes at all.
    $ver = Invoke-Child -Exe $venvPython -Arguments @('-I', '-c', 'import sys; from importlib.metadata import version; print(version(sys.argv[1])); print(version(sys.argv[2]))', 'sherpa-onnx', 'sherpa-onnx-core') -Label 'runtime version check'
    if ($ver.ExitCode -ne 0 -or $ver.Lines.Count -lt 2 -or ([string]$ver.Lines[0]).Trim() -ne $SherpaVersion -or ([string]$ver.Lines[1]).Trim() -ne $SherpaVersion) {
        Stop-Launcher ('نسخهٔ موتور نصب‌شده با نسخهٔ قفل‌شده (' + $SherpaVersion + ') مطابقت ندارد.') ('Installed runtime version does not match the pinned ' + $SherpaVersion + '.')
    }
    Say ('       نسخهٔ موتور تأیید شد: ' + $SherpaVersion) ('Runtime version confirmed: ' + $SherpaVersion)

    # ---- 9. launcher-owned config, status, test ----------------------
    $created = Write-LauncherConfig -Directory $launcherCfg -Models $ModelsPath -Runtime $runtimePath
    $markerObject = [ordered]@{ tool = 'Start-PrimeeVoice.ps1'; created_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'); created = $created }
    [System.IO.File]::WriteAllText($launcherMark, ($markerObject | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))

    $statusObject = $null
    $status = Invoke-Child -Exe $python -Arguments @('-I', '-X', 'utf8', $runPrimee, '--config', $launcherCfg, 'voice', 'status', '--json') -Label 'primee voice status' -HideOutput
    $statusOk = $false
    if ($status.ExitCode -eq 0) {
        try { $statusObject = ($status.Lines -join "`n") | ConvertFrom-Json; $statusOk = [bool]$statusObject.adapter.available } catch { $statusOk = $false }
    }
    if (-not $statusOk) {
        $reasons = ''
        try { $reasons = (@($statusObject.adapter.reasons) -join '; ') } catch { $reasons = '' }
        Stop-Launcher ('پرایمی موتور صدا را آماده نمی‌بیند: ' + $reasons) ('Primee reports the voice engine unavailable: ' + $reasons)
    }
    Say 'مرحلهٔ ۸: پرایمی موتور صدا و مدل را تأیید کرد (hash فایل‌های نصب‌شده دوباره بررسی شد).' 'Step 8: Primee verified the runtime and the installed model against the manifest.'

    Write-Host ''
    Write-Host 'مرحلهٔ ۹: آزمایش کوتاه صدای هانیه... بلندگو را روشن کنید.' -ForegroundColor Cyan
    Write-Host 'Step 9: short Haaniye test; turn the speaker on.' -ForegroundColor Cyan
    $speakObject = $null
    $speak = Invoke-Child -Exe $python -Arguments @('-I', '-X', 'utf8', $runPrimee, '--config', $launcherCfg, 'voice', 'speak', $TestSentence, '--approve', 'audio.playback', '--json') -Label 'primee voice speak (test)' -HideOutput
    $spoken = $false
    $speakError = ''
    try {
        $speakObject = ($speak.Lines -join "`n") | ConvertFrom-Json
        $spoken = [bool]$speakObject.spoken
        if (-not $spoken) { $speakError = [string]$speakObject.error_code + ' ' + [string]$speakObject.fallback_reason }
        if ((Has-Property $speakObject 'result') -and ($null -ne $speakObject.result)) {
            Log ('test metrics: audio ' + $speakObject.result.audio_seconds + 's, synthesis ' + $speakObject.result.synthesis_seconds + 's, rtf ' + $speakObject.result.real_time_factor) -Quiet
        }
    } catch { $speakError = 'unreadable result' }
    if (-not $spoken) { Stop-Launcher ('صدای آزمایشی پخش نشد: ' + $speakError) ('The test sentence was not spoken: ' + $speakError) }
    Say 'صدای آزمایشی پخش شد و فایل موقت آن حذف شد.' 'Test sentence spoken; the temporary audio was deleted by Primee.'

    # ---- 10. benchmark set for listening -----------------------------
    $benchDir = ''
    if (-not $SkipBenchmark) {
        $bench = Invoke-Child -Exe $python -Arguments @('-I', '-X', 'utf8', $runPrimee, '--config', $launcherCfg, 'voice', 'benchmark', '--output', $benchmarkDir, '--approve', 'audio.playback', '--json') -Label 'primee voice benchmark' -HideOutput
        $benchObject = $null
        try { $benchObject = ($bench.Lines -join "`n") | ConvertFrom-Json; $benchDir = [string]$benchObject.output_dir } catch { $benchDir = '' }
        if ($bench.ExitCode -eq 0 -and $benchDir -ne '') {
            Say ('مرحلهٔ ۱۰: هفت جملهٔ آزمایشی در این پوشه ذخیره شد تا گوش دهید و تصمیم بگیرید (قبول / قبول موقت / رد): ' + (Protect-Text -Text $benchDir)) ('Step 10: benchmark files written to ' + (Protect-Text -Text $benchDir) + '; listen and classify: Accept / Accept temporarily / Reject.')
        } else {
            Say 'مرحلهٔ ۱۰: تولید مجموعهٔ آزمایشی ناموفق بود؛ نصب و آزمایش کوتاه معتبر می‌مانند.' 'Step 10: the benchmark set could not be written; the installation and the short test stand.'
        }
    }

    Write-Host ''
    Write-Host '=================================================================' -ForegroundColor Green
    Write-Host ' انجام شد. بنیان متن‌به‌گفتار هانیه نصب و آزمایش شد.' -ForegroundColor Green
    Write-Host ' Done. The Haaniye text-to-speech foundation is installed and tested.' -ForegroundColor Green
    Write-Host '=================================================================' -ForegroundColor Green
    Write-Host 'یادآوری: تشخیص گفتار، Push-to-Talk و حلقهٔ کامل صوتی هنوز ساخته نشده‌اند؛ گام سوم کامل نیست.'
    Write-Host 'Reminder: speech-to-text, push-to-talk and the full voice loop are not built; Step Three is not complete.'
    Write-Host 'برای حذف کامل: ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd'
    Write-Report -Outcome 'success'
    $benchNote = ''
    if (-not $SkipBenchmark -and $benchDir -ne '') { $benchNote = "`r`n`r`n" + 'هفت جملهٔ آزمایشی برای گوش‌دادن در: ' + (Protect-Text -Text $benchDir) }
    Show-Dialog -Title 'Primee Voice - انجام شد' -Text ('بنیان متن‌به‌گفتار هانیه نصب و آزمایش شد.' + $benchNote + "`r`n`r`n" + 'تشخیص گفتار، Push-to-Talk و حلقهٔ کامل صوتی هنوز ساخته نشده‌اند؛ گام سوم کامل نیست.' + "`r`n" + 'برای حذف کامل: ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd' + "`r`n`r`n" + 'Done. The Haaniye text-to-speech foundation is installed and tested.') -Buttons 'OK' -Icon 'Information' | Out-Null
    exit 0
} catch {
    $message = ''
    try { $message = [string]$_.Exception.Message } catch { $message = 'unknown error' }
    Stop-Launcher ('خطای پیش‌بینی‌نشده: ' + $message) ('Unexpected error: ' + $message)
}
