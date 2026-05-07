import React, { useState, useEffect, useRef, useCallback } from "react";

// ══════════════════════════════════════════════════════════════════
//  ⚙  CHANGE THIS to your laptop's local IP address
//     Windows: run  ipconfig  → look for "IPv4 Address" under Wi-Fi
//     Mac/Linux: run  ifconfig | grep inet
//
//     Use https:// only after running generate_cert.py + USE_HTTPS=True
//     Use http://  otherwise (upload + USB tabs still work fully)
// ══════════════════════════════════════════════════════════════════
const API = "http://127.0.0.1:5000";   // ← UPDATE THIS

const SEV = {
  None:     { c:"#6b7280", bg:"rgba(107,114,128,.1)",  bo:"rgba(107,114,128,.2)",  icon:"✓",  lbl:"CLEAR"    },
  Low:      { c:"#3b82f6", bg:"rgba(59,130,246,.1)",   bo:"rgba(59,130,246,.22)",  icon:"◈",  lbl:"LOW"      },
  Medium:   { c:"#f59e0b", bg:"rgba(245,158,11,.1)",   bo:"rgba(245,158,11,.25)",  icon:"⚠",  lbl:"MEDIUM"   },
  High:     { c:"#f97316", bg:"rgba(249,115,22,.1)",   bo:"rgba(249,115,22,.25)",  icon:"⚠",  lbl:"HIGH"     },
  Critical: { c:"#ef4444", bg:"rgba(239,68,68,.12)",   bo:"rgba(239,68,68,.35)",   icon:"⛔", lbl:"CRITICAL" },
};

function MetricCard({ label, value, unit, color }) {
  return (
    <div className="mc">
      <div className="mc-l">{label}</div>
      <div className="mc-v" style={{ color: color || "var(--text)" }}>
        {value}{unit && <span className="mc-u">{unit}</span>}
      </div>
    </div>
  );
}

export default function App() {
  // mode: "live" | "upload" | "usb"
  const [mode,     setMode]    = useState("live");
  const [minSize,  setMinSize] = useState(0);
  const [err,      setErr]     = useState("");
  const [bkOk,     setBkOk]   = useState(null);
  const [altCfg,   setAltCfg] = useState(null);

  // Location
  const [loc,      setLoc]     = useState(null);
  const [locBusy,  setLocBusy] = useState(false);
  const [locErr,   setLocErr]  = useState("");

  // Live camera
  const [liveOn,   setLiveOn]  = useState(false);
  const [liveRes,  setLiveRes] = useState(null);
  const [httpsNeed,setHttpsNeed] = useState(false);
  const [camMode,  setCamMode] = useState("environment"); // "environment"|"user"|"webcam"

  const vidRef    = useRef(null);
  const cvsRef    = useRef(null);
  const streamRef = useRef(null);
  const timerRef  = useRef(null);

  // Upload
  const [file,     setFile]    = useState(null);
  const [preview,  setPreview] = useState(null);
  const [busy,     setBusy]    = useState(false);
  const [result,   setResult]  = useState(null);
  const [drag,     setDrag]    = useState(false);
  const fileRef   = useRef(null);

  // USB camera
  const [usbBusy,   setUsbBusy]   = useState(false);
  const [usbResult, setUsbResult] = useState(null);
  const [usbCamIdx, setUsbCamIdx] = useState(0);
  const [usbCams,   setUsbCams]   = useState([]);

  // Alert
  const [aState,  setAState]  = useState("idle");
  const [aMsg,    setAMsg]    = useState("");

  // History
  const [hist,    setHist]    = useState([]);
  const [hOpen,   setHOpen]   = useState(false);
  const [stats,   setStats]   = useState(null);

  // ── Health check ─────────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/`)
      .then(r => r.json())
      .then(d => { setBkOk(d.model === "loaded"); setAltCfg(d.alerts); })
      .catch(() => setBkOk(false));
    refreshStats();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setHttpsNeed(true);
    }
  }, []);

  // Probe USB cameras on mount
  useEffect(() => {
    fetch(`${API}/api/usb/info`)
      .then(r => r.json())
      .then(d => { setUsbCams(d.cameras || []); setUsbCamIdx(d.default_index ?? 0); })
      .catch(() => {});
  }, []);

  const refreshStats = () =>
    fetch(`${API}/api/stats`).then(r => r.json()).then(setStats).catch(() => {});
  const loadHist = () =>
    fetch(`${API}/api/history`).then(r => r.json()).then(setHist).catch(() => {});

  // ── Location ──────────────────────────────────────────────────
  const getLoc = () => {
    if (!navigator.geolocation) { setLocErr("Geolocation not supported"); return; }
    setLocBusy(true); setLocErr("");
    navigator.geolocation.getCurrentPosition(
      p => {
        const { latitude: la, longitude: ln, accuracy: ac } = p.coords;
        setLoc({ lat: la, lng: ln, display: `${la.toFixed(5)}, ${ln.toFixed(5)}`, acc: Math.round(ac) });
        setLocBusy(false);
      },
      e => { setLocErr(`Location error: ${e.message}`); setLocBusy(false); },
      { timeout: 10000, enableHighAccuracy: true }
    );
  };

  const locStr  = loc ? `${loc.lat},${loc.lng}` : "Not provided";
  const mapsUrl = loc ? `https://maps.google.com/?q=${loc.lat},${loc.lng}` : null;

  // ── Mode switch ───────────────────────────────────────────────
  const switchMode = m => {
    if (m === mode) return;
    stopLive();
    setMode(m); setResult(null); setLiveRes(null); setUsbResult(null);
    setFile(null); setPreview(null); setErr(""); setAState("idle");
  };

  // ── Camera source switch ──────────────────────────────────────
  const switchCamMode = newMode => {
    if (liveOn) stopLive();
    setCamMode(newMode);
    setErr("");
  };

  // ── Live camera ───────────────────────────────────────────────
  const startLive = async () => {
    setErr(""); setLiveRes(null); setAState("idle");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setHttpsNeed(true);
      setErr("Camera requires HTTPS. Use Upload or USB tab instead.");
      return;
    }
    try {
      // Build constraints based on selected camera mode
      const videoConstraints =
        camMode === "webcam"
          ? { width: { ideal: 640 }, height: { ideal: 480 } }
          : { facingMode: camMode, width: { ideal: 640 }, height: { ideal: 480 } };

      const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints });
      streamRef.current = stream;
      vidRef.current.srcObject = stream;
      await vidRef.current.play();
      setLiveOn(true);
      timerRef.current = setInterval(captureAndSend, 2500);
    } catch (e) {
      setErr(`Camera error: ${e.message}`);
    }
  };

  const stopLive = () => {
    clearInterval(timerRef.current);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    setLiveOn(false);
  };

  useEffect(() => () => stopLive(), []);

  const captureAndSend = useCallback(async () => {
    const v = vidRef.current, c = cvsRef.current;
    if (!v || !c || !v.videoWidth) return;
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const b64 = c.toDataURL("image/jpeg", 0.82).split(",")[1];
    try {
      const r = await fetch(`${API}/api/detect/frame`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: b64, min_size: minSize, location: locStr }),
      });
      const d = await r.json();
      if (d.error) { setErr(d.error); return; }
      setLiveRes(d); setAState("idle");
    } catch (e) {
      setErr(`Send error: ${e.message}`);
    }
  }, [minSize, locStr]);

  // ── Upload ────────────────────────────────────────────────────
  const onDrop = e => {
    e.preventDefault(); setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith("image/")) pickFile(f);
  };
  const pickFile = f => {
    setFile(f); setResult(null); setAState("idle");
    const reader = new FileReader();
    reader.onload = ev => setPreview(ev.target.result);
    reader.readAsDataURL(f);
  };

  const analyze = async () => {
    if (!file) return;
    setBusy(true); setErr(""); setResult(null); setAState("idle");
    const fd = new FormData();
    fd.append("image", file);
    fd.append("min_size", minSize);
    fd.append("location", locStr);
    try {
      const r = await fetch(`${API}/api/detect/image`, { method: "POST", body: fd });
      const d = await r.json();
      if (d.error) { setErr(d.error); } else { setResult(d); }
    } catch (e) {
      setErr(`Upload error: ${e.message}`);
    }
    setBusy(false);
  };

  // ── USB capture (Quantum QHM-495LM) ──────────────────────────
  const usbCapture = async () => {
    setUsbBusy(true); setErr(""); setUsbResult(null); setAState("idle");
    try {
      const r = await fetch(`${API}/api/usb/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_size: minSize, location: locStr, cam_index: usbCamIdx }),
      });
      const d = await r.json();
      if (d.error) { setErr(d.error); } else { setUsbResult(d); }
    } catch (e) {
      setErr(`USB capture error: ${e.message}`);
    }
    setUsbBusy(false);
  };

  // ── Alert ─────────────────────────────────────────────────────
  const sendAlert = async (data) => {
    setAState("sending");
    try {
      const r = await fetch(`${API}/api/alert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const d = await r.json();
      if (d.sent) {
        const channels = Object.entries(d.results)
          .filter(([, v]) => v.ok).map(([k]) => k).join(", ");
        setAState("ok");
        setAMsg(`Alert sent via ${channels}`);
      } else {
        setAState("fail");
        setAMsg("No alert channel sent — check backend config");
      }
    } catch (e) {
      setAState("fail"); setAMsg(e.message);
    }
  };

  // ── Download ──────────────────────────────────────────────────
  const download = (data) => {
    const a = document.createElement("a");
    a.href = `data:image/jpeg;base64,${data.image}`;
    a.download = `roadscan_${data.timestamp?.replace(/[: ]/g, "_") || "result"}.jpg`;
    a.click();
  };

  const slPct  = `${(minSize / 20) * 100}%`;
  const active = mode === "live" ? liveRes : mode === "usb" ? usbResult : result;
  const sm     = SEV[active?.severity] || SEV.None;
  const avgDepth = active?.count > 0
    ? (active.detections.reduce((s, d) => s + d.depth_cm, 0) / active.detections.length).toFixed(1)
    : "—";
  const noCfg = active && aState === "fail" && !aMsg.includes("sent");

  // Camera source option definitions
  const CAM_OPTS = [
    { id: "environment", label: "📷 Rear Cam",  tip: "Phone back camera"   },
    { id: "user",        label: "🤳 Front Cam", tip: "Phone selfie camera" },
    { id: "webcam",      label: "🖥 Webcam",     tip: "Laptop / USB webcam" },
  ];

  return (
    <div className="app">

      {/* ── Header ──────────────────────────────────────────── */}
      <header className="hdr">
        <div className="hdr-l">
          <span className="logo">🛣 RoadScan AI</span>
          <span className="ver">v4.0</span>
        </div>
        <div className="hdr-r">
          {stats && (
            <span className="stat-chip">
              {stats.scans} scans · {stats.potholes} potholes
            </span>
          )}
          <span className={`dot ${bkOk === true ? "green" : bkOk === false ? "red" : "amber"}`} />
          <span className="dot-lbl">
            {bkOk === true ? "Backend Online" : bkOk === false ? "Backend Offline" : "Connecting…"}
          </span>
        </div>
      </header>

      {/* ── WhatsApp / alert config banner ──────────────────── */}
      {altCfg && altCfg.whatsapp && (
        <div className="wa-banner">
          <span>🟢</span> WhatsApp alerts active via Green API
        </div>
      )}

      {/* ── HTTPS warning ───────────────────────────────────── */}
      {httpsNeed && mode === "live" && (
        <div className="https-warn">
          ⚠ <strong>HTTPS required for mobile camera.</strong> Use <strong>Upload</strong> or <strong>USB Camera</strong> tab, or set up HTTPS (see README).
        </div>
      )}

      {/* ── Main layout ─────────────────────────────────────── */}
      <div className="main">

        {/* ════════ LEFT — Controls ════════ */}
        <div className="panel lp">

          {/* Mode tabs */}
          <div className="tabs">
            {["live", "upload", "usb"].map(m => (
              <button key={m} className={`tab ${mode === m ? "active" : ""}`}
                onClick={() => switchMode(m)}>
                {m === "live" ? "📷 Live" : m === "upload" ? "⬆ Upload" : "🔌 USB Cam"}
              </button>
            ))}
          </div>

          {/* Depth-mode info pill */}
          <div className="depth-pill">
            {mode === "upload"
              ? <><span className="dp-icon">🧠</span> Upload uses <strong>MiDaS</strong> neural depth</>
              : <><span className="dp-icon">📐</span> {mode === "live" ? "Live" : "USB"} uses <strong>Geometry</strong> depth (instant)</>}
          </div>

          {/* ── Camera source selector (live tab only) ── */}
          {mode === "live" && (
            <div>
              <div style={{
                fontFamily: "var(--fm)", fontSize: 9, letterSpacing: 2,
                textTransform: "uppercase", color: "var(--text3)", marginBottom: 7,
              }}>
                Camera Source
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {CAM_OPTS.map(opt => (
                  <button
                    key={opt.id}
                    title={opt.tip}
                    onClick={() => switchCamMode(opt.id)}
                    style={{
                      flex: 1,
                      padding: "8px 4px",
                      borderRadius: "var(--r)",
                      border: camMode === opt.id
                        ? "1px solid rgba(245,158,11,.55)"
                        : "1px solid var(--bdr)",
                      background: camMode === opt.id
                        ? "var(--ad)"
                        : "rgba(0,0,0,.25)",
                      color: camMode === opt.id ? "var(--amber)" : "var(--text3)",
                      fontFamily: "var(--fm)",
                      fontSize: 10,
                      cursor: "pointer",
                      transition: "all .14s",
                      whiteSpace: "nowrap",
                      fontWeight: camMode === opt.id ? 600 : 400,
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {/* Active camera hint */}
              <div style={{
                marginTop: 6, fontFamily: "var(--fm)", fontSize: 10,
                color: "var(--text3)", lineHeight: 1.5,
              }}>
                {camMode === "environment" && "📱 Uses back/rear camera on phones"}
                {camMode === "user"        && "🤳 Uses front/selfie camera on phones"}
                {camMode === "webcam"      && "🖥 Uses laptop webcam or plugged-in USB camera (QHM-495LM)"}
              </div>
            </div>
          )}

          {/* ── Live video element (hidden canvas for capture) ── */}
          {mode === "live" && (
            <div style={{
              borderRadius: "var(--rl)", overflow: "hidden",
              background: "#03070f", border: "1px solid var(--bdr)",
              minHeight: 180, position: "relative",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <video
                ref={vidRef}
                playsInline
                muted
                style={{
                  width: "100%", display: liveOn ? "block" : "none",
                  maxHeight: 240, objectFit: "cover",
                }}
              />
              <canvas ref={cvsRef} style={{ display: "none" }} />
              {!liveOn && (
                <div style={{ textAlign: "center", padding: 28, color: "var(--text3)" }}>
                  <div style={{ fontSize: 36, marginBottom: 10, opacity: .6 }}>
                    {camMode === "webcam" ? "🖥" : camMode === "user" ? "🤳" : "📷"}
                  </div>
                  <div style={{ fontFamily: "var(--fm)", fontSize: 11, color: "var(--text2)" }}>
                    {CAM_OPTS.find(o => o.id === camMode)?.label}
                  </div>
                  <div style={{ fontFamily: "var(--fm)", fontSize: 10, marginTop: 4 }}>
                    Press Start Detection
                  </div>
                </div>
              )}
              {liveOn && (
                <div style={{
                  position: "absolute", top: 10, right: 10,
                  background: "rgba(239,68,68,.9)", color: "#fff",
                  fontFamily: "var(--fm)", fontSize: 9, letterSpacing: 2,
                  padding: "4px 10px", borderRadius: 20,
                  display: "flex", alignItems: "center", gap: 5,
                }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: "50%",
                    background: "#fff", animation: "blink .8s infinite",
                  }} />
                  LIVE
                </div>
              )}
            </div>
          )}

          {/* GPS */}
          <div className="gps-block">
            <button className="btn-gps" onClick={getLoc} disabled={locBusy}>
              {locBusy ? "…" : "📍 Get GPS"}
            </button>
            {loc && (
              <span className="gps-val">
                {loc.display} <span className="gps-acc">(±{loc.acc}m)</span>
              </span>
            )}
            {locErr && <span className="gps-err">{locErr}</span>}
          </div>

          {/* Drop zone (upload mode) */}
          {mode === "upload" && (
            <div
              className={`drop ${drag ? "drag" : ""}`}
              onDragOver={e => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={onDrop}
              onClick={() => fileRef.current.click()}
            >
              <input
                ref={fileRef} type="file" accept="image/*"
                style={{ display: "none" }}
                onChange={e => e.target.files[0] && pickFile(e.target.files[0])}
              />
              {preview
                ? <img src={preview} alt="preview" className="drop-prev" />
                : <>
                    <div className="drop-ico">🖼</div>
                    <div className="drop-txt">Drop image or click to browse</div>
                  </>}
            </div>
          )}

          {/* USB camera selector */}
          {mode === "usb" && (
            <div className="usb-block">
              <div className="usb-title">🔌 Quantum QHM-495LM / USB Camera</div>
              <div className="usb-row">
                <label className="usb-lbl">Camera index</label>
                <select className="usb-sel" value={usbCamIdx}
                  onChange={e => setUsbCamIdx(Number(e.target.value))}>
                  {usbCams.length > 0
                    ? usbCams.map(c => (
                        <option key={c.index} value={c.index}>
                          #{c.index} — {c.width}×{c.height}
                        </option>
                      ))
                    : [0, 1, 2].map(i => (
                        <option key={i} value={i}>#{i}</option>
                      ))}
                </select>
              </div>
              {usbCams.length === 0 && (
                <div className="usb-note">
                  No cameras detected yet. Plug in the QHM-495LM and make sure the backend is running.
                </div>
              )}
            </div>
          )}

          {/* Size slider */}
          <div className="sl-block">
            <div className="sl-hdr">
              <span>Min Pothole Size</span>
              <span className="sl-val">{minSize === 0 ? "All" : `${minSize}%`}</span>
            </div>
            <input
              type="range" min={0} max={20} step={1} value={minSize}
              onChange={e => setMinSize(Number(e.target.value))}
              className="sl" style={{ "--p": slPct }}
            />
            <div className="sl-ticks">
              <span>All</span><span>5%</span><span>10%</span><span>15%</span><span>20%</span>
            </div>
            <div className="sl-desc">
              {minSize === 0
                ? "Detecting all potholes in the full image/frame"
                : `Showing only potholes larger than ${minSize}% of the image`}
            </div>
          </div>

          {/* Error */}
          {err && <div className="err-msg">⚠ {err}</div>}

          {/* Action button */}
          {mode === "live" && (
            liveOn
              ? <button className="btn-stop" onClick={stopLive}>⏹ Stop Detection</button>
              : <button className="btn-start" onClick={startLive}
                  disabled={bkOk === false || httpsNeed}>
                  {bkOk === false ? "Backend Offline" : httpsNeed
                    ? "HTTPS Required"
                    : `▶ Start — ${CAM_OPTS.find(o => o.id === camMode)?.label}`}
                </button>
          )}
          {mode === "upload" && (
            <button
              className={`btn-analyze ${busy ? "busy" : ""}`}
              onClick={analyze} disabled={busy || !file}>
              {busy ? <><span className="spin" />Analyzing with MiDaS…</> : "⚡ Analyze Road"}
            </button>
          )}
          {mode === "usb" && (
            <button
              className={`btn-usb ${usbBusy ? "busy" : ""}`}
              onClick={usbCapture} disabled={usbBusy || bkOk === false}>
              {usbBusy ? <><span className="spin" />Capturing…</> : "📸 Capture & Detect"}
            </button>
          )}

          {/* Status chips */}
          {mode === "live" && liveOn && (
            <div className="file-meta">
              <span style={{ color: "var(--green)" }}>● Scanning every 2.5s</span>
              {loc && <span style={{ color: "var(--green)" }}>📍 GPS active</span>}
            </div>
          )}
          {file && mode === "upload" && (
            <div className="file-meta">
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file.name}
              </span>
              <span style={{ flexShrink: 0 }}>{(file.size / 1024).toFixed(1)} KB</span>
            </div>
          )}
        </div>

        {/* ════════ RIGHT — Results ════════ */}
        <div className="panel rp">

          {!active && !busy && !usbBusy && (
            <div className="empty">
              <div className="e-ico">🛣️</div>
              <div className="e-ttl">No Scan Yet</div>
              <div className="e-sub">
                {mode === "live"   && "Select camera source, then press Start Detection."}
                {mode === "upload" && "Upload a road photo and click Analyze Road (MiDaS depth)."}
                {mode === "usb"    && "Connect Quantum QHM-495LM USB camera and click Capture & Detect."}
              </div>
            </div>
          )}

          {(busy || usbBusy) && !active && (
            <div className="scanning">
              <div className="sline" />
              <div style={{ fontSize: 42 }}>🔍</div>
              <div className="slbl">
                {busy ? "Running YOLOv8 + MiDaS inference…" : "Capturing from USB camera…"}
              </div>
            </div>
          )}

          {active && (
            <div className="res">

              {/* Severity bar */}
              <div className="sev-bar" style={{ background: sm.bg, borderColor: sm.bo }}>
                <div>
                  <div className="sev-lbl" style={{ color: sm.c }}>Severity Level</div>
                  <div className="sev-val" style={{ color: sm.c }}>
                    {sm.icon} {active.count} Pothole{active.count !== 1 ? "s" : ""} Detected
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                  <span className="sev-badge" style={{ color: sm.c, background: sm.bg, borderColor: sm.bo }}>
                    {sm.lbl}
                  </span>
                  <span className="depth-tag">
                    {active.depth_backend === "midas" ? "🧠 MiDaS" : "📐 Geometry"}
                  </span>
                </div>
              </div>

              {/* GPS */}
              {loc && (
                <div className="gps-row">
                  <span>📍</span>
                  <a href={mapsUrl} target="_blank" rel="noopener noreferrer">{loc.display}</a>
                  <span style={{ color: "var(--text3)", fontSize: 10 }}>· tap for Maps</span>
                </div>
              )}

              {/* Metrics */}
              <div className="m3">
                <MetricCard label="Potholes" value={active.count}
                  color={active.count > 0 ? sm.c : "var(--green)"} />
                <MetricCard label="Avg Conf."
                  value={active.count > 0 ? (active.avg_confidence * 100).toFixed(1) : "—"}
                  unit={active.count > 0 ? "%" : ""} color="var(--blue)" />
                <MetricCard label="Avg Depth" value={avgDepth}
                  unit={active.count > 0 ? "cm" : ""} color="var(--cyan)" />
              </div>

              {/* Volume strip */}
              {active.count > 0 && (
                <div className="v3">
                  <div className="vc">
                    <div className="vl">Volume</div>
                    <div className="vv" style={{ color: "var(--amber)" }}>
                      {active.total_volume_m3?.toFixed(4)}
                      <span style={{ fontSize: 9, color: "var(--text3)", marginLeft: 3 }}>m³</span>
                    </div>
                  </div>
                  <div className="vc">
                    <div className="vl">Area</div>
                    <div className="vv" style={{ color: "var(--blue)" }}>
                      {active.total_area_m2?.toFixed(3)}
                      <span style={{ fontSize: 9, color: "var(--text3)", marginLeft: 3 }}>m²</span>
                    </div>
                  </div>
                  <div className="vc">
                    <div className="vl">Est. Cost</div>
                    <div className="vv" style={{ color: "var(--green)", fontSize: 14 }}>
                      ₹{active.materials?.total_inr?.toLocaleString("en-IN") || "0"}
                    </div>
                  </div>
                </div>
              )}

              {/* Images */}
              {mode === "upload" ? (
                <div className="cmp">
                  <div className="cc">
                    <div className="ci-lbl">Original</div>
                    {preview && <img className="ci" src={preview} alt="original" />}
                  </div>
                  <div className="cc">
                    <div className="ci-lbl">Annotated (MiDaS depth)</div>
                    <img className="ci" src={`data:image/jpeg;base64,${active.image}`} alt="result" />
                  </div>
                </div>
              ) : (
                <div>
                  <div className="ci-lbl" style={{ marginBottom: 6 }}>
                    {mode === "usb"
                      ? "USB Camera Frame"
                      : `Live Frame — ${CAM_OPTS.find(o => o.id === camMode)?.label}`}
                  </div>
                  <img
                    className="ci"
                    style={{ width: "100%", aspectRatio: "16/9" }}
                    src={`data:image/jpeg;base64,${active.image}`}
                    alt="live"
                  />
                </div>
              )}

              {/* Materials */}
              {active.count > 0 && active.materials?.items?.length > 0 && (
                <div className="mat-card">
                  <div className="mat-hdr">
                    <span className="mat-hl">Repair Materials (IRC/PWD)</span>
                    <span className="mat-tot">
                      ₹{active.materials.total_inr?.toLocaleString("en-IN")}
                    </span>
                  </div>
                  {active.materials.items.map((it, i) => (
                    <div className="mat-row" key={i}>
                      <span className="mat-n">{it.name}</span>
                      <div className="mat-r">
                        <div className="mat-q">{it.qty}</div>
                        <div className="mat-c">₹{it.cost?.toLocaleString("en-IN")}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Detection table */}
              {active.detections?.length > 0 && (
                <div className="det-card">
                  <div className="det-hdr">
                    <span>Detection Details</span>
                    <span className="dtag">
                      {active.depth_backend === "midas" ? "🧠 midas" : "📐 geometry"}
                    </span>
                  </div>
                  <table className="dt">
                    <thead>
                      <tr>
                        <th>#</th><th>Conf</th><th>Width</th>
                        <th>Depth</th><th>Area%</th><th>Vol m³</th>
                      </tr>
                    </thead>
                    <tbody>
                      {active.detections.map((d, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          <td>
                            <span className={d.conf > 0.7 ? "hi" : d.conf > 0.5 ? "md" : "lo"}>
                              {(d.conf * 100).toFixed(1)}%
                            </span>
                          </td>
                          <td>{d.width_m?.toFixed(2)} m</td>
                          <td style={{ color: "var(--cyan)" }}>{d.depth_cm} cm</td>
                          <td style={{ color: "var(--amber)" }}>{d.area_pct?.toFixed(1)}%</td>
                          <td style={{ color: "var(--purple)" }}>{d.vol?.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Alert button */}
              {active.count > 0 && (
                aState === "ok"
                  ? <div className="aok">✓ {aMsg}</div>
                  : aState === "fail"
                    ? <div className="afail">
                        ❌ {aMsg}
                        {noCfg && (
                          <div style={{ marginTop: 6, fontSize: 11 }}>
                            Set WHATSAPP_ENABLED / TELEGRAM_ENABLED / GMAIL_ENABLED in backend/app.py
                          </div>
                        )}
                      </div>
                    : <button className="btn-alert" onClick={() => sendAlert(active)}
                        disabled={aState === "sending"}>
                        {aState === "sending"
                          ? <><span className="spin" style={{ borderTopColor: "#fff" }} />Sending…</>
                          : "🚨 Send Alert (WhatsApp / Telegram / Gmail)"}
                      </button>
              )}

              {/* Actions */}
              <div className="act-row">
                <button className="btn-dl" onClick={() => download(active)}>⬇ Download</button>
                <button className="btn-clr" onClick={() => {
                  setResult(null); setLiveRes(null); setUsbResult(null);
                  setFile(null); setPreview(null); setAState("idle");
                }}>✕ Clear</button>
              </div>

              <div className="ts-row">
                {active.timestamp}
                {active.location && active.location !== "Not provided" && (
                  <span style={{ marginLeft: 10, color: "var(--green)" }}>📍 {active.location}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── History ─────────────────────────────────────────── */}
      <div className="hist-sec">
        <div
          className="hist-hdr"
          role="button" tabIndex={0}
          onClick={() => { if (!hOpen) loadHist(); setHOpen(v => !v); }}
          onKeyDown={e => e.key === "Enter" && (loadHist(), setHOpen(v => !v))}
        >
          <div className="hist-ttl">
            Detection History
            {hist.length > 0 && <span className="hist-cnt">{hist.length}</span>}
          </div>
          <span className={`chev ${hOpen ? "open" : ""}`}>▼</span>
        </div>
        {hOpen && (
          <div className="hist-body">
            {hist.length === 0
              ? <div className="hist-empty">No scans logged yet.</div>
              : <table className="dt" style={{ minWidth: 680 }}>
                  <thead>
                    <tr>
                      {["Time","File","Count","Severity","Vol m³","Cost ₹","Location"].map(h => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {hist.map((r, i) => {
                      const s = SEV[r.severity] || SEV.None;
                      return (
                        <tr key={i}>
                          <td style={{ color:"var(--text3)", fontSize:10 }}>{r.ts}</td>
                          <td style={{ maxWidth:130, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", color:"var(--text3)", fontSize:10 }}>{r.file}</td>
                          <td style={{ color: parseInt(r.count) > 0 ? "var(--amber)" : "var(--green)" }}>{r.count}</td>
                          <td>
                            <span style={{
                              color: s.c, background: s.bg,
                              border: `1px solid ${s.bo}`,
                              padding: "2px 8px", borderRadius: 10,
                              fontSize: 9, letterSpacing: 1,
                            }}>
                              {r.severity}
                            </span>
                          </td>
                          <td style={{ color:"var(--purple)" }}>{parseFloat(r.volume_m3||0).toFixed(4)}</td>
                          <td style={{ color:"var(--green)" }}>
                            {parseInt(r.cost_inr||0) > 0
                              ? `₹${parseInt(r.cost_inr).toLocaleString("en-IN")}`
                              : "—"}
                          </td>
                          <td style={{ maxWidth:140, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", color:"var(--text3)", fontSize:10 }}>{r.location||"—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
            }
          </div>
        )}
      </div>

      <footer className="footer">
        ROADSCAN AI v4.0 · YOLOv8 · MiDaS (Upload) · Geometry (Live/USB) · Green API WhatsApp · IRC/PWD · TSSM BSCOER ENTC 2025-26
      </footer>
    </div>
  );
}