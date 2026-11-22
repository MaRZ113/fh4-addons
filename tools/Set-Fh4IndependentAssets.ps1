param(
    [Parameter(Mandatory)]
    [ValidateSet('alias','entrypoint','fixed-layout','loose','mount-only','restore',
                 'matrix-mount','matrix-routed','matrix-name','matrix-ordinal')]
    [string]$Mode,
    [string]$SaveDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = Join-Path $root 'game/ForzaHorizon4'
$scratch = Join-Path $root '.scratch/phase3'
$backup = Join-Path $scratch 'phase2-baseline'
$candidate = Join-Path $scratch $(switch ($Mode) {
    'mount-only' {'alias'}
    'restore' {'alias'}
    'loose' {'fixed-layout'}
    default {$Mode}
})
$matrixKind = switch ($Mode) {
    'matrix-mount' {'routed'}
    'matrix-routed' {'routed'}
    'matrix-name' {'name'}
    'matrix-ordinal' {'ordinal'}
}
if ($matrixKind) { $candidate = Join-Path $root ".scratch/phase3_1/identity/$matrixKind" }
$alias = 'MRZ_IntegraJ_00.zip'
$db = Join-Path $game 'media/stripped/gamedbRC.slt'
$manifest = Join-Path $game 'media/zipmanifest.xml'
$archive = Join-Path $game "media/cars/$alias"
$loose = Join-Path $game 'media/cars/MRZ_IntegraJ_00'
$looseReceipt = Join-Path $scratch 'loose-receipt.json'
$donor = Join-Path $game 'media/cars/ACU_IntegraR_01.zip'
$expected = @{
    db = 'dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8'
    manifest = 'eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703'
    donor = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
    exe = 'b838a79a65c6d1e12f46deaba3584e4c88ebccdfdc4214b4535ece4ba3c0abbe'
    aliasDb = 'd6d69fde7e08375ad1ee4e9881314f44e77770602f1d433220d02a2e66ea90f7'
    aliasManifest = '685060bd016bcf7daf217ff6ad663d3984b58432071fd379cd42c5ca09a2e967'
    aliasZip = 'b1638555b113cb025fa950e44ea962244af7dacf28c794a9e95182560df65e44'
    entryManifest = 'f18533e6dfa5c12f06645464310755777e1bd787ea6bc503b2821a4272c9322f'
    entryZip = '5ff272bbd9b3f36c9b70cb6c5467d28a57cc0150a358e4c4b9ed5164ddb6be6e'
    fixedZip = 'a22761af42114e6a5c7956755b92c614f179ae0fa155d8962292a81a5307a210'
    matrixRouted = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
    matrixName = 'd06d926f5b014a521a828203317d4975d923e65ade952f37f48d3bbdb812e881'
    matrixOrdinal = '750500ba21584b708151e761a6c5fd6c0257048a9e68cdb4e15e0ccfc405583b'
}
$deployManifest = if ($matrixKind -or $Mode -in @('entrypoint','fixed-layout','loose')) {
    $expected.entryManifest
} else { $expected.aliasManifest }
$deployZip = switch ($Mode) {
    'entrypoint' { $expected.entryZip }
    'fixed-layout' { $expected.fixedZip }
    'loose' { $expected.fixedZip }
    'matrix-mount' { $expected.matrixRouted }
    'matrix-routed' { $expected.matrixRouted }
    'matrix-name' { $expected.matrixName }
    'matrix-ordinal' { $expected.matrixOrdinal }
    default { $expected.aliasZip }
}

function Assert-Closed {
    if (Get-Process ForzaHorizon4 -ErrorAction SilentlyContinue) { throw 'Close FH4 before switching assets.' }
}
function Hash-File([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
function Assert-Hash([string]$path, [string]$hash) {
    if ((Hash-File $path) -ne $hash) { throw "SHA-256 mismatch: $path" }
}
function Assert-Ordinary([string]$path) {
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unexpected link: $path"
    }
}
function Place-File([string]$source, [string]$target, [string]$hash) {
    if ([IO.File]::Exists($target) -and (Hash-File $target) -eq $hash) { return }
    Assert-Hash $source $hash
    $temp = Join-Path (Split-Path -Parent $target) ('.fh4-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::Copy($source, $temp, $false)
        Assert-Hash $temp $hash
        Assert-Closed
        [IO.File]::Move($temp, $target, $true)
        Assert-Hash $target $hash
    } finally {
        if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
    }
}
function Remove-VerifiedAlias {
    if ([IO.File]::Exists($archive)) {
        Assert-KnownAlias
        Assert-Closed
        [IO.File]::Delete($archive)
    }
}
function Assert-KnownAlias {
    if ((Hash-File $archive) -notin @($expected.aliasZip,$expected.entryZip,$expected.fixedZip,
                                     $expected.matrixRouted,$expected.matrixName,$expected.matrixOrdinal)) {
        throw 'Unrecognized archive at the alias path.'
    }
}

function Get-TreeReceipt([string]$folder) {
    Assert-Ordinary $folder
    @(Get-ChildItem -LiteralPath $folder -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
        Assert-Ordinary $_.FullName
        [ordered]@{ Path = [IO.Path]::GetRelativePath($folder,$_.FullName); Hash = Hash-File $_.FullName }
    }) | ConvertTo-Json -Compress
}
function Move-VerifiedLoose {
    if (!(Test-Path -LiteralPath $loose)) { return }
    $resolved = (Resolve-Path -LiteralPath $loose).Path
    if ($resolved -ne (Join-Path (Resolve-Path -LiteralPath (Split-Path -Parent $archive)).Path 'MRZ_IntegraJ_00')) {
        throw 'Unexpected loose-test path.'
    }
    if (!(Test-Path -LiteralPath $looseReceipt) -or
        (Get-Content -Raw -LiteralPath $looseReceipt).Trim() -ne (Get-TreeReceipt $loose)) {
        throw 'Loose-test directory changed. Inspect before rollback.'
    }
    Assert-Closed
    $retired = Join-Path (Resolve-Path -LiteralPath $scratch).Path ('loose-retired-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::Move($resolved,$retired)
}

Assert-Closed
$exe = Join-Path $game 'ForzaHorizon4.exe'
foreach ($path in @($game, (Join-Path $game 'media'), (Split-Path -Parent $db),
                    (Split-Path -Parent $archive), $db, $manifest, $donor, $exe)) {
    Assert-Ordinary $path
}
Assert-Hash $exe $expected.exe
Assert-Hash $donor $expected.donor
$baseDb = Join-Path $backup 'gamedbRC.slt'
$baseManifest = Join-Path $backup 'zipmanifest.xml'
$baseDonor = Join-Path $backup 'ACU_IntegraR_01.zip'
$haveArchive = Test-Path -LiteralPath $archive
if ($haveArchive) {
    Assert-Ordinary $archive
    Assert-KnownAlias
}
if ((Hash-File $db) -notin @($expected.db, $expected.aliasDb) -or
    (Hash-File $manifest) -notin @($expected.manifest, $expected.aliasManifest, $expected.entryManifest)) {
    throw 'Unknown GameDB or manifest. Restore from a known baseline before deployment.'
}
$isStock = !$haveArchive -and (Hash-File $db) -eq $expected.db -and
          (Hash-File $manifest) -eq $expected.manifest
if (!(Test-Path -LiteralPath $backup)) {
    if (!$isStock -or !$SaveDirectory -or $Mode -eq 'restore') {
        throw 'First deployment needs an intact Phase 2 pair and a designated offline test-save path.'
    }
    $save = (Resolve-Path -LiteralPath $SaveDirectory).Path
    Assert-Ordinary $save
    $tempBackup = Join-Path $scratch ('backup-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $tempBackup
    foreach ($source in @($db,$manifest,$donor)) {
        $dest = Join-Path $tempBackup (Split-Path -Leaf $source)
        [IO.File]::Copy($source,$dest,$false)
        Assert-Hash $dest (Hash-File $source)
    }
    $files = @(Get-ChildItem -LiteralPath $save -File -Recurse -Force)
    if (!$files.Count) { throw 'Test-save directory is empty.' }
    foreach ($file in $files) {
        Assert-Ordinary $file.FullName
        $dest = Join-Path (Join-Path $tempBackup 'test-save') ([IO.Path]::GetRelativePath($save,$file.FullName))
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force
        $hash = Hash-File $file.FullName
        [IO.File]::Copy($file.FullName,$dest,$false)
        Assert-Hash $dest $hash
        Assert-Hash $file.FullName $hash
    }
    [IO.Directory]::Move($tempBackup,$backup)
    "Verified test-save backup files: $($files.Count)"
}
Assert-Hash $baseDb $expected.db
Assert-Hash $baseManifest $expected.manifest
Assert-Hash $baseDonor $expected.donor

function Restore-Phase2 {
    Move-VerifiedLoose
    Place-File $baseDb $db $expected.db
    Place-File $baseManifest $manifest $expected.manifest
    Remove-VerifiedAlias
}

if ($Mode -eq 'restore') {
    Restore-Phase2
} else {
    $newZip = Join-Path $candidate $alias
    $newManifest = Join-Path $candidate 'zipmanifest.xml'
    $newDb = Join-Path $candidate 'gamedbRC.slt'
    Assert-Hash $newZip $deployZip
    Assert-Hash $newManifest $deployManifest
    Assert-Hash $newDb $expected.aliasDb
    try {
        Move-VerifiedLoose
        Place-File $newZip $archive $deployZip
        Place-File $newManifest $manifest $deployManifest
        if ($Mode -notin @('mount-only','matrix-mount')) {
            Place-File $newDb $db $expected.aliasDb
        } else {
            Place-File $baseDb $db $expected.db
        }
        if ($Mode -eq 'loose') {
            $stagedLoose = Join-Path $scratch ('loose-staging-' + [guid]::NewGuid().ToString('N'))
            Expand-Archive -LiteralPath $newZip -DestinationPath $stagedLoose
            if (!(Test-Path -LiteralPath (Join-Path $stagedLoose 'MRZ_IntegraJ_00.carbin'))) {
                throw 'Loose-test scene missing.'
            }
            Set-Content -LiteralPath $looseReceipt -Value (Get-TreeReceipt $stagedLoose)
            Assert-Closed
            [IO.Directory]::Move($stagedLoose,$loose)
        }
    } catch {
        Restore-Phase2
        throw
    }
}
Assert-Hash $donor $expected.donor
Get-FileHash -LiteralPath @($db,$manifest,$donor)
if (Test-Path -LiteralPath $archive) { Get-FileHash -LiteralPath $archive }
