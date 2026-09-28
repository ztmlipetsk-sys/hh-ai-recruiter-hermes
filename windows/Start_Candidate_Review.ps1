param([switch]$Run, [string]$Python = 'python', [string]$Vacancy = '137056588')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root 'hh-recruiter\scripts\candidate_questions.py'
if (-not (Test-Path $script)) { throw 'Candidate review script not found.' }
Get-Command $Python -ErrorAction Stop | Out-Null
$names = @('HH_ACCESS_TOKEN','HH_USER_AGENT','HH_REVIEW_TELEGRAM_TOKEN','HH_REVIEW_OWNER_ID')
$prior = @{}
foreach ($name in $names) { $prior[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
try {
    foreach ($name in $names) {
        if ([string]::IsNullOrWhiteSpace($prior[$name])) {
            if ($name -like '*TOKEN') {
                $secret = Read-Host "$name (hidden; separate review bot token required)" -AsSecureString
                $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
                try { [Environment]::SetEnvironmentVariable($name,[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr),'Process') }
                finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); $secret.Dispose() }
            } else {
                $value = Read-Host $name
                [Environment]::SetEnvironmentVariable($name,$value,'Process')
                $value = $null
            }
        }
    }
    & $Python $script check --vacancy $Vacancy
    if ($LASTEXITCODE -ne 0) { throw 'Connection check failed. Nothing was started.' }
    if ($Run) {
        & $Python $script run --vacancy $Vacancy
        if ($LASTEXITCODE -ne 0) { throw 'Worker stopped with an error. Inspect its status and HH history before retrying.' }
    } else { Write-Host 'Read-only check complete. Use -Run to start the review worker.' }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$prior[$name],'Process') }
}
