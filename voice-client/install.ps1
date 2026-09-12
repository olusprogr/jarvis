# Installs Jarvis Voice Client from this project's dist/ build into a proper
# per-user install location, sets up autostart + Start Menu + Add/Remove Programs
# entry, and starts it. Re-run after a rebuild to update the installed copy.
$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$InstallDir = "$env:LOCALAPPDATA\JarvisClient"
$StartupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$StartMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"

Write-Host "Stopping any running instance..."
Get-Process JarvisClient -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Write-Host "Installing to $InstallDir ..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item "$ProjectDir\dist\JarvisClient.exe" "$InstallDir\JarvisClient.exe" -Force
Copy-Item "$ProjectDir\.env" "$InstallDir\.env" -Force
New-Item -ItemType Directory -Force -Path "$InstallDir\models" | Out-Null
Copy-Item "$ProjectDir\models\*" "$InstallDir\models\" -Recurse -Force  # trailing \* -- avoids Copy-Item nesting the folder into itself when the destination already exists
Copy-Item "$ProjectDir\uninstall.ps1.template" "$InstallDir\uninstall.ps1" -Force
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null

Write-Host "Removing old dev-mode autostart entry, if any..."
Remove-Item "$StartupDir\JarvisClient.vbs" -Force -ErrorAction SilentlyContinue

Write-Host "Creating Startup + Start Menu shortcuts..."
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @($StartupDir, $StartMenuDir)) {
    $lnk = $ws.CreateShortcut("$dir\JarvisClient.lnk")
    $lnk.TargetPath = "$InstallDir\JarvisClient.exe"
    $lnk.WorkingDirectory = $InstallDir
    $lnk.Description = "Jarvis Voice Client - Hey Jarvis wake-word listener"
    $lnk.Save()
}

Write-Host "Registering Add/Remove Programs entry..."
$RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\JarvisClient"
New-Item -Path $RegPath -Force | Out-Null
Set-ItemProperty -Path $RegPath -Name DisplayName -Value "Jarvis Voice Client"
Set-ItemProperty -Path $RegPath -Name DisplayVersion -Value "1.0.0"
Set-ItemProperty -Path $RegPath -Name Publisher -Value "Olus"
Set-ItemProperty -Path $RegPath -Name InstallLocation -Value $InstallDir
Set-ItemProperty -Path $RegPath -Name UninstallString -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
Set-ItemProperty -Path $RegPath -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $RegPath -Name NoRepair -Value 1 -Type DWord

Write-Host "Starting Jarvis..."
Start-Process -FilePath "$InstallDir\JarvisClient.exe" -WorkingDirectory $InstallDir

Write-Host "Done. Installed at $InstallDir, autostarts at logon, listed under Apps & Features as 'Jarvis Voice Client'."
