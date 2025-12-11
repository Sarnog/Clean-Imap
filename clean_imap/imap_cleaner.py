import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json
import os

print("IMAP Cleaner: Python script gestart (Gmail-compatibele versie)", flush=True)

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
# 2. Persistent UID tracking
# -------------------------------------------------------
UID_FILE = "/data/imap_processed_uids.json"

def load_uids():
    if not os.path.exists(UID_FILE):
        return set()
    try:
        with open(UID_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_uids(uids):
    try:
        with open(UID_FILE, "w") as f:
            json.dump(list(uids), f)
    except Exception as e:
        print("Kon UID-bestand niet opslaan:", e, flush=True)

processed_uids = load_uids()
print(f"{len(processed_uids)} UID(s) eerder verwerkt", flush=True)

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
        except:
            result += str(text)
    return result

# -------------------------------------------------------
# 5. Beste mail-body bepalen
# -------------------------------------------------------
def extract_body(msg):
    text_plain = None
    text_html = None

    for part in msg.walk():
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        charset = part.get_content_charset() or "utf-8"

        if ctype == "text/plain":
            try:
                return payload.decode(charset, errors="replace")
            except:
                pass

        if ctype == "text/html":
            try:
                html = payload.decode(charset, errors="replace")
                return html_to_text(html)
            except:
                pass

    return "(Geen leesbare inhoud gevonden)"

# -------------------------------------------------------
# 6. MQTT client (API v2)
# -------------------------------------------------------
mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    callback_api_version=2,
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
# 7. Gmail-compatibele UID opvragen
# -------------------------------------------------------
def get_uid(mail, seq_id):
    """
    Correcte manier om UID op te halen bij Gmail (en overige IMAP servers).

    Gmail ondersteunt *niet*:
        UID FETCH <seq>

    Gmail ondersteunt wél:
        FETCH <seq> (UID)
    """

    try:
        status, response = mail.fetch(seq_id, "(UID)")
        if status == "OK" and response and isinstance(response[0], tuple):
            header = response[0][0].decode("utf-8", errors="ignore").upper()
            if "UID" in header:
                try:
                    uid = header.split("UID")[1].split()[0].strip()
                    return uid
                except:
                    pass
    except Exception as e:
        print(f"GMAIL UID-fetch fout voor message {seq_id}: {e}", flush=True)

    # Fallback als alles faalt
    fallback_uid = f"SEQ-{seq_id.decode()}"
    print(f"UID fallback gebruikt: {fallback_uid}", flush=True)
    return fallback_uid

# -------------------------------------------------------
# 8. MAIL FETCH helper (Gmail-compatibel)
# -------------------------------------------------------
def fetch_message(mail, seq_id, uid):
    """
    Probeer eerst:
        FETCH <seq> (RFC822)

    Daarna (optioneel):
        UID FETCH <uid> (RFC822)

    Gmail vereist sequence-fetch, niet UID-fetch.
    Andere servers kunnen beide.
    """
    # Gmail pad: werkt altijd
    try:
        status, data = mail.fetch(seq_id, "(RFC822)")
        if status == "OK" and data and len(data) > 0:
            return data[0][1]
    except Exception as e:
        print(f"FETCH seq error: {e}", flush=True)

    # Andere IMAP servers ondersteunen vaak UID FETCH
    try:
        status, data = mail.uid("FETCH", uid, "(RFC822)")
        if status == "OK" and data and len(data) > 0:
            return data[0][1]
    except:
        pass

    print("Kon bericht niet fetchen voor:", seq_id, flush=True)
    return None

# -------------------------------------------------------
# 9. Main loop
# -------------------------------------------------------
def run_imap_loop():

    if MARK_AS_READ:
        print("Modus: mark_as_read=TRUE (mails worden gelezen gemarkeerd)", flush=True)
    else:
        print("Modus: mark_as_read=FALSE (UID-deduplicatie, mails blijven ongelezen)", flush=True)

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
                print("UNSEEN search fout:", status, flush=True)
                time.sleep(5)
                continue

            seq_ids = ids[0].split()
            print("UNSEEN gevonden:", seq_ids, flush=True)

            for seq_id in seq_ids:

                # Stap 1 → UID ophalen via Gmail-compatible methode
                uid = get_uid(mail, seq_id)

                # Stap 2 → Dedup
                if uid in processed_uids and not MARK_AS_READ:
                    print(f"Skip: UID {uid} is al verwerkt", flush=True)
                    continue

                # Stap 3 → Bericht ophalen
                raw_bytes = fetch_message(mail, seq_id, uid)
                if not raw_bytes:
                    print("Kon mail niet ophalen:", seq_id, flush=True)
                    continue

                msg = email.message_from_bytes(raw_bytes)

                onderwerp = decode_header_value(msg.get("subject"))
                afzender = decode_header_value(msg.get("from"))
                tekst = extract_body(msg)

                mqtt_send({
                    "onderwerp": onderwerp,
                    "afzender": afzender,
                    "tekst": tekst,
                })

                # Stap 4 → UID opslaan zodat we nooit dubbel verwerken
                if not MARK_AS_READ:
                    processed_uids.add(uid)
                    save_uids(processed_uids)

            mail.logout()
            time.sleep(5)

        except Exception as e:
            print("IMAP fout:", e, flush=True)
            time.sleep(10)

# -------------------------------------------------------
# Start
# -------------------------------------------------------
if __name__ == "__main__":
    run_imap_loop()
