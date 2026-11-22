param(
    [Parameter(Mandatory)]
    [ValidateSet('front-bumper','rear-wing','mount-control','front-bumper-fixed-layout',
                 'rear-wing-fixed-layout','mount-control-fixed-layout','bumper-and-wing-fixed-layout','stock')]
    [string]$Probe
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = Join-Path $root 'game/ForzaHorizon4'
$scratch = Join-Path $root '.scratch/phase2'
$backup = Join-Path $scratch 'stock-assets'
$live = @((Join-Path $game 'media/cars/ACU_IntegraR_01.zip'),
          (Join-Path $game 'media/zipmanifest.xml'))
$paths = @{
    stock = @((Join-Path $backup 'ACU_IntegraR_01.zip'), (Join-Path $backup 'zipmanifest.xml'))
}
$hashes = @{
    stock = @('12cccdf370292c07b69eb13a406b88722012f3b87a74dba64a6e1ee53d15b12d',
              'eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703')
    'front-bumper' = @('de5755392b00d2c9d69e5351214829ab6c0f7c636009eeba4c2775bb96ae43a5',
                      '7af2c66de2ffeab28d4d327d1405760f2648e24fd9cf1cbecaf125e8b0b92389')
    'rear-wing' = @('da00c18a42e7099fd3d81a85a9f5bc5f82eae07cb5a550891a3b601b858b47a8',
                   '496a696d5c78f2032eb9fb795286bc0bbee94ee2221c183fd81285c191e74b77')
    'mount-control' = @('b723d72a5e297fae932dd8fd5d7694239f24ec8e1db8eb8b2ef14450fb97e42e',
                       'dbeeee9aad87e0963d694988c8a32a9aed88c3a24dfe7a58d247a6e4292bc430')
}
$fixed = @{
    'front-bumper-fixed-layout' = '2facc951664c9309143b1074440e54545a98b5737ecdc0df13e02752575c7f35'
    'rear-wing-fixed-layout' = '04dfccff2bf0c75b2d2c2d7fbc0fd27397395eb3dd369ebf1406c5d87bc51205'
    'mount-control-fixed-layout' = 'bbc0b7b8ba4c66572cf5ced2bb4aa09c80a1d804bc9551f80d4fae81d784b565'
    'bumper-and-wing-fixed-layout' = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
}
foreach ($name in $fixed.Keys) { $hashes[$name] = @($fixed[$name], $hashes.stock[1]) }
foreach ($name in $hashes.Keys | Where-Object { $_ -ne 'stock' }) {
    $paths[$name] = @((Join-Path $scratch "$name/ACU_IntegraR_01.zip"),
                      (Join-Path $scratch "$name/zipmanifest.xml"))
}

function Assert-Closed {
    if (Get-Process ForzaHorizon4 -ErrorAction SilentlyContinue) { throw 'Close FH4 first.' }
}
function Get-Hash([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
function Assert-Pair($actual, $expected) {
    for ($i = 0; $i -lt 2; $i++) {
        if ((Get-Hash $actual[$i]) -ne $expected[$i]) { throw "Hash mismatch: $($actual[$i])" }
    }
}
function Install-File([string]$source, [string]$target, [string]$hash) {
    $temp = Join-Path (Split-Path -Parent $target) ('.fh4-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::Copy($source, $temp, $false)
        if ((Get-Hash $temp) -ne $hash) { throw "Staging hash mismatch: $source" }
        Assert-Closed
        [IO.File]::Move($temp, $target, $true)
    } finally {
        if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
    }
}

Assert-Closed
foreach ($path in @($game, (Join-Path $game 'media'), (Join-Path $game 'media/cars')) + $live) {
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unexpected link: $path"
    }
}
$db = Join-Path $game 'media/stripped/gamedbRC.slt'
if ((Get-Hash $db) -ne 'dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8') {
    throw 'Expected the deployed Phase 1 GameDB.'
}
$before = @($live | ForEach-Object { Get-Hash $_ })
$previous = @($hashes.Keys | Where-Object { ($hashes[$_] -join ' ') -eq ($before -join ' ') })
if ($previous.Count -ne 1) { throw 'Unrecognized game asset pair.' }
if ($previous[0] -eq 'stock') {
    $null = New-Item -ItemType Directory -Path $backup -Force
    for ($i = 0; $i -lt 2; $i++) {
        if (-not [IO.File]::Exists($paths.stock[$i])) {
            [IO.File]::Copy($live[$i], $paths.stock[$i], $false)
        }
    }
}
Assert-Pair $paths.stock $hashes.stock
Assert-Pair $paths[$previous[0]] $hashes[$previous[0]]
Assert-Pair $paths[$Probe] $hashes[$Probe]
$installed = [System.Collections.Generic.List[int]]::new()
try {
    for ($i = 0; $i -lt 2; $i++) {
        if ($before[$i] -eq $hashes[$Probe][$i]) { continue }
        Install-File $paths[$Probe][$i] $live[$i] $hashes[$Probe][$i]
        $installed.Add($i)
    }
    Assert-Pair $live $hashes[$Probe]
} catch {
    foreach ($i in $installed) {
        Install-File $paths[$previous[0]][$i] $live[$i] $hashes[$previous[0]][$i]
    }
    throw
}
Get-FileHash -LiteralPath ($live + $paths.stock + @($db))
