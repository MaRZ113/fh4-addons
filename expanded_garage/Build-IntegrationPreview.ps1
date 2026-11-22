param(
    [Parameter(Mandatory)] [string] $Output,
    [Parameter(Mandatory)] [string] $Fh4Database,
    [Parameter(Mandatory)] [string] $Fh4Manifest,
    [Parameter(Mandatory)] [string] $Fh4Materials,
    [Parameter(Mandatory)] [string] $Fh4Strings,
    [Parameter(Mandatory)] [string] $OriginalDatabase,
    [Parameter(Mandatory)] [string] $OriginalManifest,
    [Parameter(Mandatory)] [string] $Crypto,
    [string] $V1 = '.scratch/expanded-garage',
    [switch] $WrapFleet
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$v1Root = (Resolve-Path -LiteralPath $V1).Path
$out = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
if (Test-Path -LiteralPath $out) { throw "Use a new output directory: $out" }
$db = (Resolve-Path -LiteralPath $Fh4Database).Path
$manifest = (Resolve-Path -LiteralPath $Fh4Manifest).Path
$materials = (Resolve-Path -LiteralPath $Fh4Materials).Path
$strings = (Resolve-Path -LiteralPath $Fh4Strings).Path
$originalDb = (Resolve-Path -LiteralPath $OriginalDatabase).Path
$originalMount = (Resolve-Path -LiteralPath $OriginalManifest).Path
$wrapper = (Resolve-Path -LiteralPath $Crypto).Path
$registry = Join-Path $out 'registry.sqlite'
$target = @('--target', 'fh4', '--fh4-db', $db, '--fh4-manifest', $manifest,
            '--fh4-materials', $materials, '--fh4-strings', $strings, '--registry', $registry)
$control = Join-Path $v1Root 'control-cars'
$honda = Join-Path $control 'HON_IntegraR_00/ir'
$suzuki = Join-Path $control 'SUZ_SX4_11/ir'
$ruf = Join-Path $control 'RUF_CTR2_95/ir'
$solo = Join-Path $out 'suzuki'
$fleet = Join-Path $out 'fleet'
$deployment = Join-Path $out 'suzuki-package'
$wrapArgs = @('--build', $solo, '--out', $deployment, '--crypto', $wrapper,
              '--original-db', $originalDb, '--original-manifest', $originalMount, '--original-en', $strings,
              '--fh4-materials', $materials)
$verifyInputs = @('--fh4-db', $db, '--fh4-manifest', $manifest, '--fh4-materials', $materials)

New-Item -ItemType Directory -Path $out | Out-Null
Copy-Item -LiteralPath (Join-Path $v1Root 'registry.sqlite') -Destination $registry
Push-Location -LiteralPath $workspace
try {
    python -B -m expanded_garage compile @target --ir $suzuki --out $solo
    if ($LASTEXITCODE) { throw 'Suzuki compile failed' }
    python -B -m expanded_garage compile-fleet @target --ir $honda --ir $suzuki --ir $ruf --out $fleet
    if ($LASTEXITCODE) { throw 'Fleet compile failed' }
    foreach ($candidate in @($solo, $fleet)) {
        python -B -m expanded_garage verify $candidate @verifyInputs
        if ($LASTEXITCODE) { throw "Native audit failed: $candidate" }
    }
    python -B -m expanded_garage prepare-package @wrapArgs
    if ($LASTEXITCODE) { throw 'Native package staging failed' }
    if ($WrapFleet) {
        python -B -m expanded_garage prepare-package --build $fleet --out (Join-Path $out 'fleet-package') `
            --crypto $wrapper --original-db $originalDb --original-manifest $originalMount --original-en $strings `
            --fh4-materials $materials
        if ($LASTEXITCODE) { throw 'Fleet package staging failed' }
    }
}
finally {
    Pop-Location
}
