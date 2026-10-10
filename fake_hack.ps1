$Host.UI.RawUI.BackgroundColor = "Black"
$Host.UI.RawUI.ForegroundColor = "Green"
Clear-Host
$Host.UI.RawUI.WindowTitle = "SYSTEM32"

function Type-Line($text, $delay=15) {
    foreach ($c in $text.ToCharArray()) {
        Write-Host -NoNewline $c
        Start-Sleep -Milliseconds $delay
    }
    Write-Host ""
}

Type-Line "INITIALIZING BREACH PROTOCOL..."
Start-Sleep -Milliseconds 500
Type-Line "CONNECTING TO REMOTE HOST... [OK]"
Start-Sleep -Milliseconds 300
Type-Line "BYPASSING FIREWALL..."
Start-Sleep -Milliseconds 800
Type-Line "ACCESS GRANTED."
Start-Sleep -Milliseconds 500
Write-Host ""

$chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
for ($i = 0; $i -lt 400; $i++) {
    $line = -join ((1..(Get-Random -Minimum 40 -Maximum 90)) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Write-Host $line
    Start-Sleep -Milliseconds 15
}

Write-Host ""
Type-Line "DOWNLOADING FILES: [====================] 100%"
Start-Sleep -Milliseconds 400
Type-Line "TARGET SYSTEM COMPROMISED."
Start-Sleep -Milliseconds 400
Write-Host ""
Type-Line "just kidding :) - nothing actually happened to your pc" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
