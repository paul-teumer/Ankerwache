# Richtet ein per USB angeschlossenes Android-Telefon (USB-Debugging an, arm64) vollständig als Ankerwache ein.
# Aufruf: Rechtsklick > "Mit PowerShell ausführen"
$ErrorActionPreference = 'Continue'
$hier = $PSScriptRoot
$projekt = Split-Path $hier
$lokal = "$projekt\lokal"
$adb = "$lokal\platform-tools\adb.exe"

function Schritt($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Abbruch($text) { Write-Host $text -ForegroundColor Red; Read-Host 'Eingabetaste zum Schließen'; exit 1 }

Schritt 'Auswahl: was soll eingerichtet werden?'
Write-Host @'
Die Ankerwache selbst (Termux, Termux:API, Termux:Boot, GPSLogger) wird immer eingerichtet.
Alles Weitere ist wählbar. Bereits vorhandene Apps und Daten auf dem Telefon bleiben unberührt,
sofern bei einem Punkt nicht ausdrücklich etwas anderes steht.

  1 = Empfohlene Einstellungen übernehmen (keine weiteren Fragen)
  2 = Jeden Punkt einzeln auswählen
'@
$schnell = (Read-Host 'Ihre Wahl (1 oder 2, Enter = 1)') -ne '2'

function Frage($titel, $erklärung, $empfohlen) {
    Write-Host "`n$titel" -ForegroundColor Cyan
    Write-Host "  $erklärung"
    $empfehlung = if ($empfohlen) { 'Ja' } else { 'Nein' }
    if ($script:schnell) { Write-Host "  -> Empfehlung übernommen: $empfehlung"; return $empfohlen }
    $antwort = Read-Host "  Möchten Sie das? (j = Ja, n = Nein, Enter = Empfehlung: $empfehlung)"
    if (-not $antwort) { return $empfohlen }
    return $antwort -match '^[jJyY]'
}

$fernzugriffApps = @(
    @{ Datei = 'com.tailscale.ipn-'; Titel = 'Tailscale (sichere Verbindung zum Telefon aus der Ferne)'; Empfohlen = $true
       Text = 'Verbindet Telefon und Rechner verschlüsselt, auch im Mobilfunknetz; Voraussetzung für Fernzugriff per SSH oder RustDesk. Erfordert ein kostenloses Tailscale-Konto und braucht etwas mobile Daten.' },
    @{ Datei = 'rustdesk-'; Titel = 'RustDesk (Telefonbildschirm auf dem Rechner sehen und bedienen)'; Empfohlen = $false
       Text = 'Nur zusammen mit Tailscale sinnvoll. Braucht viele mobile Daten; die Ankerwache schaltet es im Normalzustand aus (Befehl FERNZUGRIFF EIN/AUS).' }
)
$zusatzApps = @(
    @{ Datei = 'org.fdroid.fdroid-'; Titel = 'F-Droid (App-Laden für freie Software)'
       Text = 'Damit lassen sich Termux und die zugehörigen Apps später aktualisieren. Klein und unauffällig.' },
    @{ Datei = 'com.aurora.store-'; Titel = 'Aurora Store (Play-Store-Ersatz ohne Google-Konto)'
       Text = 'Nur nötig, wenn Sie später weitere Apps aus dem Play Store auf dem Telefon installieren möchten.' },
    @{ Datei = 'com.android.gpstest.osmdroid-'; Titel = 'GPSTest (Satellitenanzeige)'
       Text = 'Zeigt, wie viele Satelliten das Telefon empfängt. Hilfreich zum Prüfen des GPS-Empfangs, für den Betrieb nicht nötig.' }
)
$wahl = @{}
$wahl.Apps = @('com.termux-', 'com.termux.api-', 'com.termux.boot-', 'com.mendhak.gpslogger-')
$wahl.Ssh = $false
if (Frage 'Fernzugriff auf das Telefon einrichten' 'Erlaubt, das Telefon aus der Ferne zu erreichen, zum Beispiel um Protokolle zu lesen oder den Bildschirm zu sehen. Für die Ankerwache selbst nicht nötig. Bei Ja folgen die einzelnen Bausteine.' $false) {
    foreach ($app in $fernzugriffApps) {
        if (Frage "Fernzugriff: $($app.Titel)" $app.Text $app.Empfohlen) { $wahl.Apps += $app.Datei }
    }
    $wahl.Ssh = Frage 'Fernzugriff: SSH (Textbefehle vom Rechner aus)' 'Erlaubt, sich vom Rechner aus auf dem Telefon anzumelden. Sehr datensparsam, nur mit Schlüsseldatei und ohne Passwort. Für den Zugriff aus der Ferne wird Tailscale benötigt.' $true
}
foreach ($app in $zusatzApps) {
    if (Frage "Zusätzliche nützliche App (nicht für die Ankerwache nötig): $($app.Titel)" $app.Text $false) { $wahl.Apps += $app.Datei }
}
$wahl.AdbLokal = Frage 'Mobile Daten per SMS-Befehl schalten und GPS-Dienst selbst reparieren' `
    'Die Ankerwache kann dann auf Befehl (DATEN AUS / DATEN EIN) mobile Daten abschalten und einen hängenden GPS-Dienst neu starten. Dafür wird die Entwickler-Schnittstelle ADB im Telefon selbst aktiviert. Sie gilt nur bis zum nächsten Neustart des Telefons (danach einmal per USB erneuern) und ist nur nach einer Bestätigung am Telefonbildschirm nutzbar. Ohne diesen Punkt funktioniert der Alarm trotzdem, aber ohne diese beiden Komfortfunktionen.' $true
$wahl.Datensparmodus = Frage 'Datensparmodus: Hintergrunddaten nur für die Ankerwache' `
    'Alle anderen Apps dürfen dann im Hintergrund keine mobilen Daten mehr verbrauchen (Android-Datensparmodus). Sinnvoll, wenn das Telefon nur als Ankerwache dient und das Datenvolumen knapp ist. Andere Apps, zum Beispiel WhatsApp, erhalten dann nur noch Nachrichten über WLAN oder bei geöffneter App.' $false
$wahl.AppsDeaktivieren = Frage 'Vorinstallierte, nicht benötigte Apps deaktivieren' `
    'Deaktiviert Apps aus der Liste nicht-benötigte-apps.txt (zum Beispiel Facebook-Dienste, Druckdienste, Google-Sprachausgabe). Das spart Akku und Rechenleistung. Die Apps werden nicht gelöscht und lassen sich jederzeit unter Einstellungen > Apps wieder aktivieren. Wählen Sie Nein, wenn das Telefon auch anders genutzt wird.' $false
if ($wahl.Ssh -and -not (Test-Path "$lokal\ssh-schlüssel.pub")) {
    $schlüsselDatei = "$env:USERPROFILE\.ssh\ankerwache_ed25519"
    if (-not (Test-Path "$schlüsselDatei.pub")) {
        New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null
        ssh-keygen -q -t ed25519 -N '""' -C ankerwache -f $schlüsselDatei
    }
    if (Test-Path "$schlüsselDatei.pub") {
        Copy-Item "$schlüsselDatei.pub" "$lokal\ssh-schlüssel.pub"
        Write-Host "SSH-Schlüssel erzeugt: $schlüsselDatei (privat, nicht weitergeben)" -ForegroundColor Green
    } else {
        Write-Host 'Der SSH-Schlüssel konnte nicht erzeugt werden; SSH wird übersprungen.' -ForegroundColor Yellow
        $wahl.Ssh = $false
    }
}

Schritt 'Dateien besorgen'
$fehlendeDateien = @($wahl.Apps | Where-Object { -not (Get-ChildItem "$lokal\apps\$_*.apk" -ErrorAction SilentlyContinue) })
if (-not (Test-Path $adb)) { $fehlendeDateien += 'platform-tools' }
if ($fehlendeDateien) {
    Write-Host "Es fehlen Installationsdateien: $($fehlendeDateien -join ', ')"
    if (Frage 'Fehlende Dateien jetzt aus dem Internet laden' 'Die Dateien kommen von den offiziellen Quellen (f-droid.org, dl.google.com, github.com). Der Rechner braucht dafür Internet; je nach Auswahl sind es einige hundert Megabyte, das dauert einige Minuten. Bei Nein müssen Sie die Dateien selbst in die Ordner legen (siehe README.md).' $true) {
        & "$hier\dateien-herunterladen.ps1" -Ordner $lokal -Dateien $fehlendeDateien
    }
}

Schritt 'Vorbereitung prüfen'
$fehlend = @()
if (-not (Test-Path $adb)) { $fehlend += 'lokal\platform-tools (Android Platform-Tools von Google)' }
foreach ($pflicht in 'com.termux-', 'com.termux.api-', 'com.termux.boot-', 'com.mendhak.gpslogger-') {
    if (-not (Get-ChildItem "$lokal\apps\$pflicht*.apk" -ErrorAction SilentlyContinue)) { $fehlend += "lokal\apps\$pflicht*.apk" }
}
if ($fehlend) { Abbruch "Es fehlen:`n  $($fehlend -join "`n  ")`nSiehe README.md, Abschnitt `"Wenn etwas nicht klappt`"." }
$konfigurationsdatei = "$lokal\konfiguration.json"
if (-not (Test-Path $konfigurationsdatei) -or (Get-Content $konfigurationsdatei -Raw) -match 'HIER-TOKEN') {
    & "$hier\konfiguration-erstellen.ps1" -Ziel $konfigurationsdatei
    if (-not (Test-Path $konfigurationsdatei)) { Abbruch 'Ohne konfiguration.json ist keine Einrichtung möglich.' }
}

Schritt 'Gerät suchen'
& $adb start-server | Out-Null
$geräte = @((& $adb devices) -match '\tdevice$' | ForEach-Object { ($_ -split '\t')[0] })
if ($geräte.Count -eq 0) { Abbruch 'Kein Gerät. USB-Kabel anschließen, USB-Debugging einschalten und den Zugriff am Telefon erlauben.' }
if ($args.Count -gt 0) { $env:ANDROID_SERIAL = $args[0] }
elseif ($geräte.Count -gt 1) { Abbruch "Mehrere Geräte angeschlossen, nur eines anschließen: $($geräte -join ', ')" }
Write-Host "$(& $adb shell getprop ro.product.model), Android $(& $adb shell getprop ro.build.version.release), $(& $adb shell getprop ro.product.cpu.abi)"
& $adb shell svc power stayon usb

Schritt 'Apps installieren'
foreach ($name in $wahl.Apps) {
    $apk = Get-ChildItem "$lokal\apps\$name*.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $apk) { Write-Host "$name*.apk nicht vorhanden, übersprungen"; continue }
    Write-Host "$($apk.Name): $(& $adb install -r $apk.FullName 2>&1 | Select-Object -Last 1)"
}

$huaweiPakete = @('com.huawei.powergenie', 'com.huawei.android.hwaps') | Where-Object { & $adb shell pm list packages $_ }
if ($huaweiPakete) {
    Schritt 'Huawei-Energiedienst entfernen'
    $entfernen = Frage 'Huawei/Honor-Energiesparmodus-Dienst entfernen' `
        'Auf Huawei- und Honor-Telefonen beendet der Energiedienst PowerGenie Apps im Hintergrund, auch die Ankerwache; der Alarm bliebe dann aus. Die Systemkomponente wird für Ihren Benutzer entfernt und lässt sich per ADB-Befehl wiederherstellen (siehe Bauanleitung). Ohne diesen Schritt ist die Ankerwache auf diesem Telefon nicht zuverlässig.' $true
    if ($entfernen) { foreach ($paket in $huaweiPakete) { & $adb shell pm uninstall -k --user 0 $paket } }
}

if ($wahl.AppsDeaktivieren) {
    Schritt 'Nicht benötigte Apps deaktivieren (spart mobile Daten und Rechenlast)'
    $vorhanden = & $adb shell pm list packages -e
    foreach ($paket in Get-Content "$hier\nicht-benötigte-apps.txt" -Encoding UTF8 | Where-Object { $_ -and -not $_.StartsWith('#') }) {
        if ($vorhanden -contains "package:$paket") { & $adb shell pm disable-user --user 0 $paket | Out-Null }
    }
}

Schritt 'Berechtigungen und Akkuoptimierung'
foreach ($recht in 'ACCESS_FINE_LOCATION', 'ACCESS_COARSE_LOCATION', 'ACCESS_BACKGROUND_LOCATION', 'SEND_SMS', 'READ_SMS', 'RECEIVE_SMS', 'READ_PHONE_STATE') {
    & $adb shell pm grant com.termux.api android.permission.$recht 2>$null
}
foreach ($recht in 'READ_EXTERNAL_STORAGE', 'WRITE_EXTERNAL_STORAGE') { & $adb shell pm grant com.termux android.permission.$recht 2>$null }
foreach ($paket in 'com.termux', 'com.termux.api', 'com.termux.boot') {
    & $adb shell dumpsys deviceidle whitelist +$paket | Out-Null
    & $adb shell cmd appops set $paket RUN_IN_BACKGROUND allow
}
& $adb shell settings put secure location_mode 3
# Ab Android 8 erhält Termux:API im Hintergrund sonst nur alle 10 Minuten eine Position.
& $adb shell settings put global location_background_throttle_package_whitelist com.termux.api
# Ab Android 12 beendet das System sonst Hintergrundprozesse von Termux.
& $adb shell device_config set_sync_mode persistent
& $adb shell device_config put activity_manager max_phantom_processes 2147483647
& $adb shell settings put global settings_enable_monitor_phantom_procs false

if ($wahl.Datensparmodus) {
    Schritt 'Datensparmodus: im Hintergrund nur Termux, RustDesk und Tailscale'
    & $adb shell cmd netpolicy set restrict-background true
    foreach ($paket in 'com.termux', 'com.carriez.flutter_hbb', 'com.tailscale.ipn') {
        $uid = ((& $adb shell dumpsys package $paket) -match 'userId=(\d+)' | Select-Object -First 1) -replace '.*userId=(\d+).*', '$1'
        if ($uid) { & $adb shell cmd netpolicy add restrict-background-whitelist $uid }
    }
}

Schritt 'GPSLogger einrichten'
foreach ($recht in 'ACCESS_FINE_LOCATION', 'ACCESS_COARSE_LOCATION', 'READ_EXTERNAL_STORAGE', 'WRITE_EXTERNAL_STORAGE') {
    & $adb shell pm grant com.mendhak.gpslogger android.permission.$recht 2>$null
}
& $adb shell dumpsys deviceidle whitelist +com.mendhak.gpslogger | Out-Null
# GPSLogger übernimmt ein Profil nur von einer http-Adresse; adb reverse leitet sie vom Telefon zum Rechner.
$profil = [IO.File]::ReadAllBytes("$hier\gpslogger-patch\Standardprofil.properties")
$server = [Net.HttpListener]::new()
$server.Prefixes.Add('http://localhost:8765/')
$server.Start()
& $adb reverse tcp:8765 tcp:8765 | Out-Null
$anfrage = $server.GetContextAsync()
& $adb shell am start -a android.intent.action.VIEW -d http://localhost:8765/Standardprofil.properties -n com.mendhak.gpslogger/.ProfileLinkReceiverActivity | Out-Null
if ($anfrage.Wait(30000)) {
    $antwort = $anfrage.Result.Response
    $antwort.OutputStream.Write($profil, 0, $profil.Length)
    $antwort.Close()
} else { Write-Host 'GPSLogger hat das Profil nicht abgerufen; Einstellungen von Hand nach gpslogger-patch\Standardprofil.properties setzen.' }
Start-Sleep 3
$server.Stop()
& $adb reverse --remove tcp:8765
& $adb shell am start-foreground-service -n com.mendhak.gpslogger/.GpsLoggingService --ez immediatestart true | Out-Null

Schritt 'Dateien kopieren'
& $adb shell rm -rf /sdcard/Download/ankerwache
& $adb shell mkdir -p /sdcard/Download/ankerwache
$dateien = "$projekt\programm\ankerwache.py", "$lokal\konfiguration.json", "$projekt\programm\start-ankerwache.sh",
    "$projekt\programm\halte-ankerwache-am-laufen.sh", "$hier\einrichten.sh", "$lokal\pakete"
if ($wahl.Ssh) { $dateien += "$lokal\ssh-schlüssel.pub" }
foreach ($datei in $dateien) {
    if (-not (Test-Path $datei)) { continue }
    & $adb push $datei \sdcard\Download\ankerwache\ | Select-Object -Last 1
}
$optionen = if (-not $wahl.AdbLokal) { 'ohne-adb' }
& $adb shell "echo 'cd /sdcard/Download/ankerwache && sh einrichten.sh $optionen 2>&1 | tee /sdcard/Download/einrichtung.txt' > /sdcard/Download/los.sh; rm -f /sdcard/Download/einrichtung.txt"

Schritt 'Termux:Boot aktivieren und Termux starten'
& $adb shell am start -n com.termux.boot/.BootActivity | Out-Null
Start-Sleep 3
& $adb shell am start -n com.termux/.app.TermuxActivity | Out-Null
Write-Host 'Termux entpackt beim ersten Start seine Grundausstattung, bitte warten.'
Start-Sleep 30

Schritt 'Einrichtung in Termux'
& $adb shell input text 'sh%s/sdcard/Download/los.sh'
& $adb shell input keyevent 66
for ($sekunden = 0; $sekunden -lt 900; $sekunden += 10) {
    Start-Sleep 10
    $protokoll = (& $adb shell 'cat /sdcard/Download/einrichtung.txt 2>/dev/null') -join "`n"
    if ($protokoll -match 'Einrichtung fertig') { break }
}
$protokoll -split "`n" | Select-Object -Last 8
if ($protokoll -match 'Einrichtung fertig') {
    Write-Host "`nFertig. In der Telegram-Gruppe muss 'Ankerwache gestartet' erscheinen." -ForegroundColor Green
    # Der Telegram-Token darf nicht im frei lesbaren Download-Ordner des Telefons bleiben.
    & $adb shell rm -f /sdcard/Download/ankerwache/konfiguration.json
} else {
    Write-Host "`nNicht fertig geworden. Termux am Telefon ansehen und notfalls dort eingeben: sh /sdcard/Download/los.sh" -ForegroundColor Yellow
}

if ($wahl.AdbLokal) {
    Schritt 'ADB über localhost für DATEN EIN/AUS ohne Root'
    # Termux verbindet sich kurz nach dem Umschalten mit dem eigenen ADB; das Telefon fragt dann einmalig nach dem Schlüssel.
    & $adb shell "echo 'sleep 5; adb connect 127.0.0.1:5555' > /sdcard/Download/adb-verbinden.sh"
    & $adb shell input text 'sh%s/sdcard/Download/adb-verbinden.sh'
    & $adb shell input keyevent 66
    & $adb tcpip 5555
    Write-Host 'Am Telefon "USB-Debugging zulassen?" mit "Immer zulassen" bestätigen. Nach jedem Neustart des Telefons erneut per USB: adb tcpip 5555'
}
Write-Host "`nWICHTIG: Jeder Hersteller (Samsung, Xiaomi, Huawei, Honor, Oppo, OnePlus und andere) bringt eigene Energiespar- und Hintergrundregeln mit, die Apps ohne Warnung beenden können. Prüfen Sie deshalb auf Ihrem Telefon, dass für Termux, Termux:API, Termux:Boot und GPSLogger alle Energiesparoptionen ausgeschaltet sind (Akku „Nicht eingeschränkt“, Autostart und Hintergrundbetrieb erlaubt, App in der Übersicht gesperrt), und testen Sie den Alarm über Nacht. Bei Huawei und Honor: Einstellungen > Akku > App-Start, die vier Apps auf manuell mit allen Schaltern ein." -ForegroundColor Yellow
Read-Host 'Eingabetaste zum Schließen'
