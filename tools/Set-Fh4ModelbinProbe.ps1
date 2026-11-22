param(
    [Parameter(Mandatory)]
    [ValidateSet('aero','repack','append','bind','geometry','lookup','duplicate',
                 'append-fixed','lookup-fixed','bind-fixed','geometry-fixed','restore')]
    [string]$Mode,
    [string]$SaveDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = Join-Path $root 'game/ForzaHorizon4'
$scratch = Join-Path $root '.scratch/phase3_3'
$base = Join-Path $root '.scratch/phase3/phase2-baseline'
$zipName = 'ACU_IntegraR_01.zip'
$manifestName = 'zipmanifest.xml'
$zip = Join-Path $game "media/cars/$zipName"
$manifest = Join-Path $game "media/$manifestName"
$db = Join-Path $game 'media/stripped/gamedbRC.slt'
$baseZip = 'a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c'
$baseManifest = 'eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703'
$baseDb = 'dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8'

function Assert-Closed {
    if (Get-Process ForzaHorizon4 -ErrorAction SilentlyContinue) { throw 'Close FH4 to switch fixtures.' }
}
function Hash-File([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
function Assert-Hash([string]$path, [string]$expected) {
    if ((Hash-File $path) -ne $expected) { throw "SHA-256 mismatch: $path" }
}
function Assert-Ordinary([string]$path) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unexpected link: $path"
    }
}
function Install-File([string]$source, [string]$target, [string]$hash) {
    if ((Hash-File $target) -eq $hash) { return }
    $stage = Join-Path (Split-Path -Parent $target) ('.fh4-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::Copy($source, $stage, $false)
        Assert-Hash $stage $hash
        Assert-Closed
        [IO.File]::Move($stage, $target, $true)
        Assert-Hash $target $hash
    } finally {
        if ([IO.File]::Exists($stage)) { [IO.File]::Delete($stage) }
    }
}

Assert-Closed
foreach ($path in @($game, (Join-Path $game 'media'), (Split-Path -Parent $zip),
                    (Split-Path -Parent $db), $zip, $manifest, $db)) {
    Assert-Ordinary $path
}
Assert-Hash $db $baseDb
Assert-Hash (Join-Path $base $zipName) $baseZip
Assert-Hash (Join-Path $base $manifestName) $baseManifest
Assert-Hash (Join-Path $base 'gamedbRC.slt') $baseDb
$profiles = @{
    restore = @{ ZipPath = (Join-Path $base $zipName); ZipHash = $baseZip
                 ManifestPath = (Join-Path $base $manifestName); ManifestHash = $baseManifest }
}
foreach ($variant in @('aero','repack','append','bind','geometry','lookup','duplicate',
                       'append-fixed','lookup-fixed','bind-fixed','geometry-fixed')) {
    $folder = Join-Path $scratch "fixtures/$variant"
    $receipt = Join-Path $folder 'build.json'
    if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) { continue }
    $item = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($item.stage -ne $variant -or $item.status -ne 'OFFLINE_VALIDATED' -or
        $item.zip_sha256 -notmatch '^[a-f0-9]{64}$') { throw "Invalid fixture receipt: $receipt" }
    $profile = $profiles.restore.Clone()
    $profile.ZipPath = Join-Path $folder $zipName
    $profile.ZipHash = $item.zip_sha256
    if ($variant -notin @('aero','repack')) {
        if ($item.encrypted_manifest_sha256 -notmatch '^[a-f0-9]{64}$') { continue }
        $profile.ManifestPath = Join-Path $folder $manifestName
        $profile.ManifestHash = $item.encrypted_manifest_sha256
    }
    $profiles[$variant] = $profile
}
if (!$profiles.ContainsKey($Mode)) { throw "Fixture is not ready: $Mode" }
$oldZipHash = Hash-File $zip
$oldManifestHash = Hash-File $manifest
$previous = $profiles.Values | Where-Object {
    $_.ZipHash -eq $oldZipHash -and $_.ManifestHash -eq $oldManifestHash
} | Select-Object -First 1
if (!$previous) {
    if ($Mode -ne 'restore' -or $oldZipHash -notin $profiles.Values.ZipHash -or
        $oldManifestHash -notin $profiles.Values.ManifestHash) {
        throw 'Installed hashes do not match a known fixture pair.'
    }
    $previous = $profiles.restore
}
$next = $profiles[$Mode]
Assert-Hash $next.ZipPath $next.ZipHash
Assert-Hash $next.ManifestPath $next.ManifestHash
Assert-Hash $previous.ZipPath $previous.ZipHash
Assert-Hash $previous.ManifestPath $previous.ManifestHash

$receiptPath = Join-Path $scratch 'save-backup.json'
if ($Mode -ne 'restore' -and !(Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
    if (!$SaveDirectory -or $oldZipHash -ne $baseZip -or $oldManifestHash -ne $baseManifest) {
        throw 'Use a designated offline test-save directory for the first deployment from Phase 2.'
    }
    $save = (Resolve-Path -LiteralPath $SaveDirectory).Path
    Assert-Ordinary $save
    $files = @(Get-ChildItem -LiteralPath $save -File -Recurse -Force | Sort-Object FullName)
    if (!$files.Count) { throw 'Test-save directory is empty.' }
    $backupName = 'pre-test-save-' + [Guid]::NewGuid().ToString('N')
    $backup = Join-Path $scratch $backupName
    $records = foreach ($file in $files) {
        Assert-Closed
        Assert-Ordinary $file.FullName
        $relative = [IO.Path]::GetRelativePath($save, $file.FullName)
        $dest = Join-Path $backup $relative
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest)
        $hash = Hash-File $file.FullName
        [IO.File]::Copy($file.FullName, $dest, $false)
        Assert-Hash $dest $hash
        Assert-Hash $file.FullName $hash
        @{ path = $relative; sha256 = $hash }
    }
    @{ folder = $backupName; files = @($records) } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath
}
if ($Mode -ne 'restore') {
    $saveReceipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($saveReceipt.folder -notmatch '^pre-test-save-[a-f0-9]{32}$' -or !$saveReceipt.files.Count) {
        throw 'Invalid local save backup receipt.'
    }
    $folder = Join-Path $scratch $saveReceipt.folder
    foreach ($record in $saveReceipt.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $folder $record.path))
        if (!$path.StartsWith($folder + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Backup receipt path is outside the backup directory.'
        }
        Assert-Hash $path $record.sha256
    }
}

try {
    Install-File $next.ZipPath $zip $next.ZipHash
    Install-File $next.ManifestPath $manifest $next.ManifestHash
    Assert-Hash $db $baseDb
} catch {
    Install-File $previous.ZipPath $zip $previous.ZipHash
    Install-File $previous.ManifestPath $manifest $previous.ManifestHash
    throw
}
Get-FileHash -LiteralPath $zip,$manifest,$db -Algorithm SHA256
