$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = Resolve-Path (Join-Path $scriptDir '..')
Set-Location $repoRoot

$branch = git rev-parse --abbrev-ref HEAD
Write-Output "Current branch: $branch"

# detect remote default branch
$base = 'main'
$ls = git ls-remote --symref origin HEAD 2>$null
if ($ls) {
    $m = [regex]::Match($ls, 'ref: refs/heads/(?<b>[^\s]+)\s+HEAD')
    if ($m.Success) { $base = $m.Groups['b'].Value }
} else {
    if (git ls-remote --heads origin main 2>$null) { $base='main' } elseif (git ls-remote --heads origin master 2>$null) { $base='master' } else { $base='main' }
}
Write-Output "Detected base: $base"

$gh = 'C:\\Program Files\\GitHub CLI\\gh.exe'
$title = "Publish site: $branch"
$body = "Automated PR to publish branch $branch"

Write-Output "Creating PR..."
$out = & "$gh" pr create --title $title --body $body --base $base --head $branch --repo tcsutego897-code/Web_apps 2>&1

# Try to find PR URL in output
$m = [regex]::Match($out, 'https://github\\.com/[^\s/]+/[^\s/]+/pull/\d+')
if ($m.Success) {
    Write-Output ('PR URL: ' + $m.Value)
} else {
    Write-Output 'PR creation output:'
    Write-Output $out
}
