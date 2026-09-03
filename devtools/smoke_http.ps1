$ErrorActionPreference = "Continue"
$root = "D:\Documents\pythoncharm\BookanTool V2.0"
$env:BOOKAN_PORT = "8799"
$p = Start-Process -FilePath "python" -ArgumentList "main.py --web --no-gui-check" -WorkingDirectory $root -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 4
try {
    $r1 = Invoke-WebRequest -Uri "http://127.0.0.1:8799/" -UseBasicParsing -TimeoutSec 5
    $injected = $r1.Content -match "__bookan_android__" -and $r1.Content -match "android\.js"
    Write-Host ("CHECK inject_head: " + $(if ($injected) { "PASS" } else { "FAIL" }))

    $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:8799/bridge/health" -Method POST -Body '{"args":[]}' -ContentType "application/json" -UseBasicParsing -TimeoutSec 5
    Write-Host ("CHECK bridge_health: " + $r2.Content)

    $r3 = Invoke-WebRequest -Uri "http://127.0.0.1:8799/android.js" -UseBasicParsing -TimeoutSec 5
    Write-Host ("CHECK android_js: " + $(if ($r3.StatusCode -eq 200) { "PASS" } else { "FAIL" }))

    $r4 = Invoke-WebRequest -Uri "http://127.0.0.1:8799/bridge/get_config" -Method POST -Body '{"args":[]}' -ContentType "application/json" -UseBasicParsing -TimeoutSec 5
    Write-Host ("CHECK bridge_get_config: " + $r4.Content.Substring(0, [Math]::Min(120, $r4.Content.Length)))
} finally {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
