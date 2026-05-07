# RoadScan AI v4 — Complete Setup Guide
## From Scratch to Running in 8 Steps

---

## What's New in v4

| Feature | v3 | v4 |
|---|---|---|
| WhatsApp alerts | ❌ | ✅ Green API (free 500 msgs/month) |
| Depth — Upload | Geometry only | ✅ **MiDaS** neural depth (accurate) |
| Depth — Live / USB | Geometry | ✅ **Geometry** (instant, no GPU needed) |
| USB camera (Quantum QHM-495LM) | ❌ | ✅ Dedicated tab + API endpoint |
| Depth badge in UI | ❌ | ✅ Shows "🧠 MiDaS" or "📐 Geometry" |
| WhatsApp test endpoint | ❌ | ✅ `/api/whatsapp/test` |

---

## Prerequisites (install these once)

| Tool | Download |
|---|---|
| Python 3.10+ | https://www.python.org/downloads/ |
| Node.js 18+ | https://nodejs.org/ |
| Git (optional) | https://git-scm.com/ |

---

## STEP 1 — Get your YOLOv8 pothole model

Place your trained weights file inside:
```
roadscan-v4/backend/Weights/best.pt
```

The backend will auto-detect any `.pt` file in that folder at startup.

If you don't have one yet, you can download a public pothole model:
```bash
# Example using pip + ultralytics CLI
pip install ultralytics
yolo export model=yolov8n.pt   # placeholder — replace with your pothole model
```

---

## STEP 2 — Set up the Python backend

Open a terminal and run:

```bash
cd roadscan-v4/backend

# Create a virtual environment
python -m venv venv

# Activate it:
# Windows:
venv\Scripts\activate
# Mac / Linux:
source venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

> **Note:** `requirements.txt` includes `torch`, `torchvision`, and `timm` for MiDaS.
> First install may take 3–5 minutes as PyTorch downloads (~1 GB).

---

## STEP 3 — Find your laptop's local IP address

You need this so your phone/tablet can reach the backend.

**Windows:**
```
ipconfig
```
Look for **IPv4 Address** under your Wi-Fi adapter (e.g. `192.168.1.42`).

**Mac / Linux:**
```bash
ifconfig | grep "inet "
```

Note it down — you'll use it in Step 5.

---

## STEP 4 — Configure alerts in `backend/app.py`

Open `roadscan-v4/backend/app.py` and fill in at least one alert channel.

### 4a — WhatsApp via Green API (recommended — FREE)

1. Go to **https://green-api.com** → Sign up for the free plan
2. Click **Create Instance** → a new instance appears in your dashboard
3. Copy the **Instance ID** and **API Token**
4. Open WhatsApp on your phone and **scan the QR code** shown in the dashboard
5. Fill in `app.py`:

```python
WHATSAPP_ENABLED       = True
WHATSAPP_INSTANCE_ID   = "1234567890"         # from Green API dashboard
WHATSAPP_API_TOKEN     = "your_token_here"    # from Green API dashboard
WHATSAPP_PHONE_TO      = "919876543210"       # country code + number, no + or spaces
                                               # India example: 91 + 10-digit number
```

> To test your WhatsApp connection without doing a full scan:
> After starting the backend, POST to `http://127.0.0.1:5000/api/whatsapp/test`

---

### 4b — Telegram (also free)

1. Message **@BotFather** on Telegram → `/newbot` → follow steps → copy token
2. Add your bot to a group → visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat_id`

```python
TELEGRAM_ENABLED = True
TELEGRAM_TOKEN   = "7123456789:AAFxxxxxxxxxxxxxxxxx"
TELEGRAM_CHAT_ID = "-1001234567890"
```

---

### 4c — Gmail (free with App Password)

1. Go to **myaccount.google.com/apppasswords**
2. Select app: "Mail" → device: "Other" → generate → copy 16-char password

```python
GMAIL_ENABLED  = True
GMAIL_FROM     = "you@gmail.com"
GMAIL_APP_PASS = "abcd efgh ijkl mnop"    # 16-char app password (spaces OK)
GMAIL_TO       = "authority@municipality.gov.in"
```

---

## STEP 5 — Update the frontend API URL

Open `roadscan-v4/frontend/src/App.js`, line 12:

```js
const API = "http://127.0.0.1:5000";   // ← CHANGE THIS
```

**On the same laptop:** keep `http://127.0.0.1:5000`

**Accessing from phone on same Wi-Fi:**
```js
const API = "http://192.168.1.42:5000";   // use YOUR laptop IP from Step 3
```

---

## STEP 6 — (Optional) Enable HTTPS for mobile live camera

The browser camera API requires HTTPS on mobile. Skip this if you only use Upload or USB tabs.

```bash
# Inside backend/ with venv active:
python generate_cert.py
```

Then in `backend/app.py`:
```python
USE_HTTPS = True
```

On your phone — visit `https://YOUR_IP:5000` once → tap **Advanced → Proceed anyway**.

---

## STEP 7 — Set up the Quantum QHM-495LM USB Camera

The QHM-495LM is a standard UVC (USB Video Class) webcam — no special driver needed on Windows 10/11, Ubuntu 20+, or macOS.

1. Plug in the QHM-495LM via USB
2. Check which camera index it gets:
   - On Windows: Device Manager → Imaging devices
   - On Linux: `ls /dev/video*`
3. In `backend/app.py`, set:

```python
USB_CAM_INDEX = 0    # change to 1 if your laptop webcam takes index 0
```

4. Click **USB Cam** tab in the frontend → set index → **Capture & Detect**

> The backend also exposes `GET /api/usb/info` which lists all detected cameras automatically.

---

## STEP 8 — Open firewall ports (Windows only — run as Administrator)

```
netsh advfirewall firewall add rule name="RoadScan 3000" dir=in action=allow protocol=TCP localport=3000
netsh advfirewall firewall add rule name="RoadScan 5000" dir=in action=allow protocol=TCP localport=5000
```

---

## Running the Project

### Terminal 1 — Start backend

```bash
cd roadscan-v4/backend
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

python app.py
```

You should see:
```
============================================================
  RoadScan AI Backend v4
============================================================
  Laptop URL :  http://127.0.0.1:5000
  Mobile URL :  http://192.168.x.x:5000
  Model      :  best.pt
  Depth      :  MiDaS (upload) | Geometry (live/USB)
  WhatsApp   :  ✅ enabled
  USB Camera :  index 0 (Quantum QHM-495LM)
============================================================
```

### Terminal 2 — Start frontend

```bash
cd roadscan-v4/frontend
npm install          # first time only (~2 minutes)
npm start
```

Opens at **http://localhost:3000**

---

## Using the Three Tabs

### 📷 Live Tab
- Uses your phone/laptop camera
- Scans every 2.5 seconds automatically
- Depth method: **Geometry** (instant, no GPU)
- Requires HTTPS on mobile browser

### ⬆ Upload Tab
- Upload any road photo (JPG/PNG)
- Depth method: **MiDaS neural network** (more accurate depth estimation)
- MiDaS model (~80 MB) downloads automatically on first use

### 🔌 USB Cam Tab
- Captures one frame from Quantum QHM-495LM or any USB webcam
- Depth method: **Geometry** (same as live)
- Select camera index from dropdown

---

## Sending WhatsApp Alerts

1. Run a scan in any tab
2. When potholes are detected, click **"🚨 Send Alert"**
3. The alert is sent to your WhatsApp number including:
   - Severity level and count
   - GPS coordinates with Google Maps link
   - Repair material estimate in ₹
   - The annotated photo attachment

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Health check, version, alert status |
| POST | `/api/detect/frame` | Live frame detection (geometry depth) |
| POST | `/api/detect/image` | Upload detection (MiDaS depth) |
| POST | `/api/usb/capture` | Capture from USB camera |
| GET | `/api/usb/info` | List available cameras |
| POST | `/api/alert` | Send alert (WhatsApp + Telegram + Gmail) |
| POST | `/api/whatsapp/test` | Test WhatsApp connection |
| GET | `/api/history` | Last 50 detection logs |
| GET | `/api/stats` | Aggregate statistics |

---

## Every Time You Run

```bash
# Terminal 1
cd roadscan-v4/backend
venv\Scripts\activate        # Windows
python app.py

# Terminal 2
cd roadscan-v4/frontend
npm start
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `No .pt file found` | Put `best.pt` inside `backend/Weights/` |
| WhatsApp not sending | Check Instance ID + Token, rescan QR if phone disconnected |
| USB camera not found | Try index 1 or 2; replug USB; check Device Manager |
| MiDaS slow first run | It downloads ~80 MB on first upload — subsequent runs are instant |
| Mobile camera blocked | Enable HTTPS (Step 6) or use Upload/USB tab |
| `npm: command not found` | Install Node.js from nodejs.org |
| Backend offline in UI | Change `API` in App.js to your laptop IP (Step 5) |

---

## Project Structure

```
roadscan-v4/
├── backend/
│   ├── app.py                  ← Main Flask server (edit config here)
│   ├── requirements.txt        ← Python dependencies
│   ├── generate_cert.py        ← HTTPS self-signed cert generator
│   └── Weights/
│       └── best.pt             ← Your YOLOv8 pothole model (add this!)
└── frontend/
    ├── public/
    │   └── index.html
    └── src/
        ├── App.js              ← React UI (edit API URL here)
        ├── App.css             ← Styles
        └── index.js
```

---

*RoadScan AI v4 — YOLOv8 · MiDaS · Green API WhatsApp · Quantum QHM-495LM · IRC/PWD*
*TSSM BSCOER ENTC 2025-26*
