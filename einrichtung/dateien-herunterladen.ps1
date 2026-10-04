# Lädt fehlende Installationsdateien von den offiziellen Quellen. Wird von einrichten.ps1 aufgerufen.
# Dateien: "platform-tools" oder der Anfang eines APK-Dateinamens, zum Beispiel "com.termux-".
param(
    [Parameter(Mandatory)] [string]$Ordner,
    [Parameter(Mandatory)] [string[]]$Dateien
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$agent = 'Ankerwache-Installer'

# Die gepatchte GPSLogger-Fassung liegt als Release-Datei im GitHub-Projekt; die gepatchte GPSLogger-Fassung als Release-Datei.
$gpsloggerGepatcht = 'https://github.com/paul-teumer/Ankerwache/releases/latest/download/com.mendhak.gpslogger-137-gepatcht.apk'

function Lade($url, $ziel) {
    Write-Host "  Lade $(Split-Path $ziel -Leaf) ..."
    New-Item -ItemType Directory -Force (Split-Path $ziel) | Out-Null
    Invoke-WebRequest $url -OutFile "$ziel.teil" -UserAgent $agent
    Move-Item "$ziel.teil" $ziel -Force
}

function VonFDroid($paket) {
    $code = (Invoke-RestMethod "https://f-droid.org/api/v1/packages/$paket" -UserAgent $agent).suggestedVersionCode
    Lade "https://f-droid.org/repo/${paket}_$code.apk" "$Ordner\apps\$paket-$code.apk"
}

foreach ($datei in $Dateien) {
    try {
        switch ($datei) {
            'platform-tools' {
                Write-Host '  Lade Android Platform-Tools von Google ...'
                $zip = Join-Path $env:TEMP 'platform-tools.zip'
                Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zip -UserAgent $agent
                Expand-Archive $zip -DestinationPath $Ordner -Force
                Remove-Item $zip
            }
            'com.mendhak.gpslogger-' { Lade $gpsloggerGepatcht "$Ordner\apps\com.mendhak.gpslogger-137-gepatcht.apk" }
            'rustdesk-' {
                $freigabe = Invoke-RestMethod 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest' -UserAgent $agent
                $apk = $freigabe.assets | Where-Object { $_.name -match '^rustdesk-[\d.]+-aarch64(-signed)?\.apk$' } | Select-Object -First 1
                if (-not $apk) { throw 'Keine arm64-Datei in der aktuellen Freigabe gefunden.' }
                Lade $apk.browser_download_url "$Ordner\apps\$($apk.name)"
            }
            default { VonFDroid $datei.TrimEnd('-') }
        }
    } catch {
        Write-Host "  $datei konnte nicht geladen werden: $($_.Exception.Message)" -ForegroundColor Yellow
        if ($datei -eq 'com.mendhak.gpslogger-') {
            Write-Host '  Die gepatchte GPSLogger-Fassung ist zwingend nötig (die Originalfassung legt das Telefon nach etwa einer Stunde lahm). Siehe README.md.' -ForegroundColor Yellow
        }
    }
}
