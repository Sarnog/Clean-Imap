import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json
import os

print("IMAP Cleaner: Python script gestart (Gmail-compatibele versie + UID-fix, v2)", flush=True)

# -------------------------------------------------------
# 1. Lees Home Assistant add-on opties
# -------------------------------------------------------
with open("/data/options.json", "r") as f:
    opts = json.load(f)


def to_int(value, default=0):
    """
    Converteer naar int, of geef default terug als het mislukt.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


IMAP_HOST = opts.get("imap_host")
IMAP_PORT = to_int(opts.get("imap_port"), 0)
IMAP_USER = opts.get("imap_username")
IMAP_PASS = opts.get("imap_password")

MQTT_HOST = opts.get("mqtt_host")
MQTT_PORT = to_int(opts.get("mqtt_port"), 0)
MQTT_USER = opts.get("mqtt_username")
MQTT_PASS = opts.get("mqtt_password")
MQTT_TOPIC = opts.get("mqtt_topic")

POLL_INTERVAL = to_int(opts.get("poll_interval"), 60)
MARK_AS_READ = bool(opts.get("mark_as_read", False))


print(
    f"Opties geladen: IMAP host={IMAP_HOST}, user={IMAP_USER}, "
    f"mark_as_read={MARK_AS_READ}, poll_interval={POLL_INTERVAL}s",
    flush=True,
)

# -------------------------------------------------------
# 1b. Config-validatie (host / poorten / wachtwoorden)
# -------------------------------------------------------
def validate_port(name, value):
    if not isinstance(value, int):
        print(f"IMAP Cleaner: Ongeldige {name}-poort (geen getal): {value!r}", flush=True)
        return False
    if value <= 0 or value > 65535:
        print(f"IMAP Cleaner: Ongeldige {name}-poort (buiten bereik 1-65535): {value}", flush=True)
        return False
    return True


def validate_config():
    fouten = False

    # IMAP
    if not IMAP_HOST or not isinstance(IMAP_HOST, str):
        print("IMAP Cleaner: Ongeldige IMAP-hostnaam in configuratie.", flush=True)
        fouten = True
    if not validate_port("IMAP", IMAP_PORT):
        fouten = True
    if not IMAP_USER:
        print("IMAP Cleaner: Geen IMAP-gebruikersnaam opgegeven in configuratie.", flush=True)
        fouten = True
    if not IMAP_PASS:
        print("IMAP Cleaner: GEEN IMAP-wachtwoord opgegeven in configuratie!", flush=True)
        fouten = True

    # MQTT
    if not MQTT_HOST or not isinstance(MQTT_HOST, str):
        print("IMAP Cleaner: Ongeldige MQTT-hostnaam in configuratie.", flush=True)
        fouten = True
    if not validate_port("MQTT", MQTT_PORT):
        fouten = True

    if not MQTT_TOPIC:
        print(
            "IMAP Cleaner: Waarschuwing: geen MQTT-topic opgegeven; "
            "er wordt niets gepubliceerd.",
            flush=True,
        )

    # Poll-interval
    if POLL_INTERVAL <= 0:
        print(
            f"IMAP Cleaner: Ongeldig poll_interval ({POLL_INTERVAL}), gebruik fallback 60s.",
            flush=True,
        )

    # Extra waarschuwing voor MQTT-wachtwoord (niet verplicht, maar aanbevolen)
    if not MQTT_PASS:
        print(
            "IMAP Cleaner: Let op: geen MQTT-wachtwoord opgegeven. "
            "Authenticatie voor MQTT wordt sterk aanbevolen.",
            flush=True,
        )

    if fouten:
        print("IMAP Cleaner: Configuratiefouten gevonden, script wordt afgesloten.", flush=True)
        raise SystemExit(1)


# -------------------------------------------------------
# 2. Persistent UID tracking
# -------------------------------------------------------
UID_FILE = "/data/imap_processed_uids.json"


def load_uids():
    if not os.path.exists(UID_FILE):
        return set()
    try:
        with open(UID_FILE, "r") as f:
            loaded = json.load(f)

        # Oude fallback UID's (SEQ-...) negeren
        cleaned = {
            u for u in loaded
            if isinstance(u, str) and not u.startswith("SEQ-")
        }

        if len(cleaned) < len(loaded):
            print(
                f"IMAP Cleaner: {len(loaded) - len(cleaned)} oude fallback UID(s) verwijderd",
                flush=True,
            )

        return cleaned
    except Exception as e:
        print("IMAP Cleaner: Kon UID-bestand niet laden:", e, flush=True)
        return set()


def save_uids(uids):
    try:
        with open(UID_FILE, "w") as f:
            json.dump(list(uids), f)
    except Exception as e:
        print("IMAP Cleaner: Kon UID-bestand niet opslaan:", e, flush=True)


processed_uids = load_uids()
print(f"IMAP Cleaner: {len(processed_uids)} UID(s) eerder verwerkt", flush=True)

# -------------------------------------------------------
# 3. HTML → tekst conversie
# -------------------------------------------------------
def html_to_text(html):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# -------------------------------------------------------
# 4. Header decode
# -------------------------------------------------------
def decode_header_value(value):
    if not value:
        return ""
    parts = decode_header(value)
    result = ""
    for text, enc in parts:
        try:
            if isinstance(text, bytes):
                result += text.decode(enc or "utf-8", errors="replace")
            else:
                result += text
        except Exception:
            result += str(text)
    return result


# -------------------------------------------------------
# 5. Beste mail-body bepalen
# -------------------------------------------------------
def extract_body(msg):
    for part in msg.walk():
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        charset = part.get_content_charset() or "utf-8"

        if ctype == "text/plain":
            try:
                return payload.decode(charset, errors="replace")
            except Exception:
                pass

        if ctype == "text/html":
            try:
                html = payload.decode(charset, errors="replace")
                return html_to_text(html)
            except Exception:
                pass

    return "(Geen leesbare inhoud gevonden)"


# -------------------------------------------------------
# 6. MQTT client (API v2 – geen DeprecationWarning)
# -------------------------------------------------------
mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    callback_api_version=2,
)

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)


def mqtt_send(data):
    if not MQTT_TOPIC:
        print("IMAP Cleaner: MQTT-topic niet ingesteld, bericht wordt niet gepubliceerd.", flush=True)
        return

    try:
        print(f"IMAP Cleaner: MQTT publish naar {MQTT_HOST}:{MQTT_PORT}, topic={MQTT_TOPIC}", flush=True)
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.publish(MQTT_TOPIC, json.dumps(data))
        mqtt_client.disconnect()
    except Exception as e:
        print("IMAP Cleaner: MQTT-fout:", e, flush=True)


# -------------------------------------------------------
# 7. Gmail-compatibele UID opvragen
# -------------------------------------------------------
def get_uid(mail, seq_id):
    """
    Haal de echte UID op bij een bericht met sequence-ID 'seq_id'.
    """
    try:
        status, response = mail.fetch(seq_id, "(UID)")
        if status == "OK" and response and response[0]:
            first = response[0]

            # first kan bytes of tuple zijn
            if isinstance(first, tuple):
                header_bytes = first[0]
            else:
                header_bytes = first

            header = header_bytes.decode("utf-8", errors="ignore").upper()
            print(f"IMAP Cleaner: UID header voor {seq_id}: {header}", flush=True)

            if "UID" in header:
                parts = header.split("UID", 1)[1].split()
                if parts:
                    uid = parts[0].strip()
                    print(
                        f"IMAP Cleaner: Echte UID gevonden voor {seq_id}: {uid}",
                        flush=True,
                    )
                    return uid
    except Exception as e:
        print(
            f"IMAP Cleaner: fout bij UID opvragen voor {seq_id}: {e}",
            flush=True,
        )

    # Fallback als alles faalt
    fallback_uid = f"SEQ-{seq_id.decode()}"
    print(f"IMAP Cleaner: UID fallback gebruikt: {fallback_uid}", flush=True)
    return fallback_uid


# -------------------------------------------------------
# 8. MAIL FETCH helper
# -------------------------------------------------------
def fetch_message(mail, seq_id, uid):
    """
    Probeer eerst:
      FETCH <seq_id> (RFC822)
    Daarna (optioneel):
      UID FETCH <uid> (RFC822)
    """
    try:
        status, data = mail.fetch(seq_id, "(RFC822)")
        if status == "OK" and data and len(data) > 0:
            return data[0][1]
    except Exception as e:
        print(f"IMAP Cleaner: FETCH seq error: {e}", flush=True)

    try:
        status, data = mail.uid("FETCH", uid, "(RFC822)")
        if status == "OK" and data and len(data) > 0:
            return data[0][1]
    except Exception as e:
        print(f"IMAP Cleaner: UID FETCH error voor UID {uid}: {e}", flush=True)

    print("IMAP Cleaner: Kon bericht niet fetchen voor:", seq_id, flush=True)
    return None


# -------------------------------------------------------
# 9. Main loop
# -------------------------------------------------------
def run_imap_loop():
    if MARK_AS_READ:
        print(
            "IMAP Cleaner: Modus mark_as_read=TRUE "
            "(mails worden gelezen gemarkeerd, geen UID-deduplicatie).",
            flush=True,
        )
    else:
        print(
            "IMAP Cleaner: Modus mark_as_read=FALSE "
            "(UID-deduplicatie, mails blijven ongelezen).",
            flush=True,
        )

    print(
        f"IMAP Cleaner: gestart. Poll-interval: {POLL_INTERVAL} seconden. "
        f"IMAP server: {IMAP_HOST}:{IMAP_PORT}, MQTT broker: {MQTT_HOST}:{MQTT_PORT}, topic: {MQTT_TOPIC}",
        flush=True,
    )

    while True:
        try:
            # IMAP-verbinding
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            print("IMAP Cleaner: Verbonden met IMAP-server.", flush=True)

            # Correcte login: altijd twee argumenten
            mail.login(IMAP_USER, IMAP_PASS)
            print(f"IMAP Cleaner: Ingelogd als '{IMAP_USER}'.", flush=True)

            if MARK_AS_READ:
                mail.select("INBOX")
            else:
                mail.select("INBOX", readonly=True)

            status, ids = mail.search(None, "UNSEEN")
            if status != "OK":
                print("IMAP Cleaner: UNSEEN search fout:", status, flush=True)
                mail.logout()
                time.sleep(10)
                continue

            seq_ids = ids[0].split()
            print("IMAP Cleaner: UNSEEN gevonden:", seq_ids, flush=True)

            for seq_id in seq_ids:
                # 1) UID ophalen
                uid = get_uid(mail, seq_id)

                # 2) Deduplicatie (alleen als we mails ongelezen laten)
                if not MARK_AS_READ and uid in processed_uids:
                    print(f"IMAP Cleaner: Skip – UID {uid} is al verwerkt", flush=True)
                    continue

                # 3) Bericht ophalen
                raw_bytes = fetch_message(mail, seq_id, uid)
                if not raw_bytes:
                    print("IMAP Cleaner: Kon mail niet ophalen:", seq_id, flush=True)
                    continue

                msg = email.message_from_bytes(raw_bytes)

                onderwerp = decode_header_value(msg.get("subject"))
                afzender = decode_header_value(msg.get("from"))
                tekst = extract_body(msg)

                mqtt_send(
                    {
                        "onderwerp": onderwerp,
                        "afzender": afzender,
                        "tekst": tekst,
                    }
                )

                # 4) UID markeren als verwerkt
                if not MARK_AS_READ:
                    processed_uids.add(uid)
                    save_uids(processed_uids)

            mail.logout()
            time.sleep(POLL_INTERVAL if POLL_INTERVAL > 0 else 60)

        except imaplib.IMAP4.error as e:
            # Auth/IMAP-probleem (bijvoorbeeld verkeerde login)
            print("IMAP Cleaner: IMAP-authenticatie- of protocolfout:", e, flush=True)
            time.sleep(30)
        except Exception as e:
            print("IMAP Cleaner: IMAP fout:", e, flush=True)
            time.sleep(10)


# -------------------------------------------------------
# Start
# -------------------------------------------------------
if __name__ == "__main__":
    validate_config()
    run_imap_loop()
