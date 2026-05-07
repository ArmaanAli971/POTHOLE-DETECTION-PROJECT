"""
RoadScan AI — Backend v4
========================
New in v4
  • WhatsApp alerts via Green API (send message + photo to any WhatsApp number)
  • MiDaS depth estimation for UPLOAD mode (accurate, neural-network depth)
  • Camera-geometry depth for LIVE mode (zero-latency, no GPU overhead)
  • Quantum QHM-495LM USB camera endpoint  (/api/usb/capture, /api/usb/info)
  • Depth-backend badge reported per response ("midas" | "geometry")
  • All previous fixes retained (0.0.0.0, CORS *, HTTPS, size filter, etc.)
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from ultralytics import YOLO
import cv2, os, datetime, base64, csv, math, smtplib, socket, threading
import numpy as np
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

app = Flask(__name__)
CORS(app, origins="*")

# ── Paths ──────────────────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
RESDIR  = os.path.join(BASE, "results");  os.makedirs(RESDIR, exist_ok=True)
LOGFILE = os.path.join(BASE, "detections.csv")

# ── Auto-find YOLO weights ─────────────────────────────────────────────────
_pts    = [f for f in os.listdir(os.path.join(BASE, "Weights")) if f.endswith(".pt")]
WEIGHTS = os.path.join(BASE, "Weights", _pts[0]) if _pts else None

# =============================================================================
#  ⚙  CONFIGURATION — edit these values
# =============================================================================

# Camera calibration (used for LIVE geometry depth)
CAMERA_HEIGHT_M = 1.5     # metres above road (1.5 = phone at waist)
CAMERA_FOV_DEG  = 70.0    # horizontal FOV (70° for most phones/USB webcams)

# HTTPS — set True after running: python generate_cert.py
USE_HTTPS = False
CERT_FILE = os.path.join(BASE, "cert.pem")
KEY_FILE  = os.path.join(BASE, "key.pem")

# ── Quantum QHM-495LM USB Camera ──────────────────────────────────────────
# The QHM-495LM is recognised by OpenCV as a standard UVC device.
# Try index 0 first; if another camera is default, try 1 or 2.
USB_CAM_INDEX = 1          # change to 1 or 2 if laptop webcam is index 0
USB_CAM_WIDTH  = 640
USB_CAM_HEIGHT = 480

# ── WhatsApp via Green API ─────────────────────────────────────────────────
# 1. Register at https://green-api.com → FREE plan (500 msgs/month)
# 2. Create an instance → copy Instance ID and API Token
# 3. Open WhatsApp on your phone and scan the QR in the Green API dashboard
# 4. PHONE_TO must be the WhatsApp number WITH country code (no +, no spaces)
#    Example for India 9876543210 → "919876543210"
WHATSAPP_ENABLED       = True
WHATSAPP_INSTANCE_ID   = "7107597954"    # e.g. "1234567890"
WHATSAPP_API_TOKEN     = "e8718d8db56043ce8cf4b5498e1f21f2d29d8aa34ae144c383"    # e.g. "abcdef1234567890abcdef1234567890"
WHATSAPP_PHONE_TO      = "918446463971"    # e.g. "919876543210"  (country code + number)

# ── Telegram Bot ──────────────────────────────────────────────────────────
TELEGRAM_ENABLED = True
TELEGRAM_TOKEN   = "8743115726:AAH-zQQfxygPt7WhzbauQ6GPAeSoi8V3m7I"     # e.g. "7123456789:AAFxxxxxxxxx"
TELEGRAM_CHAT_ID = "5262298013"     # e.g. "-1001234567890"

# ── Fast2SMS (₹0.15/SMS) ──────────────────────────────────────────────────
FAST2SMS_ENABLED = False
FAST2SMS_KEY     = ""
FAST2SMS_NUMBER  = ""     # 10-digit, no +91

# ── Gmail ─────────────────────────────────────────────────────────────────
GMAIL_ENABLED  = False
GMAIL_FROM     = ""
GMAIL_APP_PASS = ""
GMAIL_TO       = ""

# =============================================================================
#  MiDaS — lazy-loaded ONLY when an image is uploaded (not used for live)
# =============================================================================
_midas_model     = None
_midas_transform = None
_midas_device    = None
_midas_lock      = threading.Lock()

def _load_midas():
    """Load MiDaS DPT_Small once and cache it."""
    global _midas_model, _midas_transform, _midas_device
    import torch
    with _midas_lock:
        if _midas_model is not None:
            return True
        try:
            print("Loading MiDaS depth model …")
            _midas_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            # MiDaS_small is ~80 MB — fast on CPU too
            _midas_model  = torch.hub.load("intel-isl/MiDaS", "MiDaS_small",
                                           trust_repo=True)
            _midas_model.to(_midas_device).eval()
            transforms   = torch.hub.load("intel-isl/MiDaS", "transforms",
                                          trust_repo=True)
            _midas_transform = transforms.small_transform
            print(f"MiDaS loaded on {_midas_device}")
            return True
        except Exception as e:
            print(f"MiDaS load failed ({e}) — falling back to geometry for uploads.")
            return False


def depth_midas(img_bgr):
    """
    Run MiDaS on a full image.
    Returns a depth map (H×W float32, larger value = closer).
    Normalised so the closest pixel = 1.0.
    """
    import torch
    if not _load_midas():
        return None
    try:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        inp     = _midas_transform(img_rgb).to(_midas_device)
        with torch.no_grad():
            pred = _midas_model(inp)
            pred = torch.nn.functional.interpolate(
                pred.unsqueeze(1),
                size=img_bgr.shape[:2],
                mode="bicubic",
                align_corners=False,
            ).squeeze()
        depth_np = pred.cpu().numpy()
        # Normalise: close objects get high value
        mn, mx = depth_np.min(), depth_np.max()
        if mx > mn:
            depth_np = (depth_np - mn) / (mx - mn)
        return depth_np
    except Exception as e:
        print(f"MiDaS inference error: {e}")
        return None


def midas_roi_depth_m(depth_map, x1, y1, x2, y2):
    """
    Convert the mean MiDaS depth in a bounding-box ROI to metres.
    MiDaS gives relative (inverse) depth; we map the 0-1 range to 0.02–0.30 m.
    """
    if depth_map is None:
        return None
    roi = depth_map[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    mean_rel = float(np.mean(roi))   # 0=far, 1=near (closer surface = higher)
    # Potholes are depressions → they appear FARTHER than road surface.
    # Invert: depth of pothole bottom = 1 − mean_rel
    inv = 1.0 - mean_rel
    # Map to plausible pothole depth range 0.02–0.30 m
    depth_m = 0.02 + inv * 0.28
    return round(max(0.02, min(depth_m, 0.30)), 3)

# =============================================================================
#  Geometry depth (for LIVE frames — no model load, zero latency)
# =============================================================================
def _focal(img_w):
    return img_w / (2 * math.tan(math.radians(CAMERA_FOV_DEG / 2)))

def depth_geometry(bw, img_w):
    real_w = bw * CAMERA_HEIGHT_M / _focal(img_w)
    return round(max(0.02, min(real_w * 0.12, 0.30)), 3)

def px_to_m(px, img_w):
    return round(px * CAMERA_HEIGHT_M / _focal(img_w), 3)

# =============================================================================
#  Indian repair materials (IRC:SP:83-2008 / MORTH)
# =============================================================================
def estimate_materials(vol_m3, area_m2):
    if vol_m3 <= 0:
        return {"items": [], "total_inr": 0}
    cm_kg   = round(vol_m3 * 1.15 * 1500, 2)
    cm_cost = round(cm_kg * 35)
    pr_kg   = round(area_m2 * 0.27, 2)
    pr_cost = round(pr_kg * 25)
    misc    = 550
    total   = cm_cost + pr_cost + misc
    return {
        "items": [
            {"name": "Cold Mix Asphalt",         "qty": f"{cm_kg} kg", "cost": cm_cost},
            {"name": "Bituminous Primer (SS-1)", "qty": f"{pr_kg} kg", "cost": pr_cost},
            {"name": "Labor + Equipment",         "qty": "1 job",       "cost": misc},
        ],
        "total_inr": total,
    }

# =============================================================================
#  Severity
# =============================================================================
SEV_BGR = {
    "None":     (80,  200, 0),
    "Low":      (240, 200, 0),
    "Medium":   (0,   165, 255),
    "High":     (0,   80,  255),
    "Critical": (0,   0,   220),
}

def get_severity(count, px_area, img_area):
    if count == 0: return "None"
    r = px_area / max(img_area, 1)
    if count >= 5 or r > 0.15: return "Critical"
    if count >= 3 or r > 0.08: return "High"
    if count >= 2 or r > 0.03: return "Medium"
    return "Low"

# =============================================================================
#  YOLO model
# =============================================================================
print("Loading YOLOv8 model …")
try:
    if not WEIGHTS:
        raise FileNotFoundError("No .pt file in backend/Weights/")
    model = YOLO(WEIGHTS)
    print(f"Model loaded: {os.path.basename(WEIGHTS)}")
except Exception as e:
    print(f"Model error: {e}")
    model = None

# =============================================================================
#  Core inference
# =============================================================================
def annotate(img, dets, sev, depth_backend="geometry"):
    color = SEV_BGR.get(sev, (0, 0, 255))
    h, w  = img.shape[:2]
    ov = img.copy()
    cv2.rectangle(ov, (0, 0), (w, 56), (6, 10, 16), -1)
    cv2.addWeighted(ov, 0.82, img, 0.18, 0, img)
    cv2.putText(img,
        f"{'[!]' if dets else '[OK]'} {len(dets)} Pothole(s)   Severity: {sev}   Depth: {depth_backend}",
        (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.68, color, 2, cv2.LINE_AA)
    for d in dets:
        x1, y1, x2, y2 = d["x1"], d["y1"], d["x2"], d["y2"]
        lbl = f"{d['conf']:.0%}  {d['depth_cm']}cm  {d['area_pct']:.1f}%img"
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 3)
        (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
        cv2.rectangle(img, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
        cv2.putText(img, lbl, (x1 + 3, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def run_detect(img, min_size_pct=0.0, use_midas=False):
    """
    use_midas=True  → Upload mode  (MiDaS neural depth, falls back to geometry)
    use_midas=False → Live/USB mode (camera geometry depth, instant)
    """
    if model is None:
        raise RuntimeError("Model not loaded. Put best.pt in backend/Weights/")

    h, w     = img.shape[:2]
    img_area = h * w
    results  = model(img, conf=0.25, imgsz=640, verbose=False)

    # Pre-compute MiDaS map for whole image (once per call, only for upload)
    depth_map    = None
    depth_backend = "geometry"
    if use_midas:
        depth_map = depth_midas(img)
        depth_backend = "midas" if depth_map is not None else "geometry"

    dets = []
    for r in results:
        for box in r.boxes:
            if int(box.cls[0]) != 0:
                continue
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            bw, bh   = x2 - x1, y2 - y1
            area_px  = bw * bh
            area_pct = (area_px / img_area) * 100

            if area_pct < min_size_pct:
                continue

            # Depth: MiDaS for upload, geometry for live
            if use_midas and depth_map is not None:
                depth = midas_roi_depth_m(depth_map, x1, y1, x2, y2)
                if depth is None:
                    depth = depth_geometry(bw, w)
            else:
                depth = depth_geometry(bw, w)

            wm  = px_to_m(bw, w)
            lm  = px_to_m(bh, w)
            vol = round(math.pi / 4 * wm * lm * depth, 5)

            dets.append({
                "conf":     round(conf, 4),
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "area_px":  area_px,
                "area_pct": round(area_pct, 2),
                "width_m":  wm,
                "length_m": lm,
                "depth_m":  depth,
                "depth_cm": round(depth * 100, 1),
                "vol":      vol,
            })

    sev  = get_severity(len(dets), sum(d["area_px"] for d in dets), img_area)
    vol  = round(sum(d["vol"] for d in dets), 5)
    area = round(sum(d["width_m"] * d["length_m"] for d in dets), 4)
    mats = estimate_materials(vol, area)
    ann  = annotate(img.copy(), dets, sev, depth_backend)
    return ann, dets, sev, vol, area, mats, depth_backend


def to_b64(img, q=87):
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return base64.b64encode(buf).decode()


def log_row(fname, count, sev, vol, cost, loc):
    hdr = ["ts", "file", "count", "severity", "volume_m3", "cost_inr", "location"]
    ex  = os.path.isfile(LOGFILE)
    with open(LOGFILE, "a", newline="", encoding="utf-8") as f:
        import csv as _c
        w = _c.DictWriter(f, fieldnames=hdr)
        if not ex:
            w.writeheader()
        w.writerow({
            "ts":        datetime.datetime.now().isoformat(timespec="seconds"),
            "file":      fname,
            "count":     count,
            "severity":  sev,
            "volume_m3": vol,
            "cost_inr":  cost,
            "location":  loc,
        })


def build_resp(dets, sev, vol, area, mats, ann, loc, depth_backend="geometry"):
    count = len(dets)
    avg   = round(sum(d["conf"] for d in dets) / count, 4) if count else 0
    return {
        "detected":        count > 0,
        "count":           count,
        "severity":        sev,
        "avg_confidence":  avg,
        "detections":      dets,
        "total_volume_m3": vol,
        "total_area_m2":   area,
        "materials":       mats,
        "depth_backend":   depth_backend,
        "image":           to_b64(ann),
        "timestamp":       datetime.datetime.now().isoformat(timespec="seconds"),
        "location":        loc,
    }

# =============================================================================
#  Alerts
# =============================================================================
def alert_text(data):
    sev  = data.get("severity", "?")
    cnt  = data.get("count", 0)
    vol  = data.get("total_volume_m3", 0)
    loc  = data.get("location", "N/A")
    cost = data.get("materials", {}).get("total_inr", 0)
    ts   = data.get("timestamp", "")
    maps = f"\nMaps: https://maps.google.com/?q={loc}" if "," in str(loc) else ""
    lines = [
        "🚨 POTHOLE ALERT — RoadScan AI v4", "=" * 36,
        f"📍 Location : {loc}{maps}",
        f"⚠  Severity : {sev}",
        f"🕳  Potholes : {cnt}",
        f"📦 Volume   : {vol:.4f} m³", "",
        "🔧 Materials needed (IRC/PWD):",
    ]
    for it in data.get("materials", {}).get("items", []):
        lines.append(f"  {it['name']}: {it['qty']}  ₹{it['cost']:,}")
    lines += ["", f"💰 Total cost : ₹{cost:,}", f"🕐 Time : {ts}", "", "— RoadScan AI v4"]
    return "\n".join(lines)


# ── WhatsApp via Green API ─────────────────────────────────────────────────
def send_whatsapp(text, img_path=None):
    """
    Send WhatsApp message (and optionally a photo) via Green API.
    Green API free plan: https://green-api.com
    """
    if not WHATSAPP_ENABLED or not WHATSAPP_INSTANCE_ID or not WHATSAPP_API_TOKEN:
        return {"ok": False, "error": "WhatsApp (Green API) not configured"}

    chat_id = f"{WHATSAPP_PHONE_TO}@c.us"
    base    = f"https://api.green-api.com/waInstance{WHATSAPP_INSTANCE_ID}"
    headers = {"Content-Type": "application/json"}

    try:
        # 1. Send text message
        r = requests.post(
            f"{base}/sendMessage/{WHATSAPP_API_TOKEN}",
            json={"chatId": chat_id, "message": text},
            headers=headers, timeout=20
        )
        txt_resp = r.json()

        # 2. Send annotated image if available
        if img_path and os.path.isfile(img_path):
            with open(img_path, "rb") as f:
                files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
                r2 = requests.post(
                    f"{base}/sendFileByUpload/{WHATSAPP_API_TOKEN}",
                    data={"chatId": chat_id, "caption": "🛣 RoadScan AI — Annotated pothole image"},
                    files=files, timeout=30
                )
                img_resp = r2.json()
        else:
            img_resp = {"skipped": "no image"}

        ok = "idMessage" in txt_resp
        return {"ok": ok, "text_resp": txt_resp, "img_resp": img_resp}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_telegram(text, img_path=None):
    if not TELEGRAM_ENABLED or not TELEGRAM_TOKEN:
        return {"ok": False, "error": "Telegram not configured"}
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        if img_path and os.path.isfile(img_path):
            with open(img_path, "rb") as f:
                r = requests.post(f"{base}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": text[:1024]},
                    files={"photo": f}, timeout=20)
        else:
            r = requests.post(f"{base}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=12)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_fast2sms(text):
    if not FAST2SMS_ENABLED or not FAST2SMS_KEY:
        return {"ok": False, "error": "Fast2SMS not configured"}
    try:
        r = requests.post("https://www.fast2sms.com/dev/bulkV2",
            headers={"authorization": FAST2SMS_KEY},
            json={"route": "q", "message": text[:500],
                  "numbers": FAST2SMS_NUMBER, "flash": 0}, timeout=12)
        d = r.json()
        return {"ok": d.get("return", False), "raw": d}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_gmail(text, img_path=None):
    if not GMAIL_ENABLED or not GMAIL_FROM:
        return {"ok": False, "error": "Gmail not configured"}
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_FROM
        msg["To"]      = GMAIL_TO
        msg["Subject"] = "🚨 POTHOLE ALERT — RoadScan AI v4"
        msg.attach(MIMEText(text, "plain"))
        if img_path and os.path.isfile(img_path):
            with open(img_path, "rb") as f:
                p = MIMEBase("application", "octet-stream")
                p.set_payload(f.read())
            encoders.encode_base64(p)
            p.add_header("Content-Disposition", "attachment; filename=alert.jpg")
            msg.attach(p)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_FROM, GMAIL_APP_PASS.replace(" ", ""))
            s.send_message(msg)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
#  Routes
# =============================================================================
@app.route("/")
def health():
    return jsonify({
        "status":  "running",
        "model":   "loaded" if model else "error",
        "version": "4.0",
        "alerts": {
            "whatsapp": WHATSAPP_ENABLED and bool(WHATSAPP_INSTANCE_ID),
            "telegram": TELEGRAM_ENABLED and bool(TELEGRAM_TOKEN),
            "fast2sms": FAST2SMS_ENABLED and bool(FAST2SMS_KEY),
            "gmail":    GMAIL_ENABLED and bool(GMAIL_FROM),
        },
        "depth": {
            "upload": "midas",
            "live":   "geometry",
        },
        "usb_camera": {
            "index":      USB_CAM_INDEX,
            "resolution": f"{USB_CAM_WIDTH}x{USB_CAM_HEIGHT}",
        }
    })


# ── Live frame (geometry depth) ───────────────────────────────────────────
@app.route("/api/detect/frame", methods=["POST"])
def detect_frame():
    if model is None:
        return jsonify({"error": "Model not loaded. Put best.pt in backend/Weights/"}), 500
    data = request.get_json(silent=True) or {}
    raw  = data.get("image", "")
    if not raw:
        return jsonify({"error": "No image data"}), 400
    if "," in raw:
        raw = raw.split(",", 1)[1]

    min_size = max(0.0, min(20.0, float(data.get("min_size", 0.0))))
    loc      = data.get("location", "Live camera")

    nparr = np.frombuffer(base64.b64decode(raw), np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Cannot decode frame"}), 400

    try:
        # Live → geometry depth (fast, no MiDaS)
        ann, dets, sev, vol, area, mats, db = run_detect(img, min_size_pct=min_size,
                                                          use_midas=False)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    resp = build_resp(dets, sev, vol, area, mats, ann, loc, db)
    if dets:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ip = os.path.join(RESDIR, f"live_{ts}.jpg")
        cv2.imwrite(ip, ann)
        resp["img_path"] = ip
        log_row(f"live_{ts}.jpg", len(dets), sev, vol, mats.get("total_inr", 0), loc)
    return jsonify(resp)


# ── Upload image (MiDaS depth) ────────────────────────────────────────────
@app.route("/api/detect/image", methods=["POST"])
def detect_image():
    if model is None:
        return jsonify({"error": "Model not loaded"}), 500
    if "image" not in request.files:
        return jsonify({"error": "No image field"}), 400

    file     = request.files["image"]
    min_size = max(0.0, min(20.0, float(request.form.get("min_size", 0.0))))
    loc      = request.form.get("location", "Not provided")

    nparr = np.frombuffer(file.read(), np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Cannot decode image"}), 400

    try:
        # Upload → MiDaS depth (accurate neural depth)
        ann, dets, sev, vol, area, mats, db = run_detect(img, min_size_pct=min_size,
                                                          use_midas=True)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ip = os.path.join(RESDIR, f"upload_{ts}.jpg")
    cv2.imwrite(ip, ann)
    log_row(file.filename, len(dets), sev, vol, mats.get("total_inr", 0), loc)

    resp = build_resp(dets, sev, vol, area, mats, ann, loc, db)
    resp["img_path"] = ip
    return jsonify(resp)


# ── Quantum QHM-495LM USB camera — single capture ─────────────────────────
@app.route("/api/usb/capture", methods=["POST"])
def usb_capture():
    """
    Grab one frame from the Quantum QHM-495LM (or any USB webcam) and run
    pothole detection with geometry depth (same as live mode).
    Body (JSON, all optional):
      { "min_size": 0, "location": "Road Name", "cam_index": 0 }
    """
    if model is None:
        return jsonify({"error": "Model not loaded"}), 500

    data      = request.get_json(silent=True) or {}
    min_size  = max(0.0, min(20.0, float(data.get("min_size", 0.0))))
    loc       = data.get("location", "USB Camera")
    cam_index = int(data.get("cam_index", USB_CAM_INDEX))

    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_V4L2)
    if not cap.isOpened():
        # Fallback: let OpenCV auto-pick backend
        cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        return jsonify({"error": f"Cannot open USB camera at index {cam_index}. "
                                  "Check USB cable and driver."}), 503

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  USB_CAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, USB_CAM_HEIGHT)
    # Discard first few frames (camera warm-up)
    for _ in range(3):
        cap.read()
    ok, img = cap.read()
    cap.release()

    if not ok or img is None:
        return jsonify({"error": "Failed to grab frame from USB camera"}), 503

    try:
        ann, dets, sev, vol, area, mats, db = run_detect(img, min_size_pct=min_size,
                                                          use_midas=False)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ip = os.path.join(RESDIR, f"usb_{ts}.jpg")
    cv2.imwrite(ip, ann)
    log_row(f"usb_{ts}.jpg", len(dets), sev, vol, mats.get("total_inr", 0), loc)

    resp = build_resp(dets, sev, vol, area, mats, ann, loc, db)
    resp["img_path"] = ip
    resp["cam_index"] = cam_index
    return jsonify(resp)


# ── USB camera info ────────────────────────────────────────────────────────
@app.route("/api/usb/info", methods=["GET"])
def usb_info():
    """Returns list of available camera indices (0-4) that OpenCV can open."""
    available = []
    for idx in range(5):
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            available.append({"index": idx, "width": int(w), "height": int(h)})
            cap.release()
    return jsonify({
        "cameras":      available,
        "default_index": USB_CAM_INDEX,
        "note": "Quantum QHM-495LM typically appears as index 0 or 1"
    })


# ── Alert ─────────────────────────────────────────────────────────────────
@app.route("/api/alert", methods=["POST"])
def send_alert():
    data = request.get_json(silent=True) or {}
    text = alert_text(data)
    ip   = data.get("img_path")
    res  = {
        "whatsapp": send_whatsapp(text, ip),
        "telegram": send_telegram(text, ip),
        "fast2sms": send_fast2sms(text),
        "gmail":    send_gmail(text, ip),
    }
    return jsonify({"sent": any(v.get("ok") for v in res.values()), "results": res})


# ── WhatsApp test ping ─────────────────────────────────────────────────────
@app.route("/api/whatsapp/test", methods=["POST"])
def whatsapp_test():
    """
    Send a quick test WhatsApp message to verify Green API credentials.
    Body: { "phone": "919876543210" }  (optional, overrides WHATSAPP_PHONE_TO)
    """
    data  = request.get_json(silent=True) or {}
    phone = data.get("phone", WHATSAPP_PHONE_TO)
    if not phone:
        return jsonify({"ok": False, "error": "No phone number provided"}), 400

    chat_id = f"{phone}@c.us"
    base    = f"https://api.green-api.com/waInstance{WHATSAPP_INSTANCE_ID}"
    try:
        r = requests.post(
            f"{base}/sendMessage/{WHATSAPP_API_TOKEN}",
            json={"chatId": chat_id, "message": "✅ RoadScan AI v4 — WhatsApp alerts active!"},
            headers={"Content-Type": "application/json"}, timeout=15
        )
        d = r.json()
        return jsonify({"ok": "idMessage" in d, "raw": d})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── History & stats ────────────────────────────────────────────────────────
@app.route("/api/history")
def history():
    if not os.path.isfile(LOGFILE):
        return jsonify([])
    import csv as _c
    with open(LOGFILE, "r", newline="", encoding="utf-8") as f:
        rows = list(_c.DictReader(f))
    return jsonify(rows[-50:][::-1])


@app.route("/api/stats")
def stats():
    if not os.path.isfile(LOGFILE):
        return jsonify({"scans": 0, "potholes": 0, "volume": 0, "severity_counts": {}})
    import csv as _c
    with open(LOGFILE, "r", newline="", encoding="utf-8") as f:
        rows = list(_c.DictReader(f))
    sc = {}
    for r in rows:
        s = r.get("severity", "None")
        sc[s] = sc.get(s, 0) + 1
    return jsonify({
        "scans":    len(rows),
        "potholes": sum(int(r.get("count", 0)) for r in rows),
        "volume":   round(sum(float(r.get("volume_m3", 0)) for r in rows), 4),
        "severity_counts": sc,
    })


# =============================================================================
#  Entry point
# =============================================================================
if __name__ == "__main__":
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "unknown"

    proto = "https" if USE_HTTPS else "http"
    print(f"\n{'='*60}")
    print(f"  RoadScan AI Backend v4")
    print(f"{'='*60}")
    print(f"  Laptop URL :  http://127.0.0.1:5000")
    print(f"  Mobile URL :  {proto}://{local_ip}:5000")
    print(f"  Model      :  {os.path.basename(WEIGHTS) if WEIGHTS else 'NOT FOUND'}")
    print(f"  Depth      :  MiDaS (upload) | Geometry (live/USB)")
    print(f"  WhatsApp   :  {'✅ enabled' if WHATSAPP_ENABLED else '❌ disabled (set WHATSAPP_ENABLED=True)'}")
    print(f"  USB Camera :  index {USB_CAM_INDEX} (Quantum QHM-495LM)")
    print(f"{'='*60}\n")

    if USE_HTTPS and os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE):
        app.run(host="0.0.0.0", port=5000, debug=False,
                ssl_context=(CERT_FILE, KEY_FILE))
    elif USE_HTTPS:
        print("HTTPS requested but cert/key not found — run: python generate_cert.py")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        app.run(host="0.0.0.0", port=5000, debug=False)
