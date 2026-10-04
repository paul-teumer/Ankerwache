#!/data/data/com.termux/files/usr/bin/sh
# Hält das Gerät wach und startet die Ankerwache nach jedem Absturz neu.
# Rückgabewert 3 heißt: eine andere Instanz läuft bereits.
termux-wake-lock
# Hält Termux:API dauerhaft am Leben; ohne diesen Dienst beendet Android es und GPS-Abfragen bleiben unbeantwortet.
termux-api-start
command -v sshd >/dev/null && ! pgrep -x sshd >/dev/null && sshd
cd "$HOME/ankerwache" || exit 1
mkdir -p daten
# Bei wiederholtem Absturz verdoppelt sich die Pause bis 30 Minuten; nach einer Stunde fehlerfreiem Lauf beginnt sie neu.
pause=10
while true; do
    beginn=$(date +%s)
    python ankerwache.py >> daten/protokoll.txt 2>&1
    [ $? -eq 3 ] && exit 0
    [ $(( $(date +%s) - beginn )) -gt 3600 ] && pause=10
    sleep $pause
    pause=$(( pause * 2 > 1800 ? 1800 : pause * 2 ))
done
