<#
.SYNOPSIS
  One-click listening comparison for the installed Haaniye voice.

.DESCRIPTION
  Started by TUNE_PRIMEE_VOICE_WINDOWS.cmd after START_PRIMEE_VOICE_WINDOWS.cmd
  has installed the voice. Runs `primee voice tune`, which writes the same
  Persian sentence with five synthesis settings into
  %LOCALAPPDATA%\Primee\benchmarks\haaniye-tune-<timestamp>\, opens that folder
  in Explorer and shows which file is which. Nothing is downloaded, installed
  or played automatically; nothing in the repository is modified.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Protect-Text {
    param([AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $clean = $Text
    foreach ($name in @('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $clean = $clean.Replace($value, ('%' + $name + '%')) }
    }
    return [regex]::Replace($clean, '(?i)([A-Z]:\\Users\\)[^\\\r\n]+', '$1<user>')
}

function Show-Dialog {
    param([string]$Title, [string]$Text, [string]$Buttons = 'OK', [string]$Icon = 'Information')
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $options = [System.Windows.Forms.MessageBoxOptions]::RtlReading -bor [System.Windows.Forms.MessageBoxOptions]::RightAlign
        $result = [System.Windows.Forms.MessageBox]::Show($Text, $Title, [System.Windows.Forms.MessageBoxButtons]::$Buttons, [System.Windows.Forms.MessageBoxIcon]::$Icon, [System.Windows.Forms.MessageBoxDefaultButton]::Button1, $options)
        return [string]$result
    } catch { return $null }
}

function Stop-Tune {
    param([string]$Persian, [string]$English)
    Write-Host ('خطا: ' + $Persian) -ForegroundColor Red
    Write-Host ('ERROR: ' + $English) -ForegroundColor Red
    Show-Dialog -Title 'Primee Voice - خطا' -Text ($Persian + "`r`n`r`n" + $English) -Buttons 'OK' -Icon 'Error' | Out-Null
    exit 1
}

function Invoke-Child {
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $stdout = @()
    $code = -1
    try { $stdout = @(& $Exe @Arguments 2>$null); $code = $LASTEXITCODE } finally { $ErrorActionPreference = $previous }
    $lines = @()
    foreach ($line in $stdout) { $lines += [string]$line }
    return @{ ExitCode = [int]$code; Lines = $lines }
}

if ($env:OS -ne 'Windows_NT') { Stop-Tune 'فقط روی ویندوز.' 'Windows only.' }
$repoRoot = $null
try { $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } catch { $repoRoot = $null }
if ($null -eq $repoRoot -or -not (Test-Path -LiteralPath (Join-Path $repoRoot 'run_primee.py'))) { Stop-Tune 'پوشهٔ پرایمی کامل نیست.' 'Primee repository is incomplete next to the launcher.' }
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { Stop-Tune 'متغیر LOCALAPPDATA تنظیم نیست.' 'LOCALAPPDATA is not set.' }

$launcherCfg  = Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'launcher') 'config'
$benchmarkDir = Join-Path (Join-Path $env:LOCALAPPDATA 'Primee') 'benchmarks'
$runtimePath  = Join-Path $repoRoot '.venv-voice'
if (-not (Test-Path -LiteralPath (Join-Path $launcherCfg 'voice.toml')) -or -not (Test-Path -LiteralPath (Join-Path $runtimePath 'Scripts\python.exe'))) {
    Stop-Tune 'صدای هانیه هنوز با START_PRIMEE_VOICE_WINDOWS.cmd نصب نشده است. اول آن را اجرا کنید.' 'The Haaniye voice is not installed yet; run START_PRIMEE_VOICE_WINDOWS.cmd first.'
}

$python = $null
$command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
if ($null -ne $command) { $python = $command.Source }
if ($null -eq $python) {
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $probe = Invoke-Child -Exe $launcher.Source -Arguments @('-3.11', '-I', '-c', 'import sys; print(sys.executable)')
        if ($probe.ExitCode -eq 0 -and $probe.Lines.Count -ge 1) { $python = [string]$probe.Lines[0] }
    }
}
if ($null -eq $python -or -not (Test-Path -LiteralPath $python)) { Stop-Tune 'Python 3.11 پیدا نشد.' 'Python 3.11 was not found.' }

Write-Host 'در حال ساختن پنج نسخهٔ یک جمله با تنظیمات مختلف... (چند ثانیه تا یک دقیقه)'
Write-Host 'Writing five variants of one sentence with different settings...'
$run = Invoke-Child -Exe $python -Arguments @('-I', '-X', 'utf8', (Join-Path $repoRoot 'run_primee.py'), '--config', $launcherCfg, 'voice', 'tune', '--output', $benchmarkDir, '--approve', 'audio.playback', '--json')
$report = $null
try { $report = ($run.Lines -join "`n") | ConvertFrom-Json } catch { $report = $null }
if ($run.ExitCode -ne 0 -or $null -eq $report -or -not [bool]$report.ok) {
    $reason = ''
    try { $reason = [string]$report.reason } catch { $reason = '' }
    Stop-Tune ('ساخت نسخه‌های مقایسه‌ای ناموفق بود. ' + $reason) ('Tuning failed. ' + $reason)
}

$outputDir = [string]$report.output_dir
$lines = @('پنج فایل ساخته شد. هر کدام را گوش دهید و شمارهٔ بهترین را به من بگویید:', '')
foreach ($variant in @($report.variants)) {
    $p = $variant.parameters
    $lines += ('• ' + [string]$variant.file + '  —  ' + [string]$variant.description)
    $lines += ('    speed=' + $p.speed + '  noise_scale=' + $p.noise_scale + '  noise_scale_w=' + $p.noise_scale_w + '  sentence_batch=' + $p.sentence_batch)
}
$lines += ''
$lines += ('پوشه: ' + (Protect-Text -Text $outputDir))
$lines += 'هیچ فایلی خودکار پخش نمی‌شود؛ روی هر فایل دوبار کلیک کنید.'
$lines += ''
$lines += 'English: five WAV files, one per setting. Listen, tell Primee which number sounds best.'
foreach ($line in $lines) { Write-Host $line }
try { & (Join-Path $env:SystemRoot 'explorer.exe') $outputDir } catch { }
Show-Dialog -Title 'Primee Voice - مقایسهٔ شنیداری' -Text ($lines -join "`r`n") -Buttons 'OK' -Icon 'Information' | Out-Null
exit 0
