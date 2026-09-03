$ADB = "adb"
$DEV = "HA2BN1JQ"
& $ADB -s $DEV logcat -c
& $ADB -s $DEV shell am start -n com.mumeione.bookantool/org.kivy.android.PythonActivity | Out-Null
for ($i = 1; $i -le 6; $i++) {
    Start-Sleep -Seconds 3
    $pid = & $ADB -s $DEV shell pidof com.mumeione.bookantool
    Write-Host ("t+" + ($i * 3) + "s pid=" + $pid)
}
Write-Host "=== python errors ==="
& $ADB -s $DEV logcat -d | Select-String -Pattern "FATAL|Traceback|E python|Fatal signal" | ForEach-Object { $_.Line } | Select-Object -Last 30
& $ADB -s $DEV shell screencap -p /sdcard/s.png
& $ADB -s $DEV pull /sdcard/s.png "D:\Documents\pythoncharm\BookanTool V2.0\devtools\tab_screen.png" | Out-Null
& $ADB -s $DEV shell rm /sdcard/s.png
Write-Host "screenshot updated"
