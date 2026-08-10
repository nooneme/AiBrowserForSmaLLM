# 手动启动真实 Edge（edge_manual_profile2）打开京东，保持窗口供手动登录。
# 用法（在 PowerShell 里）：
#   powershell -ExecutionPolicy Bypass -File .\open_jd_manual.ps1
#   powershell -ExecutionPolicy Bypass -File .\open_jd_manual.ps1 https://www.baidu.com
# 说明：
#   - 用系统真实 Edge + 全新 profile，不经过 Playwright，无任何自动化伪装。
#   - 需要先手动登录时用它打开，登录后关掉窗口再跑 main.py。
#   - 可传任意 URL 作为启动页（默认京东）。

param(
    [string]$Url = "https://www.jd.com/"
)

$msedge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $msedge)) {
    $msedge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
}
if (-not (Test-Path $msedge)) {
    Write-Error "找不到 msedge.exe，请检查 Edge 安装路径"
    exit 1
}

$profile = "C:\Users\z\Desktop\project\qiongguicode\edge_manual_profile2"
if (-not (Test-Path $profile)) {
    New-Item -ItemType Directory -Path $profile | Out-Null
    Write-Host "[*] 已创建新 profile: $profile"
}

# 先杀掉占用该 profile 的 Edge，避免目录被锁
Get-Process msedge -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*Edge*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

Start-Process -FilePath $msedge -ArgumentList `
    "--user-data-dir=$profile", "--start-maximized", $Url

Write-Host "[OK] 已用 profile2 打开: $Url"
Write-Host "[*] Edge 保持运行中，登录完成后请手动关闭窗口，再运行 main.py"
