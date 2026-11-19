# デスクトップに Daily CLI Updater のワンクリック起動ショートカットを作成するスクリプト
param(
    [string]$DestinationDir = "C:\Users\seizo\Desktop"
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetBat = Join-Path $projectDir "start_updater.bat"
$iconPath = Join-Path $projectDir "assets\app_icon.ico"

if (-not (Test-Path -LiteralPath $DestinationDir)) {
    $DestinationDir = [Environment]::GetFolderPath("Desktop")
}

$shortcutPath = Join-Path $DestinationDir "Daily CLI Updater.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $targetBat
$shortcut.WorkingDirectory = $projectDir
$shortcut.WindowStyle = 7  # 最小化でコンソールを起動しブラウザだけ前面に出す
$shortcut.Description = "毎朝のCLIアプリを一括アップデートするローカルWebアプリ"
if (Test-Path -LiteralPath $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
}
$shortcut.Save()

# Windows エクスプローラーのアイコン表示を即時更新
Add-Type -Namespace Win32 -Name Shell32 -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int eventId, int flags, System.IntPtr item1, System.IntPtr item2);
"@ -ErrorAction SilentlyContinue
[Win32.Shell32]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)

Write-Host "デスクトップにアイコン付きショートカットを作成しました: $shortcutPath" -ForegroundColor Green
