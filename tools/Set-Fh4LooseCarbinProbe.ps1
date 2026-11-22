param([Parameter(Mandatory)] [ValidateSet('install','restore')] [string]$Mode)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = (Resolve-Path -LiteralPath (Join-Path $root 'game/ForzaHorizon4')).Path
$dirs = @('mediapc','mediapc/cars','mediapc/cars/MRZ_IntegraJ_00') |
    ForEach-Object { Join-Path $game $_ }
$target = Join-Path $dirs[-1] 'MRZ_IntegraJ_00.carbin'
$scratch = Join-Path $root '.scratch/phase3_1/io'
$receipt = Join-Path $scratch 'loose-carbin-install.json'
$expected = '02122240cf5cf5e86f2e94d19e6794330fafab4452e44d1c62da365d92309a90'
$inputs = [ordered]@{
    'media/stripped/gamedbRC.slt' = 'd6d69fde7e08375ad1ee4e9881314f44e77770602f1d433220d02a2e66ea90f7'
    'media/zipmanifest.xml' = '685060bd016bcf7daf217ff6ad663d3984b58432071fd379cd42c5ca09a2e967'
    'media/cars/MRZ_IntegraJ_00.zip' = 'b1638555b113cb025fa950e44ea962244af7dacf28c794a9e95182560df65e44'
    'media/cars/ACU_IntegraR_01.zip' = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
}
function Assert-Closed {
    if (Get-Process ForzaHorizon4 -ErrorAction SilentlyContinue) { throw 'Close FH4 first.' }
}
function Assert-Hash([string]$Path, [string]$Hash) {
    if ((Get-FileHash -LiteralPath $Path).Hash -ne $Hash) { throw "Unexpected input: $Path" }
}
function Assert-Ordinary([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and
        ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Unexpected link: $Path"
    }
}
Assert-Closed
foreach ($path in @($game) + $dirs + @($target)) { Assert-Ordinary $path }

if ($Mode -eq 'restore') {
    $state = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($state.Target -ne $target) { throw 'Probe receipt path mismatch.' }
    Assert-Hash $target $expected
    [IO.File]::Delete($target)
    for ($i = $dirs.Count - 1; $i -ge 0; $i--) {
        if ($dirs[$i] -in $state.Created -and (Test-Path -LiteralPath $dirs[$i]) -and
            -not (Get-ChildItem -LiteralPath $dirs[$i] -Force | Select-Object -First 1)) {
            [IO.Directory]::Delete($dirs[$i],$false)
        }
    }
    Remove-Item -LiteralPath $receipt
    return
}

if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $receipt)) {
    throw 'Probe path or receipt already exists.'
}
foreach ($name in $inputs.Keys) { Assert-Hash (Join-Path $game $name) $inputs[$name] }
$null = New-Item -ItemType Directory -Path $scratch -Force
$temp = Join-Path $scratch ('carbin-' + [guid]::NewGuid().ToString('N'))
$archive = [IO.Compression.ZipFile]::OpenRead((Join-Path $game 'media/cars/MRZ_IntegraJ_00.zip'))
try {
    $entry = $archive.GetEntry('MRZ_IntegraJ_00.carbin')
    if (!$entry -or $entry.Length -ne 45204) { throw 'Unexpected ZIP member.' }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$temp,$false)
} finally {
    $archive.Dispose()
}
$created = @($dirs | Where-Object { !(Test-Path -LiteralPath $_) })
try {
    Assert-Hash $temp $expected
    Assert-Closed
    $null = New-Item -ItemType Directory -Path $dirs[-1] -Force
    [IO.File]::Move($temp,$target,$false)
    @{ Target = $target; Created = $created; SHA256 = $expected } | ConvertTo-Json |
        Set-Content -LiteralPath $receipt
    Assert-Hash $target $expected
    foreach ($name in $inputs.Keys) { Assert-Hash (Join-Path $game $name) $inputs[$name] }
} finally {
    if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
}
Get-FileHash -LiteralPath $target
