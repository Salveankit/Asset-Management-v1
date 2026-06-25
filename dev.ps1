param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$manage = Join-Path $PSScriptRoot 'manage.py'

if (!(Test-Path $python)) {
    throw "Expected interpreter not found: $python"
}

& $python $manage @Args
exit $LASTEXITCODE
