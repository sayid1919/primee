<#
.SYNOPSIS
  One-click rollback of the Primee Voice (Haaniye) installation.

.DESCRIPTION
  Started by ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd. Removes ONLY what the
  installer and the launcher recorded as created (.primee-installed.json and
  .primee-launcher-created.json). Pre-existing folders, the Vault, the audit
  log, benchmark recordings and any user file are never touched. No Git
  command is used. One local confirmation is required.
#>
[CmdletBinding()]
param(
    [string]$ModelsPath = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch { }

function Protect-Text {
    param([AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $clean = $Text
    foreach ($name in @('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $clean = $clean.Replace($value, ('%' + $name + '%')) }
    }
    $clean = [regex]::Replace($clean, '(?i)([A-Z]:\\Users\\)[^\\\r\n]+', '$1<user>')
    return $clean
}

function Stop-Rollback {
    param([string]$Persian, [string]$English)
    Write-Host ''
    Write-Host ('خطا: ' + $Persian) -ForegroundColor Red
    Write-Host ('ERROR: ' + $English) -ForegroundColor Red
    exit 1
}

function Show-Dialog {
    param([string]$Title, [string]$Text, [string]$Buttons = 'OK', [string]$Icon = 'Information')
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $options = [System.Windows.Forms.MessageBoxOptions]::RtlReading -bor [System.Windows.Forms.MessageBoxOptions]::RightAlign
        $result = [System.Windows.Forms.MessageBox]::Show($Text, $Title, [System.Windows.Forms.MessageBoxButtons]::$Buttons, [System.Windows.Forms.MessageBoxIcon]::$Icon, [System.Windows.Forms.MessageBoxDefaultButton]::Button2, $options)
        return [string]$result
    } catch { return $null }
}

function Has-Property { param($Object, [string]$Name); if ($null -eq $Object) { return $false }; return ($Object.PSObject.Properties.Name -contains $Name) }

Write-Host ''
Write-Host '=================================================================' -ForegroundColor Cyan
Write-Host ' Primee Voice - حذف نصب صدای هانیه' -ForegroundColor Cyan
Write-Host ' Primee Voice - rollback of the Haaniye installation' -ForegroundColor Cyan
Write-Host '=================================================================' -ForegroundColor Cyan

if ($env:OS -ne 'Windows_NT') { Stop-Rollback 'فقط روی ویندوز.' 'Windows only.' }
$repoRoot = $null
try { $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } catch { $repoRoot = $null }
$installer = $null
if ($null -ne $repoRoot) { $installer = Join-Path $repoRoot 'tools\voice\Install-PrimeeVoice.ps1' }
if ($null -eq $installer -or -not (Test-Path -LiteralPath $installer)) { Stop-Rollback 'پوشهٔ پرایمی کامل نیست.' 'Primee repository is incomplete next to the launcher.' }
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { Stop-Rollback 'متغیر LOCALAPPDATA تنظیم نیست.' 'LOCALAPPDATA is not set.' }

if ([string]::IsNullOrWhiteSpace($ModelsPath)) {
    if (-not [string]::IsNullOrWhiteSpace($env:PRIMEE_MODELS_PATH)) { $ModelsPath = $env:PRIMEE_MODELS_PATH }
    else { $ModelsPath = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'models' }
}
$ModelsPath    = [System.IO.Path]::GetFullPath($ModelsPath)
$runtimePath   = Join-Path $repoRoot '.venv-voice'
$launcherDir   = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'launcher'
$launcherMark  = Join-Path $launcherDir '.primee-launcher-created.json'
$runtimeMarker = Join-Path $runtimePath '.primee-installed.json'
$modelMarker   = Join-Path $ModelsPath '.primee-installed.json'
$powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$found = @()
foreach ($marker in @($runtimeMarker, $modelMarker, $launcherMark)) { if (Test-Path -LiteralPath $marker) { $found += $marker } }
if ($found.Count -eq 0) {
    Write-Host ''
    Write-Host 'هیچ نصبی که این راه‌انداز ساخته باشد پیدا نشد. چیزی حذف نمی‌شود.'
    Write-Host 'No installation recorded by this launcher was found. Nothing is removed.'
    exit 0
}

Write-Host ''
Write-Host 'موارد زیر، و فقط این‌ها، حذف می‌شوند (ثبت‌شده به‌عنوان ساخته‌شده توسط نصب‌کننده):' -ForegroundColor Yellow
Write-Host 'Only the following recorded items will be removed:' -ForegroundColor Yellow
$total = 0
foreach ($marker in $found) {
    try {
        $record = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Has-Property $record 'created') {
            foreach ($path in @($record.created)) { Write-Host ('  - ' + (Protect-Text -Text ([string]$path))); $total = $total + 1 }
        }
    } catch { Write-Host ('  (unreadable record: ' + (Protect-Text -Text $marker) + ')') }
}
Write-Host ''
Write-Host 'حذف نمی‌شوند: Vault، گزارش‌های audit، فایل‌های benchmark، پوشه‌هایی که از قبل وجود داشتند.'
Write-Host 'Not removed: the Vault, audit logs, benchmark recordings, any pre-existing folder.'
Write-Host ''
$listed = @()
foreach ($marker in $found) {
    try { $record = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json; if (Has-Property $record 'created') { foreach ($path in @($record.created)) { $listed += ('• ' + (Protect-Text -Text ([string]$path))) } } } catch { }
}
$dialogText = ('موارد زیر، و فقط این‌ها، حذف می‌شوند:' + "`r`n" + ($listed -join "`r`n") + "`r`n`r`n" + 'حذف نمی‌شوند: Vault، گزارش‌های audit، فایل‌های benchmark، پوشه‌های قبلی.' + "`r`n`r`n" + 'Yes = حذف        No = انصراف' + "`r`n" + 'English: remove only the recorded items above. Yes = remove, No = cancel.')
$answer = Show-Dialog -Title 'Primee Voice - تأیید حذف / confirm rollback' -Text $dialogText -Buttons 'YesNo' -Icon 'Warning'
if ($null -eq $answer) {
    $typed = Read-Host 'برای حذف، کلمهٔ YES را تایپ کنید (هر چیز دیگر = انصراف) / Type YES to remove'
    if (([string]$typed).Trim().ToUpper() -ne 'YES') { $answer = 'No' } else { $answer = 'Yes' }
}
if ($answer -ne 'Yes') {
    Write-Host 'انصراف داده شد. چیزی حذف نشد.'
    Write-Host 'Cancelled. Nothing was removed.'
    exit 0
}

$failed = $false
if ((Test-Path -LiteralPath $runtimeMarker) -or (Test-Path -LiteralPath $modelMarker)) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $powerShellExe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Rollback -ModelsPath $ModelsPath -RuntimePath $runtimePath
        if ($LASTEXITCODE -ne 0) { $failed = $true }
    } finally { $ErrorActionPreference = $previous }
}

if (Test-Path -LiteralPath $launcherMark) {
    try {
        $record = Get-Content -LiteralPath $launcherMark -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Has-Property $record 'created') {
            foreach ($path in @($record.created | Sort-Object -Property { ([string]$_).Length } -Descending)) {
                $full = [System.IO.Path]::GetFullPath([string]$path)
                if (-not $full.StartsWith($launcherDir, [System.StringComparison]::OrdinalIgnoreCase)) { Write-Host ('  refusing to remove a path outside the launcher folder: ' + (Protect-Text -Text $full)); continue }
                if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force; Write-Host ('  removed ' + (Protect-Text -Text $full)) }
            }
        }
        Remove-Item -LiteralPath $launcherMark -Force
        Write-Host ('  removed ' + (Protect-Text -Text $launcherMark))
    } catch { Write-Host ('  could not fully remove the launcher configuration: ' + $_.Exception.Message); $failed = $true }
}

Write-Host ''
if ($failed) {
    Write-Host 'حذف با خطا تمام شد؛ پیام‌های بالا را بخوانید. هیچ فایل ثبت‌نشده‌ای لمس نشد.' -ForegroundColor Red
    Write-Host 'Rollback finished with errors; see the messages above. No unrecorded file was touched.' -ForegroundColor Red
    exit 1
}
Write-Host 'حذف کامل شد. پرایمی در حالت متنی به کار خود ادامه می‌دهد.' -ForegroundColor Green
Write-Host 'Rollback complete. Primee continues in text-only mode.' -ForegroundColor Green
exit 0
