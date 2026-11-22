param(
    [Parameter(Mandatory)] [string] $Fm4Root,
    [Parameter(Mandatory)] [string] $JulyDlc,
    [Parameter(Mandatory)] [string] $Fh4Database,
    [Parameter(Mandatory)] [string] $Fh4Manifest,
    [Parameter(Mandatory)] [string] $Fh4Materials,
    [Parameter(Mandatory)] [string] $Registry,
    [Parameter(Mandatory)] [string] $Output,
    [string] $StfsTool
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$fm4 = (Resolve-Path -LiteralPath $Fm4Root).Path
$july = (Resolve-Path -LiteralPath $JulyDlc).Path
$db = (Resolve-Path -LiteralPath $Fh4Database).Path
$manifest = (Resolve-Path -LiteralPath $Fh4Manifest).Path
$materials = (Resolve-Path -LiteralPath $Fh4Materials).Path
$registryFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Registry)
$outputRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$pack = @('--dlc', $july)
if ($StfsTool) {
    $pack += @('--stfs-tool', (Resolve-Path -LiteralPath $StfsTool).Path)
}
$common = @('--source', 'fm4', '--target', 'fh4', '--fm4-root', $fm4,
    '--fh4-db', $db, '--fh4-manifest', $manifest, '--fh4-materials', $materials, '--registry', $registryFile)

Push-Location -LiteralPath $workspace
try {
    foreach ($name in @('HON_IntegraR_00', 'SUZ_SX4_11', 'RUF_CTR2_95')) {
        $destination = Join-Path $outputRoot $name
        $dlc = if ($name -eq 'RUF_CTR2_95') { $pack } else { @() }
        python -m expanded_garage import @common --car $name --out $destination @dlc
        if ($LASTEXITCODE -ne 0) { throw "Import failed for $name" }
        python -m expanded_garage verify (Join-Path $destination 'fh4')
        if ($LASTEXITCODE -ne 0) { throw "Validation failed for $name" }
    }
}
finally {
    Pop-Location
}
