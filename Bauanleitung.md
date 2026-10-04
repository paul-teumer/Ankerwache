# Ankerwache – Einrichtung auf dem Bordgerät

Python-Programm unter Termux. Alarm per SMS und Telegram. Befehle per Telegram-Gruppe oder per SMS; Lebenszeichen, Startmeldung und Warnungen wahlweise per Telegram, SMS oder beidem. Mit `DATEN AUS` läuft die Ankerwache vollständig ohne mobile Daten.

| Pfad | Zweck |
|---|---|
| `programm/ankerwache.py` | Programm |
| `programm/konfiguration.beispiel.json` | Vorlage für `konfiguration.json` (Token, Gruppe, SMS-Empfänger, Grenzwerte); der Assistent füllt sie aus und legt das Ergebnis in `lokal/` ab |
| `programm/start-ankerwache.sh` | Hält das Gerät wach und startet das Programm nach einem Absturz neu, läuft beim Systemstart |
| `programm/halte-ankerwache-am-laufen.sh` | Wird von Android alle 15 Minuten gestartet und startet die Ankerwache, falls sie nicht läuft |
| `Einrichten.bat` | Startet den Assistenten `einrichtung/einrichten.ps1` (Windows, Telefon per USB) |
| `einrichtung/einrichten.sh` | Einrichtung in Termux, auch zum Aktualisieren; `daten/` bleibt erhalten |
| `lokal/ssh-schlüssel.pub` | Nur bei gewähltem SSH-Fernzugriff: öffentlicher Schlüssel; der Assistent erzeugt ihn bei Bedarf, der private liegt nur auf dem Rechner (`~/.ssh/ankerwache_ed25519`) |
| `programm/test_ankerwache.py` | Tests der Logik (nur für die Entwicklung) |

Laufzeitdaten liegen in `~/ankerwache/daten/`: `zustand.json`, `track_JJJJ-MM-TT.csv`, `protokoll.txt`.

## Schnellweg: neues Gerät per USB vom Rechner

Voraussetzung: Android mit arm64, USB-Debugging an, Termux aus dem Play Store vorher deinstalliert.
`Einrichten.bat` doppelklicken. Der Assistent lädt fehlende Apps und ADB selbst von den offiziellen Quellen (alternativ vorher in `lokal/apps` und `lokal/platform-tools` ablegen, dann braucht der Rechner kein Internet), legt Telegram-Bot, Gruppe und Rufnummern in `konfiguration.json` an und lässt jeden Zusatz (Zusatz-Apps, Datensparmodus, Deaktivieren nicht benötigter Apps, SSH, ADB auf dem Telefon) wählbar. Fragt Play Protect nach, „Trotzdem installieren“ wählen. Danach nur noch die herstellerspezifischen Schalter unten in Abschnitt 2 setzen.
Eine vorhandene `konfiguration.json` verwendet der Assistent unverändert; ohne sie oder mit der Vorlage fragt er die Angaben ab. Der Telegram-Zugang wird nach der Einrichtung vom frei lesbaren Download-Ordner des Telefons gelöscht.

## 1. Apps installieren (alle kostenlos)

1. F-Droid von `f-droid.org` installieren.
2. Über F-Droid **Termux**, **Termux:API** und **Termux:Boot** installieren. Alle drei müssen aus F-Droid stammen, Versionen aus dem Play Store passen nicht zusammen.
3. **Termux:Boot** einmal öffnen, damit Android den Start beim Systemstart erlaubt.

## 2. Android-Einstellungen

> **Wichtig:** Jeder Hersteller (Samsung, Xiaomi, Huawei, Honor, Oppo, OnePlus und andere) bringt eigene Energiespar- und Hintergrundregeln mit, die Apps ohne Warnung beenden können. Prüfen Sie deshalb auf Ihrem Telefon, dass für Termux, Termux:API, Termux:Boot und GPSLogger alle Energiesparoptionen ausgeschaltet sind (Akku „Nicht eingeschränkt“, Autostart und Hintergrundbetrieb erlaubt, App in der Übersicht gesperrt), und testen Sie den Alarm über Nacht.

- **Termux:API:** Standort „Immer zulassen“ und „Genauer Standort“, SMS erlauben (Senden und Lesen).
- **Mobile Daten schalten (`DATEN EIN/AUS`):** Das darf nur Root oder ADB. Gerootetes Telefon: Termux dauerhaft Root-Rechte geben. Ohne Root: `pkg install android-tools` (macht `einrichten.sh`), per USB `adb tcpip 5555`, dann in Termux `adb connect 127.0.0.1:5555` und am Telefon „Immer zulassen“ (der Schnellweg erledigt das). `adb tcpip` gilt nur bis zum nächsten Neustart des Telefons; danach schaltet `DATEN EIN/AUS` nur das WLAN, bis `adb tcpip 5555` erneut per USB gegeben wird. Solange ADB per TCP an ist, lauscht es im Netz auf Port 5555; fremde Rechner brauchen aber die Freigabe am Bildschirm.
- **Termux, Termux:API und Termux:Boot:** Akkunutzung „Nicht eingeschränkt“ bzw. „Nicht optimieren“.
- SIM-Karte mit SMS-Guthaben und mobilen Daten.
- **Ab Android 8:** Im Hintergrund erhält Termux:API sonst höchstens alle 10 Minuten eine Position, der Alarm bliebe aus. Einmalig per ADB, bleibt über Neustarts erhalten (der Schnellweg erledigt das):
  ```
  adb shell settings put global location_background_throttle_package_whitelist com.termux.api
  ```
- **Honor/Huawei (EMUI):** Der Energiedienst PowerGenie beendet Termux trotz aller Einstellungen. Per ADB entfernen:
  ```
  adb shell pm uninstall -k --user 0 com.huawei.powergenie
  adb shell pm uninstall -k --user 0 com.huawei.android.hwaps
  ```
  Rückgängig: `adb shell cmd package install-existing com.huawei.powergenie`. Zusätzlich Einstellungen > Akku > App-Start: Termux, Termux:API und Termux:Boot auf „Manuell verwalten“, alle Schalter ein; Termux in der Übersicht der zuletzt benutzten Apps sperren.
- **Xiaomi (MIUI):** Für Termux, Termux:API und Termux:Boot unter App-Info „Autostart“ ein und Energiesparen „Keine Einschränkungen“; Termux in der Übersicht sperren. Ohne diese Schalter blockiert MIUI den Standort, sobald Termux im Hintergrund ist.

## 3. Programm kopieren und einrichten

1. `programm/konfiguration.beispiel.json` nach `konfiguration.json` kopieren, Telegram-Token und Gruppe eintragen und die SMS-Empfänger unter `smsEmpfänger` setzen, immer mit Ländervorwahl, zum Beispiel `["+491701234567"]`. Nur so kommen SMS auch im Ausland oder mit ausländischer SIM-Karte an.
2. Einen Ordner `ankerwache` anlegen und darin ablegen: `ankerwache.py`, `start-ankerwache.sh`, `halte-ankerwache-am-laufen.sh` (aus `programm/`), `einrichten.sh` (aus `einrichtung/`) und die ausgefüllte `konfiguration.json`. Den Ordner per USB in den Ordner `Download` des Telefons kopieren.
3. In Termux:
   ```
   termux-setup-storage
   cd ~/storage/downloads/ankerwache
   sh einrichten.sh
   ```
   Das Skript gibt eine GPS-Position und den Batteriestand aus, richtet einen Android-Zeitplanauftrag ein, der die Ankerwache alle 15 Minuten startet, falls sie nicht läuft, und startet sie. Fragt Android nach Berechtigungen: erlauben.
4. SMS-Test: `termux-sms-send -n +491701234567 Test`

In der Gruppe erscheint „Ankerwache gestartet“. Nach einem Neustart des Telefons startet das Programm von selbst.

Die Termux-Benachrichtigung („wake lock held“) muss stehen bleiben. Termux nicht über „Exit“ beenden.
Stoppen: `termux-job-scheduler --cancel --job-id 1; pkill -f start-ankerwache; pkill -f ankerwache.py`
Wieder starten: `sh ~/storage/downloads/ankerwache/einrichten.sh` oder Telefon neu starten.
Nach einer Änderung an der Konfiguration: stoppen und neu starten.

## 4. Befehle per Telegram oder SMS

Befehle gehen in die Telegram-Gruppe oder per SMS an das Bordtelefon. Per SMS gelten nur Absender aus `smsEmpfänger`, gleich in welcher Schreibweise der Anbieter die Absendernummer liefert (+49…, 0049…, 0…); die Antwort geht per SMS nur an den Absender, ohne Umlaute und so kurz, dass `POSITION` meist in eine SMS passt (`HILFE` braucht zwei).

```
POSITION                          Position, Abstand zum Anker, Status
SETZE ANKER                       Anker auf aktuelle Position
SETZE ANKER 43.123456, -5.678901  Anker auf Koordinaten (Süd und West negativ)
ALARM EIN                         Radius = aktueller Abstand + 20 Meter
ALARM EIN 80                      Radius 80 Meter
ALARM AUS                         Überwachung aus
GENAUIGKEIT 20                    Nur Positionen mit 20 Metern Genauigkeit oder besser zählen (1 bis 100)
GPS WARNUNG 30                    Warnung nach 30 Minuten ohne gültige Position (5 bis 240, Standard aus gpsWarnungNachSekunden)
TRACK                             Telegram: Track der letzten 7 Tage als GPX-Datei
                                  SMS: weitester Abstand vom Anker seit dem Setzen (höchstens 7 Tage) mit Genauigkeit, Zeit, Position
MELDUNGEN TELEGRAM                Lebenszeichen, Start und Warnungen per Telegram (wie bisher)
MELDUNGEN SMS                     … nur per SMS an alle SMS-Empfänger
MELDUNGEN BEIDE                   … auf beiden Wegen
LEBENSZEICHEN TÄGLICH             Status täglich um lebenszeichenUhrzeit
LEBENSZEICHEN WÖCHENTLICH         … montags
LEBENSZEICHEN MONATLICH           … am 1. des Monats
DATEN AUS                         Mobile Daten und WLAN aus; danach Befehle und Meldungen nur per SMS
DATEN EIN                         Mobile Daten und WLAN ein
FERNZUGRIFF AUS                   RustDesk und Tailscale deaktivieren, sie nutzen dann keine Daten (Root/ADB); Grundeinstellung
FERNZUGRIFF EIN                   RustDesk und Tailscale starten; bleibt über Neustarts, bis FERNZUGRIFF AUS kommt
EMPFÄNGER HINZU +491701234567     SMS-Empfänger hinzufügen (mit Ländervorwahl, +… oder 00…)
EMPFÄNGER ENTFERNEN +491701234567 SMS-Empfänger entfernen; der letzte bleibt
STATUS, GERÄTESTATUS              Mobile Daten und Verbindung, Mobilfunknetz und Roaming, WLAN, ob mobile Daten schaltbar sind
                                  (Root/ADB), letzter Telegram-Kontakt, SMS-Empfänger, gesendete SMS im Monat, ob SMS
                                  möglich sind und wie viele warten (per SMS nur die wartenden), Meldeweg,
                                  Lebenszeichen, Batterie mit Temperatur, GPS, Alarm
HILFE                             Befehlsübersicht
```
`smsEmpfänger` in `konfiguration.json` gilt nur beim ersten Start; danach zählen die per `EMPFÄNGER` geänderten Nummern, auch über Neustarts. Die Zahl gesendeter SMS zählt lange SMS mit allen abgerechneten Teilen.
Groß- und Kleinschreibung ist egal, statt Ä, Ö, Ü geht auch AE, OE, UE. Ein unbekannter Befehl liefert „Falscher Befehl.“ und per Telegram die Übersicht. Befehle, die älter als 5 Minuten sind (zum Beispiel nach einem Netzausfall oder Neustart), werden verworfen, per Telegram erst nach 10 Minuten. SMS werden alle 20 Sekunden abgerufen, Telegram alle 5 Minuten; ein Befehl per Telegram wird daher bis zu 5 Minuten später ausgeführt.

`MELDUNGEN` und `LEBENSZEICHEN` bleiben über Neustarts erhalten; Startwerte sind `meldeweg` und `lebenszeichenHäufigkeit` in `konfiguration.json`. `DATEN AUS` per Telegram schaltet erst nach der Antwort ab, die Bestätigung kommt per SMS. `DATEN EIN` geht bei ausgeschalteten Daten nur per SMS.

## 5. Meldungen

| Ereignis | SMS | Telegram |
|---|---|---|
| Alarm: 3 gültige Positionen hintereinander außerhalb des Radius | immer | ja, mit Kartenverweis, außer bei `DATEN AUS` |
| Nach 30 Minuten weiterhin außerhalb | einmal | einmal |
| Lebenszeichen um 09:00, täglich, montags oder am 1. des Monats | nach `MELDUNGEN` | nach `MELDUNGEN` |
| Programmstart, auch nach Neustart oder Absturz, höchstens einmal pro Stunde | nach `MELDUNGEN` | nach `MELDUNGEN` |
| Keine gültige GPS-Position seit 30 Minuten (`gpsWarnungNachSekunden`), auch über Neustarts nur einmal / wieder verfügbar nach 30 Minuten stabilem Empfang | nach `MELDUNGEN` | nach `MELDUNGEN` |
| Batterie unter 20 Prozent | nach `MELDUNGEN` | nach `MELDUNGEN` |
| Antwort auf einen Befehl | an den Absender, wenn per SMS | wenn per Telegram |

Bei `DATEN AUS` gehen alle Meldungen per SMS, gleich was `MELDUNGEN` sagt. Jede Meldung per SMS kostet je SMS-Empfänger eine SMS.

Fehlen SIM-Karte oder Mobilfunknetz, wartet jede SMS und geht genau einmal raus, sobald beides wieder da ist (Prüfung alle 30 Sekunden), auch nach einem Neustart; ist sie mehr als 5 Minuten verspätet, beginnt sie mit „Verspaetet, erstellt …“. `ALARM EIN`, `ALARM AUS` und `SETZE ANKER` verwerfen alle wartenden SMS. Fehlendes Guthaben oder eine nicht zugestellte SMS bemerkt das Programm nicht.

Datensparsam: Die Verbindung zu Telegram bleibt offen, eingehende Befehle werden nur alle 5 Minuten abgefragt; Meldungen gehen sofort raus. Nachrichten, die Telegram ablehnt, werden verworfen und ins Protokoll geschrieben. Noch nicht zugestellte Nachrichten werden gespeichert und nach einem Neustart weiter versendet; kommt eine Nachricht mehr als 5 Minuten nach ihrer Entstehung an, nennt sie ihren Entstehungszeitpunkt. Ohne Netzverbindung versucht das Programm es jede Minute erneut, das kostet keine Daten. Bei sonstigen Netz- oder Serverfehlern wartet es 10, dann 20, dann jeweils 30 Minuten bis zum nächsten Versuch; `DATEN EIN` beendet diese Pause innerhalb von 10 Sekunden. SMS sind davon nicht betroffen. Erhält die Gruppe eine neue Nummer (Umwandlung in eine Supergruppe), übernimmt das Programm sie selbst.

Das GPS wird alle 3 Sekunden abgefragt und bleibt so warm. Jede Messung fragt 30 Sekunden lang wiederholt ab und nimmt die genaueste Position, `SETZE ANKER` ebenso über 15 Sekunden. Gültig ist eine Position mit einer Genauigkeit von 15 Metern oder besser; `GENAUIGKEIT` ändert die Grenze dauerhaft. Nach `SETZE ANKER` ist der Alarm aus, bis `ALARM EIN` kommt.

Positionen kommen bevorzugt aus der Aufzeichnung von GPSLogger, sonst aus Termux:API. Ist der letzte GPSLogger-Eintrag älter als 60 Sekunden, nimmt das Programm die letzte GPS-Messung von Termux:API, sofern sie höchstens 10 Sekunden alt ist. Sonst prüft es höchstens alle 10 Minuten über ADB, ob der GPSLogger-Dienst läuft, und startet ihn nur dann neu, wenn nicht; ohne `adb tcpip 5555` seit dem letzten Neustart des Telefons gelingt das nicht. Nur wenn der Dienst nicht läuft und sich nicht starten lässt, fragt das Programm den Standort über Termux:API selbst ab; nach einem Fehlschlag erst nach 10 Minuten wieder, weil Android Termux:API bei jeder unbeantworteten Abfrage nach 60 Sekunden beendet. Bleibt eine Standortabfrage über Termux:API ohne Antwort, ruht diese für 10 Minuten ganz.

GPSLogger 137 meldet unter Android 8 bei jeder Messung Empfänger beim Systemprozess an, ohne sie abzumelden. Nach 40 bis 80 Minuten läuft dann ein Thread des Systemprozesses (HwBinder) dauerhaft unter Volllast, und keine App erhält mehr Positionen. Deshalb nur die gepatchte Fassung aus `neues-gerät/apps` verwenden; Ursache, Änderung und Nachbau stehen in `neues-gerät/gpslogger-patch/README.md`. Sie ist anders signiert als die F-Droid-Fassung, F-Droid kann sie deshalb nicht aktualisieren. Als Rückfallebene gilt: Läuft GPSLogger, liefert aber keine Positionen, misst das Programm bei der Prüfung über ADB 10 Sekunden lang die Auslastung dieser Threads (HwBinder). Liegt sie über 50 % eines Kerns, schaltet es das GPS für 30 Sekunden aus und startet GPSLogger neu. Hängt das GPS danach weiterhin, wiederholt es das bei der nächsten Prüfung. Das Handy startet es nicht neu, weil danach die lokale ADB-Verbindung und damit `DATEN EIN`/`DATEN AUS` bis zum erneuten `adb tcpip 5555` über USB fehlen würden.

## 6. Fernzugriff

- **Tailscale** (in der App anmelden) verbindet Rechner und Telefon auch im Mobilnetz. Damit es nach einem Neustart von selbst verbindet: Einstellungen > Verbindungen > Weitere Verbindungseinstellungen > VPN > Tailscale > „Durchgehend aktives VPN“ ein.
- **SSH** in Termux, sehr datensparsam: `ssh -i ~/.ssh/ankerwache_ed25519 -p 8022 <Tailscale-Adresse des Telefons>`. Nur mit Schlüssel, `sshd` startet beim Systemstart und alle 15 Minuten, falls er nicht läuft.
- **RustDesk** für den Bildschirm, wenn SSH nicht reicht; braucht deutlich mehr Daten.
- **Datensparmodus:** Im Mobilnetz dürfen nur Termux, RustDesk und Tailscale im Hintergrund Daten nutzen; alle nicht benötigten Apps (`einrichtung/nicht-benötigte-apps.txt`) sind deaktiviert, auch Google Play-Dienste und Google Maps. Einmalig per ADB, bleibt über Neustarts erhalten:
  ```
  adb shell cmd netpolicy set restrict-background true
  adb shell cmd netpolicy add restrict-background-whitelist <UID>
  ```
  `<UID>` jeweils von `com.termux`, `com.carriez.flutter_hbb` und `com.tailscale.ipn` (`adb shell dumpsys package <Paket> | grep userId`).
- **GPS-Hilfsdaten:** Der Broadcom-GPS-Dienst des Samsung S7 lädt laufend Hilfsdaten von glpals.com, etwa 45 MB im Monat. Weder `assisted_gps_enabled` noch der Datensparmodus wirken darauf; abstellen ließe sich das nur mit Root oder einer Firewall-App, die das VPN von Tailscale belegen würde.

## 7. Abnahme

1. `POSITION` und einen falschen Befehl senden.
2. An Land `SETZE ANKER`, dann `ALARM EIN`, dann 40 Meter weggehen. Nach etwa 90 Sekunden müssen SMS und Telegram-Meldung kommen.
3. `TRACK` senden und die GPX-Datei öffnen; `TRACK` und `POSITION` per SMS senden.
4. `DATEN AUS` per SMS, prüfen, dass mobile Daten und WLAN aus sind, dann `DATEN EIN` per SMS.
5. Telefon neu starten, die Startmeldung muss von selbst kommen (frühestens eine Stunde nach der letzten).
6. Zwei Nächte Testbetrieb.
