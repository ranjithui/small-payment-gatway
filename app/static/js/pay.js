(() => {
  "use strict";
  const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const API = "/api/v1/payment-declarations";
  const root = document.getElementById("app");
  const KEY_STORE = "ff-idem-" + token.slice(0, 12);
  const RETURN_FLAG = "ff-launched-" + token.slice(0, 12);
  const INTENT_FLAG = "ff-intent-" + token.slice(0, 12);
  const UA = navigator.userAgent || "";
  // Desktop browsers can't open UPI apps: lead with the QR code.
  const IS_DESKTOP = !/Android|iPhone|iPad|iPod|Mobile/i.test(UA) && !(navigator.maxTouchPoints > 1 && /Macintosh/.test(UA));
  const IS_IOS = /iPhone|iPad|iPod/.test(UA);
  let state = null;
  let intentNotice = null; // { text, tone } shown under the one-tap button
  let initialPanelDone = false;

  const $ = (id) => document.getElementById(id);
  const rupees = (p) => "Rs. " + (p / 100).toLocaleString("en-IN", { minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2 });
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "-");
  const fmtMonth = (m) => { const [y, mo] = m.split("-"); return new Date(+y, +mo - 1, 1).toLocaleString("en-IN", { month: "long", year: "numeric" }); };
  const show = (el, on) => el.classList.toggle("hidden", !on);
  const visible = (el) => !el.classList.contains("hidden");
  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

  // One idempotency key per "attempt": repeated taps reuse it, so only one declaration is created.
  function attemptKey(action) {
    const store = JSON.parse(sessionStorage.getItem(KEY_STORE) || "{}");
    if (!store[action]) { store[action] = newKey(); sessionStorage.setItem(KEY_STORE, JSON.stringify(store)); }
    return store[action];
  }
  const clearKeys = () => sessionStorage.removeItem(KEY_STORE);

  async function api(method, url, body, idem) {
    const headers = { "Content-Type": "application/json" };
    if (idem) headers["Idempotency-Key"] = idem;
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, keepalive: method !== "GET" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      let msg = data.detail;
      if (Array.isArray(msg)) msg = msg.map((d) => d.msg.replace(/^Value error, /, "")).join(" ");
      throw new Error(msg || "Something went wrong. Please try again.");
    }
    return data;
  }

  async function load() {
    try {
      state = await api("GET", `${API}/public/${encodeURIComponent(token)}`);
      render();
    } catch (e) {
      root.innerHTML = `<div class="card"><div class="brand">FeeFlow</div><h1>Link not available</h1><p class="muted"></p></div>`;
      root.querySelector("p").textContent = e.message;
    }
  }

  const PILL = {
    PAID: ["PAID", "ok"], VERIFIED: ["Verified", "ok"], PARTIALLY_PAID: ["Partially paid", "warn"],
    PAYMENT_REPORTED: ["Awaiting verification", "warn"], PENDING_VERIFICATION: ["Awaiting verification", "warn"],
    PAYMENT_PROCESSING: ["Processing", "warn"], PAYMENT_INITIATED: ["Payment started", ""], UNPAID: ["Unpaid", "bad"],
    REJECTED: ["Not verified", "bad"], FAILED: ["Payment failed", "bad"], EXPIRED: ["Link expired", "bad"],
  };

  function render() {
    const s = state;
    if (!$("centreName")) root.replaceChildren($tpl());
    const c = s.centre;
    $("centreName").textContent = c.name;
    const logo = $("logo");
    if (c.logo_url) { logo.innerHTML = ""; const img = new Image(); img.src = c.logo_url; img.alt = ""; logo.appendChild(img); }
    else logo.textContent = c.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    $("studentName").textContent = s.student.name;
    $("studentId").textContent = s.student.student_id;
    $("feeDesc").textContent = s.fee.description;
    $("feeMonth").textContent = fmtMonth(s.fee.billing_month);
    $("feeTotal").textContent = rupees(s.fee.total_paise);
    $("feePaid").textContent = rupees(s.fee.paid_paise);
    $("feeDue").textContent = new Date(s.fee.due_date + "T00:00:00").toLocaleDateString("en-IN", { dateStyle: "medium" });
    $("feeOutstanding").textContent = rupees(s.fee.outstanding_paise);
    $("feeRef").textContent = s.payment_reference || "-";
    const [label, cls] = PILL[s.display_status] || [s.display_status, ""];
    const pill = $("statusPill"); pill.textContent = label; pill.className = "pill " + cls;
    $("contactName").textContent = c.name;
    const tel = $("contactPhone"); tel.textContent = c.contact_phone || c.contact_email || ""; tel.href = c.contact_phone ? "tel:" + c.contact_phone.replace(/\s/g, "") : "#";

    // Status message
    const d = s.declaration;
    let msg = s.message, tone = "info";
    if (s.display_status === "PAID") tone = "ok";
    else if (s.display_status === "REJECTED" || s.display_status === "EXPIRED") tone = "bad";
    else if (s.display_status === "PAYMENT_PROCESSING" || s.display_status === "FAILED") tone = "warn";
    if (d && d.status === "VERIFIED" && s.display_status === "PARTIALLY_PAID") { msg = "Payment successfully verified! " + (s.message || ""); tone = "ok"; }
    if (s.awaiting_verification) msg = null; // dedicated section
    show($("messageCard"), !!msg);
    if (msg) { $("messageBanner").textContent = msg; $("messageBanner").className = "banner " + tone; }

    // Payment options
    show($("upiErrorCard"), s.can_pay && !s.upi);
    if (s.can_pay && !s.upi) $("upiErrorMsg").textContent = s.upi_error || "Online payment is not available. Please contact the tuition centre.";
    show($("paySection"), s.can_pay && !!s.upi);
    if (s.can_pay && s.upi) renderPayment(s.upi);

    const launched = sessionStorage.getItem(RETURN_FLAG) === "1";
    const midFlow = d && (d.status === "PAYMENT_INITIATED" || d.status === "PAYMENT_PROCESSING");
    show($("askSection"), s.can_pay && !!s.upi && (launched || midFlow || IS_DESKTOP));
    $("processingBtn").classList.toggle("hidden", d && d.status === "PAYMENT_PROCESSING");

    // Awaiting verification
    show($("waitSection"), s.awaiting_verification);
    if (s.awaiting_verification && d) {
      $("declaredAt").textContent = fmtTime(d.declared_at);
      $("declaredAmt").textContent = rupees(d.declared_amount_paise) + (d.payment_type === "PARTIAL" ? " (partial)" : "");
      $("declRef").textContent = d.reference;
      show($("clarifyBox"), !!d.clarification_request);
      show($("addInfo"), !d.clarification_request);
      if (d.clarification_request) $("clarifyQ").textContent = d.clarification_request;
    }

    // Receipts
    show($("receiptSection"), s.receipts.length > 0);
    const list = $("receiptList"); list.innerHTML = "";
    s.receipts.forEach((r) => {
      const a = document.createElement("a");
      a.className = "btn secondary"; a.href = r.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = `Receipt ${r.receipt_number} · ${rupees(r.amount_paise)}`;
      list.appendChild(a);
    });
  }

  // ---------------- payment options
  // Personal UPI: no one-tap (UPI apps block pre-filled payments to personal IDs); UPI ID panel first; QR without amount.
  // Merchant UPI: "Pay using UPI app" first, then QR (with amount) and Copy UPI ID.
  function renderPayment(u) {
    const m = u.methods;
    $("payAmt").textContent = rupees(u.amount_paise);
    $("payPayee").textContent = u.payee_name; $("payRef").textContent = u.reference;
    $("mPayee").textContent = u.payee_name; $("mPayee2").textContent = u.payee_name;
    $("mVpa").textContent = u.payee_vpa; $("mAmt").textContent = "Rs. " + u.amount_text; $("mAmt2").textContent = "Rs. " + u.amount_text;
    $("mRef").textContent = u.reference;
    $("mMobile").textContent = u.upi_mobile || ""; show($("mMobileRow"), !!u.upi_mobile); show($("stepMobile"), !!u.upi_mobile);
    $("vpa").textContent = u.payee_vpa; $("qrPayee").textContent = u.payee_name;
    $("qrAmtText").textContent = "Rs. " + u.amount_text; $("qrRefText").textContent = u.reference;
    show($("qrAmountNote"), !u.qr_has_amount);
    const qr = `/api/v1/pay/${encodeURIComponent(token)}/qr.png`;
    $("qrSave").href = qr + "?download=1"; $("qrSaveHi").href = qr + "?download=1&size=large";

    show($("desktopNote"), IS_DESKTOP && m.qr);
    show($("oneTapBlock"), m.upi_app && !IS_DESKTOP);
    show($("openAppBlock"), !IS_DESKTOP);
    if (intentNotice) { $("intentMsg").textContent = intentNotice.text; $("intentMsg").className = "banner " + intentNotice.tone; }
    else show($("intentMsg"), false);

    // Which panel is open first (only decided once, so the parent's own choice is kept on refresh)
    if (!initialPanelDone) {
      initialPanelDone = true;
      const ways = $("payWays");
      if (IS_DESKTOP) ["qrBlock", "manualBlock", "choiceRow", "oneTapBlock"].forEach((id) => ways.appendChild($(id)));
      if (IS_DESKTOP && m.qr) openPanel("qr", false);
      else if (!m.upi_app || IS_DESKTOP) openPanel(m.copy ? "manual" : "qr", false);
    }
    // Buttons for the options that are not open yet
    show($("showQrBtn"), m.qr && !visible($("qrBlock")));
    show($("showManualBtn"), m.copy && !visible($("manualBlock")));
    show($("choiceRow"), visible($("showQrBtn")) || visible($("showManualBtn")));
  }

  function openPanel(which, record = true) {
    const qr = which === "qr";
    const panel = qr ? $("qrBlock") : $("manualBlock");
    show(panel, true);
    if (qr) loadQr();
    logAttempt(qr ? "QR" : "MANUAL", qr ? "QR_SHOWN" : "STARTED", IS_DESKTOP ? "desktop" : null, true);
    if (record) startedManually(qr ? "QR" : "OTHER_UPI");
    else show($("askSection"), true);
    if (state && state.upi) {
      show($("showQrBtn"), state.upi.methods.qr && !visible($("qrBlock")));
      show($("showManualBtn"), state.upi.methods.copy && !visible($("manualBlock")));
      show($("choiceRow"), visible($("showQrBtn")) || visible($("showManualBtn")));
    }
    if (record) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function loadQr() {
    const img = $("qrImg"), url = `/api/v1/pay/${encodeURIComponent(token)}/qr.png`;
    if (img.getAttribute("src") !== url) img.src = url;
  }

  // Opens the UPI app's home screen only (no pre-filled payment, so nothing can be blocked).
  const APP_PKG = { GPAY: "com.google.android.apps.nbu.paisa.user", PHONEPE: "com.phonepe.app",
    PAYTM: "net.one97.paytm", BHIM: "in.org.npci.upiapp" };
  const IOS_SCHEME = { GPAY: "gpay://", PHONEPE: "phonepe://", PAYTM: "paytmmp://", BHIM: "bhim://" };
  function openUpiApp(app) {
    logAttempt("MANUAL", "OPEN_APP", app);
    startedManually("OTHER_UPI");
    if (IS_IOS) { location.href = IOS_SCHEME[app]; return; }
    const store = encodeURIComponent("https://play.google.com/store/apps/details?id=" + APP_PKG[app]);
    location.href = `intent:#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;package=${APP_PKG[app]};S.browser_fallback_url=${store};end`;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall back below */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    ta.remove(); return ok;
  }

  function paymentDetailsText() {
    const u = state.upi;
    return [`Pay to: ${u.payee_name} (${state.centre.name})`, `UPI ID: ${u.payee_vpa}`,
      ...(u.upi_mobile ? [`Or mobile number: ${u.upi_mobile}`] : []), `Amount: Rs. ${u.amount_text}`,
      `Reference: ${u.reference}`, `Student: ${state.student.name} (${state.student.student_id})`,
      `Fee month: ${fmtMonth(state.fee.billing_month)}`].join("\n");
  }

  let manualStarted = false;
  function startedManually(app) {
    // The parent is about to pay from their UPI app: record the intent (not proof) and show "Have you paid?".
    sessionStorage.setItem(RETURN_FLAG, "1"); show($("askSection"), true);
    if (!manualStarted) { manualStarted = true; api("POST", API, { token, action: "INITIATED", upi_app: app }, newKey()).catch(() => {}); }
  }

  // ---------------- diagnostics (never contains banking credentials; not proof of payment)
  const logged = new Set();
  function logAttempt(method, event, detail, once) {
    const k = method + ":" + event;
    if (once && logged.has(k)) return;
    logged.add(k);
    fetch(`/api/v1/pay/${encodeURIComponent(token)}/attempts`, { method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method, event, detail: detail || null }) }).catch(() => {});
  }

  // ---------------- one-tap UPI app (merchant UPI IDs only; Android lets the parent choose the app)
  const FALLBACK_MSG = "We couldn't initiate this payment through your selected UPI application. Please try another UPI application or use the manual payment option.";
  let intentPending = false;
  function payUsingUpi() {
    if (intentPending) return; // one attempt at a time - never loops or re-redirects
    intentPending = true;
    let left = false;
    const onHide = () => { if (document.visibilityState === "hidden") left = true; };
    const onBlur = () => { left = true; };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("blur", onBlur);
    sessionStorage.setItem(RETURN_FLAG, "1");
    sessionStorage.setItem(INTENT_FLAG, "1");
    api("POST", API, { token, action: "INITIATED", upi_app: "OTHER_UPI" }, newKey()).catch(() => {});
    logAttempt("UPI_INTENT", "STARTED");
    $("optApp").value = "OTHER_UPI";
    location.href = state.upi.uris.OTHER_UPI;
    setTimeout(() => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("blur", onBlur);
      intentPending = false;
      if (!left && document.visibilityState === "visible") {
        // Nothing took over the screen: no UPI app handled the link. (Not a payment failure - status unknown.)
        sessionStorage.removeItem(INTENT_FLAG);
        intentNotice = { text: FALLBACK_MSG, tone: "warn" };
        logAttempt("UPI_INTENT", "NO_APP", null, true);
        if (state.upi.methods.copy) openPanel("manual", false);
        if (state.upi.methods.qr) openPanel("qr", false);
        render();
      } else {
        logAttempt("UPI_INTENT", "APP_OPENED", null, true);
      }
      show($("askSection"), true);
    }, 2500);
  }

  // Back from the UPI app: we can't know if the payment succeeded - ask the parent.
  function onReturnFromApp() {
    if (sessionStorage.getItem(INTENT_FLAG) !== "1") return;
    sessionStorage.removeItem(INTENT_FLAG);
    intentNotice = { text: "If you completed the payment, tap \"I have paid\" below. If your UPI app showed an error, " +
      "try another UPI app or use Copy UPI ID.", tone: "info" };
    logAttempt("UPI_INTENT", "RETURNED", null, true);
  }

  async function submit(action) {
    const err = $("askErr"); err.textContent = "";
    if (action === "FAILED") logAttempt($("optApp").value === "QR" ? "QR" : "UPI_INTENT", "FAILED", "parent reported payment failed");
    const body = { token, action, upi_app: $("optApp").value || null };
    if (action === "PAID" && $("partialChk").checked) {
      const amt = Math.round(parseFloat($("partialAmt").value) * 100);
      if (!amt || amt <= 0) { err.textContent = "Please enter the amount you paid."; return; }
      if (amt > state.fee.outstanding_paise) { err.textContent = `Amount cannot be more than ${rupees(state.fee.outstanding_paise)}.`; return; }
      body.payment_type = "PARTIAL"; body.declared_amount_paise = amt;
    }
    if ($("optTime").value) body.approximate_payment_time = $("optTime").value;
    if ($("optNote").value.trim()) body.note = $("optNote").value.trim();
    const buttons = [$("paidBtn"), $("processingBtn"), $("failedBtn")];
    buttons.forEach((b) => (b.disabled = true));
    try {
      state = await api("POST", API, body, attemptKey(action + JSON.stringify(body)));
      clearKeys();
      if (action !== "PROCESSING") sessionStorage.removeItem(RETURN_FLAG);
      render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      err.textContent = e.message;
      if (/already/.test(e.message)) load();
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function sendInfo(note, extra, errEl, btn) {
    errEl.textContent = "";
    if (!note.trim()) { errEl.textContent = "Please write a short message."; return; }
    btn.disabled = true;
    try {
      state = await api("POST", `${API}/${encodeURIComponent(state.declaration.reference)}/clarification`,
        { token, note: note.trim(), ...extra }, newKey());
      render();
    } catch (e) { errEl.textContent = e.message; } finally { btn.disabled = false; }
  }

  function $tpl() {
    const frag = document.getElementById("tpl").content.cloneNode(true);
    const wrap = document.createElement("div"); wrap.appendChild(frag);
    setTimeout(bind, 0);
    return wrap;
  }

  function bind() {
    $("payUpiBtn").addEventListener("click", payUsingUpi);
    $("showQrBtn").addEventListener("click", () => openPanel("qr"));
    $("showManualBtn").addEventListener("click", () => openPanel("manual"));
    document.querySelectorAll(".copy[data-copy]").forEach((b) => b.addEventListener("click", async () => {
      const u = state.upi, what = b.dataset.copy;
      const text = { vpa: u.payee_vpa, mobile: (u.upi_mobile || "").replace(/\s/g, ""), amount: u.amount_text,
        reference: u.reference, all: paymentDetailsText() }[what];
      const ok = await copyText(text);
      if (!ok && what !== "all") {
        // Some in-app browsers block the clipboard: select the value so the parent can long-press -> Copy.
        const el = b.previousElementSibling, range = document.createRange();
        range.selectNodeContents(el); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      }
      b.textContent = ok ? "Copied" : (what === "all" ? "Copy failed - copy each item" : "Press and hold to copy");
      b.classList.toggle("done", ok);
      setTimeout(() => { b.textContent = b.dataset.label; b.classList.remove("done"); }, ok ? 1800 : 4000);
      logAttempt("MANUAL", "COPIED", what, true);
      startedManually("OTHER_UPI");
    }));
    document.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openUpiApp(b.dataset.open)));
    [$("qrSave"), $("qrSaveHi")].forEach((a) => a.addEventListener("click", () => {
      logAttempt("QR", "QR_DOWNLOADED", a.id === "qrSaveHi" ? "large" : "standard", true);
      startedManually("QR");
    }));
    $("partialChk").addEventListener("change", () => show($("partialBox"), $("partialChk").checked));
    $("paidBtn").addEventListener("click", () => submit("PAID"));
    $("processingBtn").addEventListener("click", () => submit("PROCESSING"));
    $("failedBtn").addEventListener("click", () => submit("FAILED"));
    $("clarifyBtn").addEventListener("click", () => sendInfo($("clarifyNote").value,
      { approximate_payment_time: $("clarifyTime").value || null, upi_reference: $("clarifyRef").value.trim() || null }, $("clarifyErr"), $("clarifyBtn")));
    $("moreBtn").addEventListener("click", () => sendInfo($("moreNote").value, {}, $("moreErr"), $("moreBtn")));
  }

  // Returning from the UPI app: refresh state and ask the question. Returning is NOT proof of payment.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state) { onReturnFromApp(); load(); }
  });
  load();
})();
