Write-Host "=== crash signal + thread ==="
adb -s HA2BN1JQ logcat -d | Select-String -Pattern "Fatal signal|>>> com.mumeione|name:.*tid" | ForEach-Object { $_.Line } | Select-Object -Last 6
Write-Host "=== python lines around crash ==="
adb -s HA2BN1JQ logcat -d | Select-String -Pattern "I python" | ForEach-Object { $_.Line } | Select-Object -Last 15
