import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json
import os

print("IMAP Cleaner: Python script gestart (Enterprise Reliability Mode)", flush=True)

# -------------------------------------------------------
# 1. Lees Home Assistant add-on opties
# -------------------------------------------------------
with open("/data/options.json", "r") as f:
    opts = json.load(f)

IMAP_HOST = opts.get("imap_host")
IMAP_PORT = int(opts.get("imap_port"))
IMAP_USER = opts.get("imap_username")
IMAP_PASS = opts.get("imap_password")

MQTT_HOST = opts.get("mqtt_host")
MQTT_PORT = int(opts.get("mqtt_port"))
MQTT_USER = opts.get("mqtt_username")
MQTT_PASS = opts.get("mqtt_password")
MQTT_TOPIC = opts.get("mqtt_topic")

MARK_AS_READ = bool(opts.get("mark_as_read", False))

print(
    f"Opties geladen: host={IMAP_HOST}, user={IMAP_USER}, mark_as_read={MARK_AS_READ}",
    flush=True,
)

# -------------------------------------------------------
# UID Persistence
# -------------------------------------------------------
PROCESSED_UIDS_FILE = "/data/imap_processed_uids.json"

def load_processed_uids():
    if not os.path.exists(PROCESSED_UIDS_FILE):
        return set()
    try:
        with open(PROCESSED_UIDS_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_processed_uids(uids):
    try:
        with open(PROCESSED_UIDS_FILE, "w") as f:
            json.dump(list(uids), f)
    except Exception as e:
        print("Kon UID-bestand niet opslaan:", e, flush=True)

processed_uids = load_processed_uids()
print(f"Loaded {len(processed_uids)} verwerkte UID(s)", flush=True)


# -------------------------------------------------------
# HTML opschonen
# -------------------------------------------------------
def html_to_text(html):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return clean


# -------------------------------------------------------
# Header decoderen
# -------------------------------------------------------
def decode_header_value(value):
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for text, enc in parts:
        try:
            if isinstance(text, bytes):
                decoded += text.decode(enc or "utf-8", errors="replace")
            else:
                decoded += text
        except:
            decoded += str(text)
    return decoded


# -------------------------------------------------------
# Beste body extraheren
# -------------------------------------------------------
def extract_body(msg):
    text_plain = None
    text_html = None

    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            text_plain = part.get_payload(decode=True)
            if text_plain:
                charset = part.get_content_charset() or "utf-8"
                try: return text_plain.decode(charset, errors="replace")
                except: pass

        if part.get_content_type() == "text/html":
            text_html = part.get_payload(decode=True)
            if text_html:
                charset = part.get_content_charset() or "utf-8"
                try:
                    return html_to_text(text_html.decode(charset, errors="replace"))
                except:
                    pass

    return "(Geen leesbare inhoud gevonden)"


# -------------------------------------------------------
# MQTT client
# -------------------------------------------------------
mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    callback_api_version=2
)

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def mqtt_send(data):
    try:
        print("MQTT publish:", data, flush=True)
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.publish(MQTT_TOPIC, json.dumps(data))
        mqtt_client.disconnect()
    except Exception as e:
        print("MQTT fout:", e, flush=True)


# -------------------------------------------------------
# UID Parser (FALLBACK SAFE)
# -------------------------------------------------------
def safe_get_uid(mail, msg_id):
    """
    Retourneert UID indien beschikbaar,
    anders fallback SEQ-msg_id.
    """

    # Probeer officiële UID op te vragen via UID FETCH
    try:
        status, response = mail.uid("FETCH", msg_id, "(UID)")
        if status == "OK" and response and isinstance(response[0], bytes):
            text = response[0].decode("utf-8", errors="ignore")
            # Zoek substring "UID <nummer>"
            if "UID" in text.upper():
                try:
                    uid = text.upper().split("UID")[1].split()[0]
                    return uid.strip()
                except:
                    pass
    except Exception as e:
        print(f"UID FETCH error voor {msg_id}: {e}", flush=True)

    # FALLBACK
    fallback_uid = f"SEQ-{msg_id.decode()}"
    print(f"UID fallback gebruikt voor message {msg_id}: {fallback_uid}", flush=True)
    return fallback_uid


# -------------------------------------------------------
# Main Loop
# -------------------------------------------------------
def run_imap_loop():
    if MARK_AS_READ:
        print("Modus: mark_as_read = TRUE (mails worden gelezen gemarkeerd)", flush=True)
    else:
        print("Modus: mark_as_read = FALSE (UID-deduplicatie, mails blijven ongelezen)", flush=True)

    while True:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(IMAP_USER, IMAP_PASS)

            if MARK_AS_READ:
                mail.select("INBOX")
            else:
                mail.select("INBOX", readonly=True)

            status, ids = mail.search(None, "UNSEEN")

            if status != "OK":
                print("UNSEEN search mislukt:", status, flush=True)
                mail.logout()
                time.sleep(5)
                continue

            msg_ids = ids[0].split()
            print(f"UNSEEN gevonden: {msg_ids}", flush=True)

            for msg_id in msg_ids:
                # Stap 1: UID correct ophalen
                uid = safe_get_uid(mail, msg_id)

                # Stap 2: Deduplicatie
                if uid in processed_uids and not MARK_AS_READ:
                    print(f"Skip (UID verwerkt): {uid}", flush=True)
                    continue

                # Stap 3: FETCH BODY
                status, data = mail.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not data or len(data) < 1:
                    print(f"UID FETCH faalde, fallback FETCH voor msg_id={msg_id}", flush=True)
                    status, data = mail.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not data or len(data) < 1:
                        print("FETCH faalde volledig", flush=True)
                        continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                onderwerp = decode_header_value(msg.get("subject"))
                afzender = decode_header_value(msg.get("from"))
                tekst = extract_body(msg)

                mqtt_send({
                    "onderwerp": onderwerp,
                    "afzender": afzender,
                    "tekst": tekst
                })

                # Stap 4: Markeer UID verwerkt
                if not MARK_AS_READ:
                    processed_uids.add(uid)
                    save_processed_uids(processed_uids)

            mail.logout()
            time.sleep(5)

        except Exception as e:
            print("IMAP fout:", e, flush=True)
            time.sleep(10)


# -------------------------------------------------------
# Start script
# -------------------------------------------------------
if __name__ == "__main__":
    run_imap_loop()
