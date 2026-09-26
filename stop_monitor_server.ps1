$ErrorActionPreference = "Continue"
$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$lockPath = Join-Path $projectDirectory ".pc_server.lock"

$targets = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^python(w)?\.exe$' -and
            $_.CommandLine -match '(?i)pc_server\.py'
        }
)

if ($targets.Count -eq 0) {
    Write-Host "没有发现正在运行的小菜蛾监测服务。"
} else {
    foreach ($target in $targets) {
        try {
            Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
            Write-Host "已关闭监测服务，进程号：$($target.ProcessId)"
        } catch {
            Write-Host "关闭失败，进程号：$($target.ProcessId)"
            Write-Host $_.Exception.Message
        }
    }
}

Start-Sleep -Milliseconds 500

if (Test-Path -LiteralPath $lockPath) {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

$remaining = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^python(w)?\.exe$' -and
            $_.CommandLine -match '(?i)pc_server\.py'
        }
)

if ($remaining.Count -gt 0) {
    Write-Host "仍有监测服务未能关闭。"
    exit 1
}

Write-Host "网页、计数、图片和灯光控制服务均已停止。"
exit 0
