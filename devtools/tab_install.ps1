$ErrorActionPreference = "Stop"
$src = Get-ChildItem "\\wsl$\Ubuntu-24.04\home\ltdz1376\bookantool\bin\*.apk" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
adb -s HA2BN1JQ install -r $src
adb -s HA2BN1JQ logcat -c
adb -s HA2BN1JQ shell am start -n com.mumeione.bookantool/org.kivy.android.PythonActivity | Out-Null
Start-Sleep -Seconds 8
Write-Host "pid=$(adb -s HA2BN1JQ shell pidof com.mumeione.bookantool)"
adb -s HA2BN1JQ logcat -d | Select-String -Pattern "FATAL|Traceback|Fatal signal" | ForEach-Object { $_.Line } | Select-Object -Last 10
