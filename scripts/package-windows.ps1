# Keep the launcher's display name independent of Explorer's file extension settings.
$ErrorActionPreference = 'Stop'
$dist = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist'
$bundle = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
$app = New-Item -ItemType Directory -Path (Join-Path $bundle 'app') -Force
try {
    $target = Join-Path $app.FullName 'MLK.gg.exe'
    Copy-Item -LiteralPath (Join-Path $dist 'MLK.gg.exe') -Destination $target
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $bundle 'MLK.gg.lnk'))
    $shortcut.TargetPath = $target
    $shortcut.Description = 'MLK.gg'
    $shortcut.Save()
    Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath (Join-Path $dist 'MLK.gg-Windows.zip') -Force
} finally {
    Remove-Item -LiteralPath $bundle -Recurse -Force
}
