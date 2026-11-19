#Requires -Version 7.3
if ($MyInvocation.InvocationName -eq '.') {
    throw 'Execute this launcher; do not dot-source it.'
}
# python.exe must be the real Python 3.12+ installation, not a Store alias.
& python.exe -I (Join-Path $PSScriptRoot 'codex-dev.py') @args
exit $LASTEXITCODE
