$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'AVARA storefront - Gmail setup'
Write-Host 'Enter the Gmail address used to create the app password.'
$sender = Read-Host 'Gmail address'
$secret = Read-Host 'Gmail app password (input hidden)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:AVARA_STORE_SMTP_USER = $sender.Trim()
    $env:AVARA_STORE_SMTP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Replace(' ', '')
    $env:AVARA_STORE_SECURE_COOKIE = '0'
    $env:AVARA_STORE_TEST_MODE = '0'
    Write-Host 'Opening http://127.0.0.1:8092/ — keep this window open.'
    Start-Process 'http://127.0.0.1:8092/'
    & (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe') -m uvicorn server:app --host 127.0.0.1 --port 8092
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Item Env:AVARA_STORE_SMTP_PASSWORD -ErrorAction SilentlyContinue
}
