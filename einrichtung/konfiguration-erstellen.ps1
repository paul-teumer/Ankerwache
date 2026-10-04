# Fragt Telegram-Token und Rufnummern ab, ermittelt die Telegram-Gruppe selbst und schreibt konfiguration.json.
# Wird von einrichten.ps1 aufgerufen, wenn noch keine konfiguration.json existiert.
param([Parameter(Mandatory)] [string]$Ziel)
$ErrorActionPreference = 'Stop'
$vorlage = Join-Path (Split-Path $PSScriptRoot) 'programm\konfiguration.beispiel.json'

function Telegram($token, $methode, $zeitlimit = 15) {
    Invoke-RestMethod "https://api.telegram.org/bot$token/$methode" -TimeoutSec $zeitlimit
}

Write-Host "`nSchritt 1 von 4: Telegram-Bot anlegen" -ForegroundColor Cyan
Write-Host @'
  1. Telegram öffnen und nach "@BotFather" suchen (blaues Häkchen).
  2. Senden: /newbot  und einen Namen sowie einen Benutzernamen (muss auf "bot" enden) wählen.
  3. Senden: /setprivacy, den neuen Bot wählen, dann "Disable" wählen.
     (Sonst sieht der Bot die Befehle in der Gruppe nicht.)
  4. BotFather nennt einen Token, etwa 123456789:AAH... Diesen Token kopieren.
'@
while ($true) {
    $token = (Read-Host 'Token hier einfügen').Trim()
    try { $bot = (Telegram $token 'getMe').result; Write-Host "Bot gefunden: @$($bot.username)" -ForegroundColor Green; break }
    catch { Write-Host 'Dieser Token wird von Telegram nicht akzeptiert. Bitte erneut kopieren.' -ForegroundColor Yellow }
}

Write-Host "`nSchritt 2 von 4: Telegram-Gruppe" -ForegroundColor Cyan
Write-Host @"
  1. In Telegram eine neue Gruppe anlegen und @$($bot.username) sowie alle Mitsegler hinzufügen, die Befehle senden dürfen.
  2. In die Gruppe eine beliebige Nachricht schreiben, zum Beispiel "Hallo".
"@
Read-Host 'Eingabetaste drücken, sobald die Nachricht gesendet ist' | Out-Null
$gruppe = $null
for ($versuch = 0; $versuch -lt 6 -and -not $gruppe; $versuch++) {
    $chats = (Telegram $token 'getUpdates?timeout=10' 20).result | ForEach-Object {
        if ($_.message) { $_.message.chat } elseif ($_.my_chat_member) { $_.my_chat_member.chat }
    } | Where-Object { $_.type -in 'group', 'supergroup' }
    $gruppe = $chats | Select-Object -Last 1
}
if (-not $gruppe) { Write-Host 'Keine Gruppe gefunden. Ist der Bot in der Gruppe und wurde danach eine Nachricht gesendet? Bitte neu starten.' -ForegroundColor Red; exit 1 }
Write-Host "Gruppe gefunden: $($gruppe.title)" -ForegroundColor Green

Write-Host "`nSchritt 3 von 4: Rufnummern für SMS" -ForegroundColor Cyan
Write-Host '  Handynummern, die Alarme per SMS erhalten und Befehle per SMS senden dürfen, mit Ländervorwahl, getrennt durch Komma.'
Write-Host '  Beispiel: +491701234567, +491761234567'
while ($true) {
    $nummern = @((Read-Host 'Rufnummern') -split '[,;]' | ForEach-Object { ($_ -replace '[\s/-]', '') -replace '^00', '+' } | Where-Object { $_ })
    if ($nummern.Count -gt 0 -and -not ($nummern | Where-Object { $_ -notmatch '^\+\d{8,15}$' })) { break }
    Write-Host 'Bitte jede Nummer mit +Ländervorwahl angeben, zum Beispiel +491701234567.' -ForegroundColor Yellow
}

Write-Host "`nSchritt 4 von 4: Meldungen" -ForegroundColor Cyan
Write-Host @'
  Der Alarm bei Ankerdrift kommt immer per SMS und Telegram. Übrige Meldungen (Lebenszeichen, Start, Warnungen) wahlweise:
    1 = per Telegram (empfohlen; keine SMS-Kosten)
    2 = per SMS (kostet je Meldung und Empfänger eine SMS; funktioniert ohne mobile Daten)
    3 = auf beiden Wegen
'@
$meldeweg = @{ '2' = 'sms'; '3' = 'beide' }[(Read-Host 'Ihre Wahl (Enter = 1)').Trim()]
Write-Host @'
  Wie oft soll sich die Ankerwache mit einer Statusmeldung melden ("Lebenszeichen")?
    1 = täglich (empfohlen)   2 = wöchentlich (montags)   3 = monatlich (am 1.)
'@
$häufigkeit = @{ '2' = 'wöchentlich'; '3' = 'monatlich' }[(Read-Host 'Ihre Wahl (Enter = 1)').Trim()]

$konfiguration = Get-Content $vorlage -Raw -Encoding UTF8 | ConvertFrom-Json
$konfiguration.telegramToken = $token
$konfiguration.telegramGruppe = [int64]$gruppe.id
$konfiguration.'smsEmpfänger' = $nummern
if ($meldeweg) { $konfiguration.meldeweg = $meldeweg }
if ($häufigkeit) { $konfiguration.'lebenszeichenHäufigkeit' = $häufigkeit }
[IO.File]::WriteAllText($Ziel, ($konfiguration | ConvertTo-Json -Depth 3), (New-Object Text.UTF8Encoding $false))
Write-Host "`nkonfiguration.json gespeichert." -ForegroundColor Green
