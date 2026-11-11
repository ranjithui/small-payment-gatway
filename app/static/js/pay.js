(() => {
  "use strict";
  const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const API = "/api/v1/payment-declarations";
  const root = document.getElementById("app");
  const KEY_STORE = "ff-idem-" + token.slice(0, 12);
  const RETURN_FLAG = "ff-launched-" + token.slice(0, 12);
  let state = null;

  const $ = (id) => document.getElementById(id);
  const rupees = (p) => "Rs. " + (p / 100).toLocaleString("en-IN", { minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2 });
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "-");
  const fmtMonth = (m) => { const [y, mo] = m.split("-"); return new Date(+y, +mo - 1, 1).toLocaleString("en-IN", { month: "long", year: "numeric" }); };
  const show = (el, on) => el.classList.toggle("hidden", !on);
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

    // Payment controls
    show($("paySection"), s.can_pay);
    if (s.can_pay) {
      $("vpa").textContent = s.upi.payee_vpa;
      if ($("qrDetails").open) loadQr();
    }
    const launched = sessionStorage.getItem(RETURN_FLAG) === "1";
    const midFlow = d && (d.status === "PAYMENT_INITIATED" || d.status === "PAYMENT_PROCESSING");
    show($("askSection"), s.can_pay && (launched || midFlow));
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

  function $tpl() {
    const frag = document.getElementById("tpl").content.cloneNode(true);
    const wrap = document.createElement("div"); wrap.appendChild(frag);
    setTimeout(bind, 0);
    return wrap;
  }

  function loadQr() {
    const img = $("qrImg");
    if (!img.src) img.src = `/api/v1/pay/${encodeURIComponent(token)}/qr.svg`;
  }

  async function launchApp(app, anyAmount) {
    const uris = anyAmount ? state.upi.uris_any_amount : state.upi.uris;
    sessionStorage.setItem(RETURN_FLAG, "1");
    // Record the intent (NOT proof of payment). Fire-and-forget so the app opens instantly.
    api("POST", API, { token, action: "INITIATED", upi_app: app }, newKey()).catch(() => {});
    $("optApp").value = app === "QR" ? "QR" : app;
    location.href = uris[app];
    // If no UPI app handled the link (e.g. desktop), still show the confirmation question.
    setTimeout(() => { show($("askSection"), true); }, 1500);
  }

  async function submit(action) {
    const err = $("askErr"); err.textContent = "";
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

  function bind() {
    document.querySelectorAll(".app[data-app]").forEach((b) => b.addEventListener("click", () => launchApp(b.dataset.app, false)));
    $("anyAmountBtn").addEventListener("click", () => { $("partialChk").checked = true; show($("partialBox"), true); launchApp("OTHER_UPI", true); });
    $("qrDetails").addEventListener("toggle", () => {
      if ($("qrDetails").open) {
        loadQr(); sessionStorage.setItem(RETURN_FLAG, "1"); show($("askSection"), true);
        api("POST", API, { token, action: "INITIATED", upi_app: "QR" }, newKey()).catch(() => {});
      }
    });
    $("partialChk").addEventListener("change", () => show($("partialBox"), $("partialChk").checked));
    $("paidBtn").addEventListener("click", () => submit("PAID"));
    $("processingBtn").addEventListener("click", () => submit("PROCESSING"));
    $("failedBtn").addEventListener("click", () => submit("FAILED"));
    $("clarifyBtn").addEventListener("click", () => sendInfo($("clarifyNote").value,
      { approximate_payment_time: $("clarifyTime").value || null, upi_reference: $("clarifyRef").value.trim() || null }, $("clarifyErr"), $("clarifyBtn")));
    $("moreBtn").addEventListener("click", () => sendInfo($("moreNote").value, {}, $("moreErr"), $("moreBtn")));
  }

  // Returning from the UPI app: refresh state and ask the question. Returning is NOT proof of payment.
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && state) load(); });
  load();
})();
