@echo off
rem 将女仆酱注册到 Windows 开机自启动（在 shell:startup 创建快捷方式）
powershell -NoProfile -ExecutionPolicy Bypass -Command "$pyw=(Get-Command pyw).Source; $s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\SakuraMaid.lnk'); $s.TargetPath=$pyw; $s.Arguments='%~dp0main.py'; $s.WorkingDirectory='%~dp0'; $s.Save(); Write-Host 'OK: startup shortcut created'"
pause
