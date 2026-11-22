param([Parameter(Mandatory)] [ValidateSet('bumper','restore')] [string]$Mode)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = Join-Path $root 'game/ForzaHorizon4'
$live = Join-Path $game 'media/cars/ACU_IntegraR_01.zip'
$stock = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
$probe = 'bdb0327206640931eafe22d847f0dc5dc31a4b0f5a8f9193e2cfd83edbcaab2b'
$baseline = Join-Path $root '.scratch/phase3/phase2-baseline/ACU_IntegraR_01.zip'
$candidate = Join-Path $root '.scratch/phase3_1/shared-geometry/ACU_IntegraR_01.zip'
function Hash-File($p) { (Get-FileHash -LiteralPath $p).Hash }
function Assert-Closed {
    if (Get-Process ForzaHorizon4 -ErrorAction SilentlyContinue) { throw 'Close FH4 first.' }
}
Assert-Closed
foreach ($path in @($game,(Join-Path $game 'media'),(Split-Path -Parent $live),$live)) {
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unexpected link: $path"
    }
}
if ((Hash-File $baseline) -ne $stock -or (Hash-File $live) -notin @($stock,$probe)) {
    throw 'Unexpected shared ZIP or baseline.'
}
if ((Hash-File (Join-Path $game 'media/stripped/gamedbRC.slt')) -ne 'dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8' -or
    (Hash-File (Join-Path $game 'media/zipmanifest.xml')) -ne 'eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703') {
    throw 'Restore Phase 2 GameDB and manifest before this test.'
}
$source, $hash = if ($Mode -eq 'bumper') { $candidate, $probe } else { $baseline, $stock }
if ((Hash-File $source) -ne $hash) { throw 'Candidate hash mismatch.' }
$temp = Join-Path (Split-Path -Parent $live) ('.fh4-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    [IO.File]::Copy($source,$temp,$false)
    if ((Hash-File $temp) -ne $hash) { throw 'Staging hash mismatch.' }
    Assert-Closed
    [IO.File]::Move($temp,$live,$true)
    if ((Hash-File $live) -ne $hash) { throw 'Installed hash mismatch.' }
} finally {
    if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
}
Get-FileHash -LiteralPath $live
