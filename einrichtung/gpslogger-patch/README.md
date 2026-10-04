# GPSLogger 137 mit behobenem Empfängerleck

`lokal/apps/com.mendhak.gpslogger-137-gepatcht.apk` (Release-Datei dieses Projekts) ist `com.mendhak.gpslogger-137-original.apk` mit zwei Änderungen in `GpsLoggingService.startGpsManager()`.

**Fehler:** GPSLogger meldet bei jeder Messrunde zwei Empfänger beim Systemprozess an, die nie wieder abgemeldet werden. Unter Android 8 sammeln sich so 10 bis 20 Empfänger pro Minute an. Nach 40 bis 80 Minuten läuft dort ein HwBinder-Thread unter Volllast, und keine App erhält mehr Positionen.

1. GPSLogger erzeugt bei jeder Runde einen neuen `GnssStatus.Callback`. Abgemeldet wird nur der jeweils letzte, und mit „GPS zwischen den Positionsbestimmungen eingeschaltet lassen“ gar keiner.
2. GPSLogger übergibt bei jeder Runde denselben NMEA-Empfänger an `addNmeaListener`. Das ist an sich harmlos, aber Android 8.0 prüft in `addNmeaListener(OnNmeaMessageListener, Handler)` die falsche Tabelle: `mGpsNmeaListeners` statt `mGnssNmeaListeners`. Deshalb meldet die Methode denselben Empfänger jedes Mal neu an.

**Änderungen** in `smali/com/mendhak/gpslogger/GpsLoggingService.smali`:

1. Unmittelbar nach `if-lt v0, v1, :cond_4` und vor `new-instance v2, Lcom/mendhak/gpslogger/GpsLoggingService$2;` einfügen:

   ```smali
       iget-object v2, p0, Lcom/mendhak/gpslogger/GpsLoggingService;->gnssStatusCallback:Landroid/location/GnssStatus$Callback;

       if-nez v2, :cond_4
   ```

2. Nach `iget-object v0, p0, …->nmeaLocationListener:…` die Zeile `if-nez v0, :cond_6` durch `if-nez v0, :goto_1` ersetzen. Der NMEA-Empfänger wird damit nur bei seiner Erzeugung angemeldet. Ab `:goto_1` liest der Code nur `v3`, und `v3` hat auf beiden Wegen denselben Wert.

Beide Empfänger bestehen damit einmal je Dienstlaufzeit.

**Nachbau** (Java 17, apktool, uber-apk-signer):

1. `java -jar apktool.jar d -r original.apk -o dec` ausführen und die Änderung oben einfügen.
2. `java -jar apktool.jar b dec -o neu.apk` ausführen.
3. Aus `neu.apk` nur `classes.dex` in eine Kopie der Original-APK übernehmen, ohne die alten Signaturdateien in `META-INF`. Die Ressourcen tragen Namen, die sich nur in der Groß-/Kleinschreibung unterscheiden. Unter Windows überschreiben sie sich beim Entpacken, ein vollständiger Neubau stürzt deshalb ab.
4. `java -jar uber-apk-signer.jar -a kopie.apk` ausführen.

Lizenz: GPLv2 wie GPSLogger (Datei `LICENSE` in diesem Ordner). Quelltext des Originals: <https://github.com/mendhak/gpslogger>, Version 137; die Änderung steht vollständig oben. Release-Datei und Originaldatei liegen im Release dieses Projekts.

Die Signatur weicht vom Original ab. Ein Wechsel zwischen beiden Fassungen erfordert deshalb Deinstallieren. uber-apk-signer verwendet stets denselben eingebauten Debug-Schlüssel, deshalb lässt sich eine neu gebaute gepatchte Fassung mit `adb install -r` über die vorhandene installieren, ohne Datenverlust. Dabei löscht Android den Ordner `Android/data/com.mendhak.gpslogger`, die CSV-Dateien und das Profil vorher sichern.

`Standardprofil.properties` enthält die Einstellungen für die Ankerwache: nur GPS, alle 5 s, GPS dauerhaft an, nur CSV, eine Datei pro Tag, Start beim Hochfahren.
