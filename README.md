# 📬 IMAP Cleaner Add-on voor Home Assistant

De **IMAP Cleaner** add-on verwerkt automatisch inkomende e-mails via IMAP en publiceert de volledig gedecodeerde tekst (plain-text versie) naar een MQTT-topic.  
Hiermee kun je gemakkelijk automations maken gebaseerd op e-mails, zoals:

- 🔔 Service-meldingen  
- 🔒 Registratiemeldingen  
- 🔧 Alles wat jij uit e-mail wilt automatiseren  

De add-on ondersteunt:

- **Gmail**
- **iCloud**
- **Outlook / Office365**
- **Ziggo/KPN IMAP**
- **Elke standaard IMAP-server**

En verwerkt automatisch:

- Base64 inhoud  
- HTML → platte tekst  
- Multipart e-mails  
- Unicode headers  
- Onleesbare formats  

MQTT publicatie is gestandaardiseerd, zodat jouw Home Assistant sensors altijd netjes bijgewerkt worden.

---

# 🚀 Installatie via Custom Repository

1. Open Home Assistant  
2. Ga naar **Instellingen → Add-ons → Add-on Store**  
3. Klik rechtsboven op **⋮ → Repositories**  
4. Voeg jouw repository toe:

https://github.com/Sarnog/Imap-Cleaner

5. Klik op **Add**  
6. Je ziet nu de add-on **IMAP Cleaner** in de lijst  
7. Klik op **Installeren**  

---

# ⚙️ Configuratie van de Add-on

Wanneer je de add-on opent krijg je een aantal instellingen:

| Instelling | Type | Omschrijving |
|-----------|------|--------------|
| `imap_host` | Tekst | De IMAP-server, bv. `imap.gmail.com` |
| `imap_port` | Nummer | De IMAP-poort, meestal **993** |
| `imap_username` | Tekst | Je e-mailadres |
| `imap_password` | Wachtwoord | Wordt met sterretjes weergegeven |
| `mqtt_host` | Tekst | MQTT broker hostnaam, bv. `core-mosquitto` |
| `mqtt_port` | Nummer | MQTT poort, meestal **1883** |
| `mqtt_username` | Tekst | Gebruikersnaam |
| `mqtt_password` | Wachtwoord | Wordt met sterretjes weergegeven |
| `mqtt_topic` | Tekst | Topic waarop e-maildata wordt gepubliceerd |
| `mark_as_read` | True/False | Of e-mails gelezen worden gemarkeerd in je mailbox |

### ✨ Wachtwoordvelden

- verborgen achter **asterisks**
- zichtbaar te maken via een **oog-icoon**

---

# 📡 MQTT Data-output

Elke e-mail die de add-on verwerkt wordt gepubliceerd op:

mail/decoded

Payload-voorbeeld:

```json
{
  "onderwerp": "Welcome to the club",
  "afzender": "no-reply@club.com",
  "tekst": "Dear Member,..."
}
```

# 🧩 Integratie met Home Assistant (MQTT Sensor)

#### Voeg het onderstaande toe aan je configuratie (configuration.yaml), om de add-on te integreren in Home Assistant:

```
mqtt:
  sensor:
    - name: "Clean IMAP Add-on Resultaten"
      unique_id: "clean_imap_addon_resultaten"
      state_topic: "mail/decoded"
      value_template: >
        {{ value_json.tekst[:200] ~ '...' }}
      json_attributes_topic: "mail/decoded"
      json_attributes_template: >
        {
          "tekst": {{ value_json.tekst | tojson }},
          "onderwerp": {{ value_json.onderwerp | tojson }},
          "afzender": {{ value_json.afzender | tojson }}
        }
```


# English version:


# 📬 IMAP Cleaner Add-on for Home Assistant

The **IMAP Cleaner** add-on automatically processes incoming emails using IMAP and publishes a fully decoded plain-text version to an MQTT topic.  
This allows you to easily create Home Assistant automations triggered by email content, such as:

- 🔔 Service notifications  
- 🔒 Verification or registration emails  
- 🔧 Any automation that relies on email events  

The add-on supports:

- **Gmail**
- **iCloud**
- **Outlook / Office365**
- **Ziggo / KPN IMAP**
- **Any standard IMAP server**

It automatically handles:

- Base64 content  
- HTML → clean plain text  
- Multipart emails  
- Unicode headers  
- Irregular or malformed formats  

MQTT publishing is standardized, ensuring your Home Assistant sensors always receive clean, structured data.

---

# 🚀 Installation via Custom Repository

1. Open **Home Assistant**
2. Go to **Settings → Add-ons → Add-on Store**
3. Click **⋮ (top right) → Repositories**
4. Add the following repository URL:

https://github.com/Sarnog/Imap-Cleaner

5. Click **Add**
6. The add-on **IMAP Cleaner** will now appear in the list  
7. Click **Install**

---

# ⚙️ Add-on Configuration

Inside the add-on settings, you will find these options:

| Setting | Type | Description |
|--------|------|-------------|
| `imap_host` | Text | IMAP server, e.g., `imap.gmail.com` |
| `imap_port` | Number | IMAP port, typically **993** |
| `imap_username` | Text | Your email address |
| `imap_password` | Password | Hidden by default |
| `mqtt_host` | Text | MQTT broker hostname, e.g., `core-mosquitto` |
| `mqtt_port` | Number | MQTT port, usually **1883** |
| `mqtt_username` | Text | Username for MQTT |
| `mqtt_password` | Password | Hidden by default |
| `mqtt_topic` | Text | MQTT topic where decoded mail is published |
| `mark_as_read` | Boolean | Whether emails should be marked as read |

### ✨ Password Fields

- Shown as **asterisks**
- Can be revealed using a **show/hide eye icon**

---

# 📡 MQTT Output Format

Every processed email is published to the following MQTT topic:

mail/decoded

Example JSON payload:

```json
{
  "onderwerp": "Welcome to the club",
  "afzender": "no-reply@club.com",
  "tekst": "Dear Member,..."
}
```

# 🧩 Integrating With Home Assistant (MQTT Sensor)

To use the add-on output in automations, create an MQTT sensor.

Add the following to your Home Assistant configuration (configuration.yaml or your package-based structure):

```
mqtt:
  sensor:
    - name: "Clean IMAP Add-on Results"
      unique_id: "clean_imap_addon_resultaten"
      state_topic: "mail/decoded"
      value_template: >
        {{ value_json.tekst[:200] ~ '...' }}
      json_attributes_topic: "mail/decoded"
      json_attributes_template: >
        {
          "tekst": {{ value_json.tekst | tojson }},
          "onderwerp": {{ value_json.onderwerp | tojson }},
          "afzender": {{ value_json.afzender | tojson }}
        }
```
