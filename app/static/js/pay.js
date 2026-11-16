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
  // Short links (/p/...) come from the fee SMS, which already shows the UPI details:
  // most parents open it after paying, so "Already paid?" comes first.
  const CONFIRM_FIRST = location.pathname.startsWith("/p/");
  let state = null;
  let intentNotice = null; // { key, tone } shown under the one-tap button
  let initialPanelDone = false;

  // ---------------- language (English / Tamil; more can be added in i18n.js)
  const DICT = window.FF_I18N || { en: {} };
  const saved = (() => { try { return localStorage.getItem("ff-lang"); } catch (_) { return null; } })();
  let lang = DICT[saved] ? saved : (/^ta\b/i.test(navigator.language || "") && DICT.ta ? "ta" : "en");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function T(key, vars) {
    const s = (DICT[lang] && DICT[lang][key]) ?? DICT.en[key] ?? key;
    return vars ? s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? "")) : s;
  }
  // Same, as HTML with every value escaped and shown in bold.
  function TB(key, vars) {
    const s = esc((DICT[lang] && DICT[lang][key]) ?? DICT.en[key] ?? key);
    return s.replace(/\{(\w+)\}/g, (_, k) => `<b>${esc(vars[k] ?? "")}</b>`);
  }
  function applyLang(scope) {
    document.documentElement.lang = lang;
    (scope || document).querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T(el.dataset.i18n); });
    (scope || document).querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = T(el.dataset.i18nPh); });
  }
  const LOCALE = () => (lang === "ta" ? "ta-IN" : "en-IN");

  const $ = (id) => document.getElementById(id);
  const rupees = (p) => (lang === "ta" ? "₹" : "Rs. ") + (p / 100).toLocaleString("en-IN", { minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2 });
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString(LOCALE(), { dateStyle: "medium", timeStyle: "short" }) : "");
  const fmtMonth = (m) => { const [y, mo] = m.split("-"); return new Date(+y, +mo - 1, 1).toLocaleString(LOCALE(), { month: "long", year: "numeric" }); };
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
      root.innerHTML = `<div class="card"><div class="brand">FeeFlow</div><h1></h1><p class="muted"></p></div>`;
      root.querySelector("h1").textContent = T("linkNA");
      root.querySelector("p").textContent = e.message;
    }
  }

  const PILL_TONE = { PAID: "ok", VERIFIED: "ok", PARTIALLY_PAID: "warn", PAYMENT_REPORTED: "warn", PENDING_VERIFICATION: "warn",
    PAYMENT_PROCESSING: "warn", PAYMENT_INITIATED: "", UNPAID: "bad", REJECTED: "bad", FAILED: "bad", EXPIRED: "bad" };

  // English uses the server's own wording; other languages use the dictionary (same meaning).
  function statusMessage(s) {
    if (lang === "en") return s.message;
    if (s.display_status === "PARTIALLY_PAID")
      return T("msg_PARTIALLY_PAID", { paid: rupees(s.fee.paid_paise), balance: rupees(s.fee.outstanding_paise) });
    return DICT[lang]["msg_" + s.display_status] || s.message;
  }

  function timeline(el, steps) {
    el.innerHTML = steps.map((st) => `<li class="${st.state}"><span class="tl-dot"></span><span class="tl-txt">${esc(st.label)}
      ${st.time ? `<span class="tl-time">${esc(st.time)}</span>` : ""}</span></li>`).join("");
  }

  function renderLangSwitch() {
    const box = $("langSwitch");
    box.innerHTML = Object.keys(DICT).map((k) => `<button type="button" data-lang="${k}" class="${k === lang ? "on" : ""}">${esc(DICT[k]._name)}</button>`).join("");
    box.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => {
      lang = b.dataset.lang;
      try { localStorage.setItem("ff-lang", lang); } catch (_) { /* private mode */ }
      applyLang(root); render();
    }));
  }

  function render() {
    const s = state;
    if (!$("centreName")) { root.replaceChildren($tpl()); applyLang(root); }
    renderLangSwitch();
    const c = s.centre;
    $("centreName").textContent = c.name;
    const logo = $("logo");
    if (c.logo_url) { logo.innerHTML = ""; const img = new Image(); img.src = c.logo_url; img.alt = ""; logo.appendChild(img); }
    else logo.textContent = c.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    $("studentName").textContent = s.student.name;
    $("studentClass").textContent = s.student.class_grade ? s.student.class_grade + " · " : "";
    $("studentId").textContent = s.student.student_id;
    $("parentName").textContent = s.student.parent_name || "";
    show($("parentLbl"), !!s.student.parent_name); show($("parentName"), !!s.student.parent_name);
    $("feeDesc").textContent = s.fee.description;
    $("feeMonth").textContent = fmtMonth(s.fee.billing_month);
    $("feeTotal").textContent = rupees(s.fee.total_paise);
    $("feePaid").textContent = rupees(s.fee.paid_paise);
    $("feeDue").textContent = new Date(s.fee.due_date + "T00:00:00").toLocaleDateString(LOCALE(), { dateStyle: "medium" });
    $("feeOutstanding").textContent = rupees(s.fee.outstanding_paise);
    $("feeRef").textContent = s.payment_reference || "-";
    const pill = $("statusPill"); pill.textContent = T("pill_" + s.display_status); pill.className = "pill " + (PILL_TONE[s.display_status] || "");
    $("contactLine").textContent = T("contact", { centre: c.name });
    const tel = $("contactPhone"); tel.textContent = c.contact_phone || c.contact_email || ""; tel.href = c.contact_phone ? "tel:" + c.contact_phone.replace(/\s/g, "") : "#";

    // Status message
    const d = s.declaration;
    let msg = statusMessage(s), tone = "info";
    if (s.display_status === "PAID") tone = "ok";
    else if (s.display_status === "REJECTED" || s.display_status === "EXPIRED") tone = "bad";
    else if (s.display_status === "PAYMENT_PROCESSING" || s.display_status === "FAILED") tone = "warn";
    if (s.awaiting_verification) msg = null; // dedicated section
    // Confirmed: a clear thank-you with the receipt (part payments also show the balance)
    const verifiedPartial = d && d.status === "VERIFIED" && s.display_status === "PARTIALLY_PAID";
    const thanks = (s.display_status === "PAID" || verifiedPartial) && s.receipts.length > 0;
    show($("thanksCard"), thanks);
    if (thanks) {
      const latest = s.receipts[0]; // newest first
      $("thanksText").textContent = s.display_status === "PAID"
        ? T("thanksFull", { centre: c.name, amount: rupees(s.fee.paid_paise), student: s.student.name, month: fmtMonth(s.fee.billing_month) })
        : T("thanksPart", { centre: c.name, amount: rupees(latest.amount_paise), student: s.student.name, balance: rupees(s.fee.outstanding_paise) });
      $("thanksReceipt").href = latest.url;
      const steps = [];
      if (d && d.declared_at) steps.push({ label: T("tlReported"), time: fmtTime(d.declared_at), state: "done" });
      steps.push({ label: T("tlConfirmed"), time: fmtTime((d && d.reviewed_at) || latest.issued_at), state: "done" });
      timeline($("thanksTimeline"), steps);
      msg = null;
    }
    show($("messageCard"), !!msg);
    if (msg) { $("messageBanner").textContent = msg; $("messageBanner").className = "banner " + tone; }

    // Payment options
    show($("upiErrorCard"), s.can_pay && !s.upi);
    if (s.can_pay && !s.upi) $("upiErrorMsg").textContent = s.upi_error || "Online payment is not available. Please contact the tuition centre.";
    show($("paySection"), s.can_pay && !!s.upi);
    if (s.can_pay && s.upi) renderPayment(s.upi);

    const launched = sessionStorage.getItem(RETURN_FLAG) === "1";
    const midFlow = d && (d.status === "PAYMENT_INITIATED" || d.status === "PAYMENT_PROCESSING");
    show($("askSection"), s.can_pay && !!s.upi && (launched || midFlow || IS_DESKTOP || CONFIRM_FIRST));
    const amt = rupees(s.fee.outstanding_paise);
    const fromSms = CONFIRM_FIRST && !launched && !midFlow;
    $("askTitle").textContent = fromSms && s.upi ? T("askAlready", { amount: amt, payee: s.upi.payee_name }) : T("askDid", { amount: amt });
    $("processingBtn").classList.toggle("hidden", d && d.status === "PAYMENT_PROCESSING");
    if (s.upi) {
      $("payTitle").textContent = (fromSms ? T("notPaidYet") : "") + T("pay", { amount: rupees(s.upi.amount_paise) });
    }

    // Reported: waiting for the centre
    show($("waitSection"), s.awaiting_verification);
    if (s.awaiting_verification && d) {
      $("declaredAmt").textContent = rupees(d.declared_amount_paise) + (d.payment_type === "PARTIAL" ? ` (${T("stPARTIALLY_PAID")})` : "");
      $("declRef").textContent = s.payment_reference || d.reference;
      $("waitMsg").textContent = T("waitMsg", { centre: c.name, hours: c.verification_hours || 24 });
      timeline($("waitTimeline"), [
        { label: T("tlReported"), time: fmtTime(d.declared_at), state: "done" },
        { label: T("tlVerifying"), time: "", state: "now" },
        { label: T("tlConfirmed"), time: "", state: "" },
      ]);
      show($("clarifyBox"), !!d.clarification_request);
      show($("addInfo"), !d.clarification_request);
      if (d.clarification_request) $("clarifyQ").textContent = d.clarification_request;
    }

    renderFamily(s);
  }

  // ---------------- other children + payment history
  function renderFamily(s) {
    const fam = s.family || { children: [], history: [] };
    const others = fam.children.filter((k) => !k.current);
    show($("familyCard"), others.length > 0);
    if (others.length) {
      $("familyList").innerHTML = fam.children.map((k) => `<div class="fam-child">
          <div class="fam-head"><b>${esc(k.name)}</b>${k.class_grade ? ` <span class="muted small">${esc(k.class_grade)}</span>` : ""}</div>
          ${k.fees.length ? k.fees.map((f) => `<div class="fam-fee">
              <span><span class="small muted">${esc(fmtMonth(f.billing_month))} · ${esc(f.description)}</span><br><b>${rupees(f.outstanding_paise)}</b></span>
              ${f.current ? `<span class="pill">${esc(T("familyThis"))}</span>`
                : f.awaiting_verification ? `<span class="pill warn">${esc(T("familyVerifying"))}</span>`
                : `<button class="btn sm" data-fam="${esc(f.reference)}">${esc(T("familyPay"))}</button>`}
            </div>`).join("") : `<div class="small" style="color:var(--ok)">${esc(T("familyAllPaid"))}</div>`}
        </div>`).join("");
      $("familyList").querySelectorAll("[data-fam]").forEach((b) => (b.onclick = async () => {
        $("familyErr").textContent = ""; b.disabled = true;
        try {
          const r = await api("POST", `/api/v1/pay/${encodeURIComponent(token)}/family-link`, { reference: b.dataset.fam });
          if (r.url) location.href = r.url;
        } catch (e) { $("familyErr").textContent = e.message; b.disabled = false; }
      }));
    }
    show($("historyCard"), fam.history.length > 0);
    $("historyList").innerHTML = fam.history.map((h) => `<div class="hist">
        <span><b>${esc(h.student_name)}</b> <span class="small muted">${esc(fmtMonth(h.billing_month))} · ${esc(h.description)}</span><br>
          <span class="small">${rupees(h.paid_paise)} / ${rupees(h.total_paise)} · ${esc(T("st" + h.status))}</span></span>
        ${h.receipt_url ? `<a class="btn secondary sm" href="${esc(h.receipt_url)}" target="_blank" rel="noopener">${esc(T("receipt"))}</a>` : ""}
      </div>`).join("");
  }

  // ---------------- payment options
  // Personal UPI: big "Pay with <app>" buttons. A tap copies the UPI ID, shows short app-specific steps and
  //   opens that app's home screen (no pre-filled payment - UPI apps block those for personal IDs).
  // Merchant UPI: one big "Pay Rs. X" button that opens the UPI app with everything filled in.
  // Desktop: QR code first.
  function renderPayment(u) {
    const m = u.methods;
    $("payPayee").textContent = T("payTo", { payee: u.payee_name });
    $("payUpiBtn").textContent = T("pay", { amount: rupees(u.amount_paise) });
    $("mPayee").textContent = u.payee_name;
    $("mVpa").textContent = u.payee_vpa; $("mAmt").textContent = rupees(u.amount_paise);
    $("mRef").textContent = u.reference;
    $("mMobile").textContent = u.upi_mobile || ""; show($("mMobileRow"), !!u.upi_mobile);
    $("manualHelp").innerHTML = TB("manualHelp", { payee: u.payee_name, amount: rupees(u.amount_paise) });
    $("qrCaption").innerHTML = TB("qrScan", { payee: u.payee_name, vpa: u.payee_vpa });
    $("qrAmountNote").innerHTML = TB("qrEnter", { amount: rupees(u.amount_paise), ref: u.reference });
    show($("qrAmountNote"), !u.qr_has_amount);
    const qr = `/api/v1/pay/${encodeURIComponent(token)}/qr.png`;
    $("qrSave").href = qr + "?download=1"; $("qrSaveHi").href = qr + "?download=1&size=large";

    show($("desktopNote"), IS_DESKTOP && m.qr);
    show($("oneTapBlock"), m.upi_app && !IS_DESKTOP);
    show($("appButtons"), !m.upi_app && m.copy && !IS_DESKTOP);
    if (intentNotice) { $("intentMsg").textContent = T(intentNotice.key); $("intentMsg").className = "banner " + intentNotice.tone; }
    else show($("intentMsg"), false);
    if (guideApp) renderGuide();

    if (!initialPanelDone) {
      initialPanelDone = true;
      if (IS_DESKTOP) $("paySection").after($("askSection")); // on a computer: QR first, question below it
      if (IS_DESKTOP) { if (m.qr) openPanel("qr", false); else if (m.copy) openPanel("manual", false); }
      else if (!m.upi_app && !m.copy && m.qr) openPanel("qr", false);
    }
    updateLinks();
  }

  function updateLinks() {
    const m = state.upi.methods;
    show($("showQrLink"), m.qr && !visible($("qrBlock")));
    show($("showManualLink"), m.copy && !visible($("manualBlock")));
    show($("payLinks"), visible($("showQrLink")) || visible($("showManualLink")));
  }

  function openPanel(which, record = true) {
    const qr = which === "qr";
    const panel = qr ? $("qrBlock") : $("manualBlock");
    show(panel, true);
    if (qr) loadQr();
    logAttempt(qr ? "QR" : "MANUAL", qr ? "QR_SHOWN" : "STARTED", IS_DESKTOP ? "desktop" : null, true);
    if (record) startedManually(qr ? "QR" : "OTHER_UPI");
    else show($("askSection"), true);
    if (state && state.upi) updateLinks();
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
  const APP_NAME = { GPAY: "Google Pay", PHONEPE: "PhonePe", PAYTM: "Paytm" };
  function launchApp(app) {
    if (!APP_PKG[app]) return;
    if (IS_IOS) { location.href = IOS_SCHEME[app]; return; }
    const store = encodeURIComponent("https://play.google.com/store/apps/details?id=" + APP_PKG[app]);
    location.href = `intent:#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;package=${APP_PKG[app]};S.browser_fallback_url=${store};end`;
  }

  let guideApp = null, guideCopied = false;
  function renderGuide() {
    const u = state.upi;
    $("guideTitle").textContent = guideCopied ? T("guideCopied") : T("guideCopyThis", { vpa: u.payee_vpa });
    $("guideText").textContent = T("guideText", {
      app: APP_NAME[guideApp] || T("yourApp"), step: T("step" + guideApp),
      what: guideCopied ? T("guideWhat") : u.payee_vpa,
      mobile: u.upi_mobile ? T("guideMobile", { mobile: u.upi_mobile }) : "",
      payee: u.payee_name, amount: rupees(u.amount_paise),
    });
    show($("guideCard"), true);
    show($("guideOpenAgain"), !!APP_PKG[guideApp]);
  }
  async function payWithApp(app) {
    guideApp = app;
    guideCopied = await copyText(state.upi.payee_vpa);
    $("optApp").value = app === "OTHER" ? "OTHER_UPI" : app;
    logAttempt("MANUAL", "COPIED", "vpa", true);
    logAttempt("MANUAL", "OPEN_APP", app);
    startedManually(app === "OTHER" ? "OTHER_UPI" : app);
    renderGuide();
    $("guideCard").scrollIntoView({ behavior: "smooth", block: "center" });
    if (!guideCopied) openPanel("manual", false);
    // Give the parent a moment to read the steps, then open the app.
    if (APP_PKG[app]) setTimeout(() => launchApp(app), 1200);
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
      `Fee month: ${state.fee.billing_month}`].join("\n");
  }

  let manualStarted = false;
  function startedManually(app) {
    // The parent is about to pay from their UPI app: record the intent (not proof) and show the question.
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
        intentNotice = { key: "fallback", tone: "warn" };
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
    intentNotice = { key: "returnNotice", tone: "info" };
    logAttempt("UPI_INTENT", "RETURNED", null, true);
  }

  // ---------------- reporting the payment ("I've completed the payment" -> confirm -> submit)
  function paidAmount() {
    if (!$("partialChk").checked) return { ok: true, paise: state.fee.outstanding_paise, partial: false };
    const amt = Math.round(parseFloat($("partialAmt").value) * 100);
    if (!amt || amt <= 0) return { ok: false, err: T("enterAmount") };
    if (amt > state.fee.outstanding_paise) return { ok: false, err: T("amountTooHigh", { max: rupees(state.fee.outstanding_paise) }) };
    return { ok: true, paise: amt, partial: true };
  }

  function askConfirm() {
    $("askErr").textContent = "";
    const a = paidAmount();
    if (!a.ok) { $("askErr").textContent = a.err; return; }
    const payee = state.upi ? state.upi.payee_name : state.centre.name;
    $("confirmText").innerHTML = TB(a.partial ? "confirmPart" : "confirmText", { amount: rupees(a.paise), payee });
    show($("confirmBox"), true);
    $("confirmYes").focus();
  }

  async function submit(action) {
    const err = $("askErr"); err.textContent = "";
    if (action === "FAILED") logAttempt($("optApp").value === "QR" ? "QR" : "UPI_INTENT", "FAILED", "parent reported payment failed");
    const body = { token, action, upi_app: $("optApp").value || null };
    if (action === "PAID") {
      const a = paidAmount();
      if (!a.ok) { err.textContent = a.err; return; }
      if (a.partial) { body.payment_type = "PARTIAL"; body.declared_amount_paise = a.paise; }
      if ($("optUtr").value.trim()) body.upi_reference = $("optUtr").value.replace(/\s/g, "");
    }
    if ($("optTime").value) body.approximate_payment_time = $("optTime").value;
    if ($("optNote").value.trim()) body.note = $("optNote").value.trim();
    const buttons = [$("paidBtn"), $("processingBtn"), $("failedBtn"), $("confirmYes")];
    buttons.forEach((b) => (b.disabled = true));
    try {
      state = await api("POST", API, body, attemptKey(action + JSON.stringify(body)));
      clearKeys();
      if (action !== "PROCESSING") sessionStorage.removeItem(RETURN_FLAG);
      show($("confirmBox"), false);
      render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      show($("confirmBox"), false);
      err.textContent = e.message;
      if (/already/.test(e.message)) load();
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function sendInfo(note, extra, errEl, btn) {
    errEl.textContent = "";
    if (!note.trim()) { errEl.textContent = T("writeShort"); return; }
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
    $("showQrLink").addEventListener("click", (e) => { e.preventDefault(); openPanel("qr"); });
    $("showManualLink").addEventListener("click", (e) => { e.preventDefault(); openPanel("manual"); });
    document.querySelectorAll("[data-pay]").forEach((b) => b.addEventListener("click", () => payWithApp(b.dataset.pay)));
    $("guideOpenAgain").addEventListener("click", () => { if (guideApp) launchApp(guideApp); });
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
      b.textContent = ok ? T("copied") : (what === "all" ? T("copyFailed") : T("holdToCopy"));
      b.classList.toggle("done", ok);
      setTimeout(() => { b.textContent = T(b.dataset.i18n); b.classList.remove("done"); }, ok ? 1800 : 4000);
      logAttempt("MANUAL", "COPIED", what, true);
      startedManually("OTHER_UPI");
    }));
    [$("qrSave"), $("qrSaveHi")].forEach((a) => a.addEventListener("click", () => {
      logAttempt("QR", "QR_DOWNLOADED", a.id === "qrSaveHi" ? "large" : "standard", true);
      startedManually("QR");
    }));
    $("partialChk").addEventListener("change", () => show($("partialBox"), $("partialChk").checked));
    $("paidBtn").addEventListener("click", askConfirm);
    $("confirmYes").addEventListener("click", () => submit("PAID"));
    $("confirmNo").addEventListener("click", () => show($("confirmBox"), false));
    $("confirmBox").addEventListener("click", (e) => { if (e.target === $("confirmBox")) show($("confirmBox"), false); });
    $("processingBtn").addEventListener("click", () => submit("PROCESSING"));
    $("failedBtn").addEventListener("click", () => submit("FAILED"));
    $("clarifyBtn").addEventListener("click", () => sendInfo($("clarifyNote").value,
      { approximate_payment_time: $("clarifyTime").value || null, upi_reference: $("clarifyRef").value.trim() || null }, $("clarifyErr"), $("clarifyBtn")));
    $("moreBtn").addEventListener("click", () => sendInfo($("moreNote").value, {}, $("moreErr"), $("moreBtn")));
  }

  // Returning from the UPI app: refresh state and ask the question. Returning is NOT proof of payment.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state) {
      const back = sessionStorage.getItem(RETURN_FLAG) === "1";
      onReturnFromApp();
      load().then(() => { if (back && visible($("askSection"))) $("askSection").scrollIntoView({ behavior: "smooth", block: "start" }); });
    }
  });
  applyLang(document);
  load();
})();
