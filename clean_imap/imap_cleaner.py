import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json
import os

print("IMAP Cleaner: Python script started", flush=True)

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

print(
    "IMAP Cleaner: Options geladen "
    f"(imap_host={IMAP_HOST}, imap_user={IMAP_USER}, mqtt_host={MQTT_HOST}, mqtt_topic={MQTT_TOPIC})",
    flush=True,
)

# -------------------------------------------------------
# 1b. Persistent opslaan van verwerkte UID's
# -------------------------------------------------------
PROCESSED_UIDS_FILE = "/data/imap_processed_uids.json"

def load_processed_uids():
    if not os.path.exists(PROCESSED_UIDS_FILE):
        return set()
    try:
        with open(PROCESSED_UIDS_FILE, "r") as f:
            data = json.load(f)
        return set(data)
    except Exception as e:
        print("IMAP Cleaner: kon processed UIDs niet laden:", e, flush=True)
        return set()


def save_processed_uids(uids):
    try:
        with open(PROCESSED_UIDS_FILE, "w") as f:
            json.dump(list(uids), f)
    except Exception as e:
        print("IMAP Cleaner: kon processed UIDs niet opslaan:", e, flush=True)


processed_uids = load_processed_uids()
print(f"IMAP Cleaner: {len(processed_uids)} eerder verwerkte UID(s) geladen", flush=True)

# -------------------------------------------------------
# 2. HTML opschonen
# -------------------------------------------------------
def html_to_text(html):
    """Converteert HTML naar platte tekst."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")

    clean = "\n".join(
        line.strip() for line in text.splitlines() if line.strip()
    )
    return clean


# -------------------------------------------------------
# 3. Header (subject, from, etc.) veilig decoderen
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
        except Exception:
            decoded += str(text)

    return decoded


# -------------------------------------------------------
# 4. Beste leesbare e-mailtekst ophalen (plain > html)
# -------------------------------------------------------
def extract_body(msg):
    text_plain = None
    text_html = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"

            if payload is None:
                continue

            if ctype == "text/plain" and text_plain is None:
                try:
                    text_plain = payload.decode(charset, errors="replace")
                except Exception:
                    pass

            elif ctype == "text/html" and text_html is None:
                try:
                    text_html = payload.decode(charset, errors="replace")
                except Exception:
                    pass

    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        ctype = msg.get_content_type()

        if payload:
            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = None

            if ctype == "text/plain":
                text_plain = decoded
            elif ctype == "text/html":
                text_html = decoded

    if text_plain:
        return text_plain
    if text_html:
        return html_to_text(text_html)

    return "(Geen leesbare inhoud gevonden)"


# -------------------------------------------------------
# 5. MQTT client (API v2 – future-proof)
# -------------------------------------------------------
mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    transport="tcp",
    callback_api_version=2,
)

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

print("IMAP Cleaner: MQTT client gestart", flush=True)


# -------------------------------------------------------
# 6. MQTT publish functie
# -------------------------------------------------------
def mqtt_send(data):
    try:
        print("IMAP Cleaner: MQTT publish:", data, flush=True)
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.publish(MQTT_TOPIC, json.dumps(data))
        mqtt_client.disconnect()
    except Exception as e:
        print("MQTT fout:", e, flush=True)


# -------------------------------------------------------
# 7. Hulpfunctie om UID uit FETCH-respons te halen
# -------------------------------------------------------
def parse_uid_from_response(fetch_header_bytes):
    """
    Voorbeeld header:
    b'1 (UID 12345 RFC822 {3423}'
    We trekken hier '12345' uit.
    """
    try:
        header_str = fetch_header_bytes.decode("utf-8", errors="ignore")
        # Zoek 'UID <nummer>'
        for part in header_str.split():
            if part.upper() == "UID":
                # Volgende item is het UID nummer
                # we pakken de index van 'UID' en lezen het volgende element
                parts = header_str.split()
                for i, p in enumerate(parts):
                    if p.upper() == "UID" and i + 1 < len(parts):
                        return parts[i + 1]
        return None
    except Exception as e:
        print("IMAP Cleaner: kon UID niet parsen uit:", fetch_header_bytes, "fout:", e, flush=True)
        return None


# -------------------------------------------------------
# 8. Main loop – IMAP uitlezen (readonly, zodat mails ongelezen blijven)
# -------------------------------------------------------
def run_imap_loop():
    global processed_uids

    print("IMAP Cleaner: IMAP loop gestart (readonly, mails blijven ongelezen)", flush=True)

    while True:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(IMAP_USER, IMAP_PASS)

            # readonly=True zorgt ervoor dat IMAP geen \Seen flag zet
            mail.select("INBOX", readonly=True)

            status, ids = mail.search(None, "UNSEEN")
            print("IMAP Cleaner: UNSEEN search:", status, ids, flush=True)

            if status == "OK":
                id_list = ids[0].split()
                print(f"IMAP Cleaner: {len(id_list)} UNSEEN berichten gevonden", flush=True)

                for msg_id in id_list:
                    # Haal zowel UID als volledige mail op, maar in readonly-mode
                    res, msg_data = mail.fetch(msg_id, "(UID RFC822)")

                    if res != "OK" or not msg_data or len(msg_data) < 1:
                        print("IMAP Cleaner: fetch mislukt voor ID", msg_id, "res=", res, flush=True)
                        continue

                    header_part = msg_data[0][0]
                    raw_email = msg_data[0][1]

                    if not header_part or not raw_email:
                        print("IMAP Cleaner: lege fetch data voor ID", msg_id, flush=True)
                        continue

                    uid = parse_uid_from_response(header_part)
                    if not uid:
                        print("IMAP Cleaner: geen UID gevonden voor ID", msg_id, flush=True)
                        continue

                    if uid in processed_uids:
                        print(f"IMAP Cleaner: UID {uid} al eerder verwerkt, overslaan", flush=True)
                        continue

                    msg = email.message_from_bytes(raw_email)

                    onderwerp = decode_header_value(msg.get("subject"))
                    afzender = decode_header_value(msg.get("from"))
                    tekst = extract_body(msg)

                    payload = {
                        "onderwerp": onderwerp,
                        "afzender": afzender,
                        "tekst": tekst,
                    }

                    mqtt_send(payload)

                    # Markeer deze UID als verwerkt (maar laat mail ongelezen op de server)
                    processed_uids.add(uid)
                    save_processed_uids(processed_uids)
                    print(f"IMAP Cleaner: UID {uid} toegevoegd aan processed lijst", flush=True)

            mail.logout()
            time.sleep(5)

        except Exception as e:
            print("IMAP fout:", e, flush=True)
            time.sleep(10)


# -------------------------------------------------------
# 9. Start script
# -------------------------------------------------------
if __name__ == "__main__":
    run_imap_loop()
