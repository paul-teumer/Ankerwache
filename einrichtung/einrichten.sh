#!/data/data/com.termux/files/usr/bin/sh
# Einmalige Einrichtung in Termux. Aufruf aus dem Ordner ankerwache: sh einrichten.sh
set -e
# Rückfragen zu geänderten Konfigurationsdateien automatisch mit der Paketfassung beantworten.
export DEBIAN_FRONTEND=noninteractive
echo 'Dpkg::Options { "--force-confnew"; "--force-confdef"; };' > "$PREFIX/etc/apt/apt.conf.d/90-keine-nachfrage"
# Mitgelieferte Pakete sparen das Herunterladen; ohne sie oder bei Fehler aus dem Netz.
if ! { [ -d pakete ] && apt install -y ./pakete/*.deb; }; then
    pkg update -y
    pkg install -y --no-install-recommends python termux-api
fi
# Fernzugriff per SSH nur mit Schlüssel, Port 8022; ohne Datei ssh-schlüssel.pub entfällt er.
if [ ! -f ssh-schlüssel.pub ]; then
    echo "Kein SSH-Schlüssel, kein Fernzugriff per SSH."
elif pkg install -y openssh; then
    mkdir -p "$HOME/.ssh"
    cp ssh-schlüssel.pub "$HOME/.ssh/authorized_keys"
    chmod 700 "$HOME/.ssh"; chmod 600 "$HOME/.ssh/authorized_keys"
    sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' "$PREFIX/etc/ssh/sshd_config"
    grep -q '^PasswordAuthentication no' "$PREFIX/etc/ssh/sshd_config" || echo 'PasswordAuthentication no' >> "$PREFIX/etc/ssh/sshd_config"
else
    echo "openssh nicht installiert, kein Fernzugriff per SSH."
fi
# Ohne Root schaltet die Ankerwache die mobilen Daten über ADB, das auf dem Gerät selbst lauscht; Aufruf mit "ohne-adb" lässt es weg.
if [ "$1" = "ohne-adb" ]; then
    echo "Ohne ADB: DATEN EIN/AUS schaltet nur das WLAN."
else
    su -c true >/dev/null 2>&1 || pkg install -y android-tools || echo "android-tools nicht installiert, DATEN EIN/AUS schaltet nur das WLAN."
fi
mkdir -p "$HOME/ankerwache/daten" "$HOME/.termux/boot"
cp ankerwache.py konfiguration.json halte-ankerwache-am-laufen.sh "$HOME/ankerwache/"
cp start-ankerwache.sh "$HOME/.termux/boot/start-ankerwache.sh"
chmod +x "$HOME/.termux/boot/start-ankerwache.sh" "$HOME/ankerwache/halte-ankerwache-am-laufen.sh"
termux-job-scheduler --job-id 1 --script "$HOME/ankerwache/halte-ankerwache-am-laufen.sh" --period-ms 900000 \
    --persisted true --network none --battery-not-low false
echo "Test GPS:";      termux-location -p gps -r once
echo "Test Batterie:"; termux-battery-status
sh "$HOME/ankerwache/halte-ankerwache-am-laufen.sh"
echo "Einrichtung fertig, Ankerwache gestartet."
