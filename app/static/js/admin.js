(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rupees = (p) => (p == null ? "-" : "Rs. " + (p / 100).toLocaleString("en-IN", { minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2 }));
  const t = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "-");
  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  const toPaise = (v) => Math.round(parseFloat(v) * 100);
  const PILL = { VERIFIED: "ok", PAID: "ok", REFUNDED: "ok", ADJUSTED: "ok", PARTIALLY_PAID: "warn", PAYMENT_PROCESSING: "warn",
    PAYMENT_REPORTED: "warn", PENDING_VERIFICATION: "warn", FLAGGED: "warn", REJECTED: "bad", UNPAID: "bad", FAILED: "bad", EXPIRED: "bad",
    CANCELLED: "", SCHEDULED: "", PENDING_SYNC: "warn", SYNCED: "ok" };
  const pill = (s) => `<span class="pill ${PILL[s] || ""}">${esc(String(s).replace(/_/g, " "))}</span>`;
  let token = sessionStorage.getItem("ff-admin");
  let selected = null;

  async function api(method, url, body, idem) {
    const headers = { "Content-Type": "application/json", Authorization: "Bearer " + token };
    if (idem) headers["Idempotency-Key"] = idem;
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401) { logout(); throw new Error("Session expired. Please sign in again."); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      let msg = data.detail;
      if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`).join("; ");
      throw new Error(msg || res.statusText);
    }
    return data;
  }

  // ---------------- auth
  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("loginErr").textContent = "";
    const res = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value, password: $("password").value }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { $("loginErr").textContent = data.detail || "Sign in failed"; return; }
    token = data.access_token; sessionStorage.setItem("ff-admin", token); start();
  });
  function logout() { sessionStorage.removeItem("ff-admin"); token = null; $("mainView").classList.add("hidden"); $("setupView").classList.add("hidden"); $("loginView").classList.remove("hidden"); }
  $("logoutBtn").addEventListener("click", logout);

  let polling = null;
  async function start() {
    let me;
    try { me = await api("GET", "/api/v1/auth/me"); } catch (_) { logout(); return; }
    $("loginView").classList.add("hidden");
    if (!me.has_workspace) {
      // First sign-in: the admin creates their own workspace (centre + UPI ID).
      $("mainView").classList.add("hidden"); $("setupView").classList.remove("hidden");
      return;
    }
    $("setupView").classList.add("hidden");
    $("centreTitle").textContent = me.workspace.name; $("adminName").textContent = me.email || me.name;
    $("mainView").classList.remove("hidden");
    loadQueue(); loadNotifBadge();
    if (!polling) polling = setInterval(() => { if (token && !document.hidden && !$("mainView").classList.contains("hidden")) { loadQueue(false); loadNotifBadge(); } }, 30000);
  }

  $("setupLogout").addEventListener("click", logout);
  $("setupForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("setupErr").textContent = "";
    const vpa = $("wsVpa").value.trim();
    if (vpa.toLowerCase() !== $("wsVpa2").value.trim().toLowerCase()) { $("setupErr").textContent = "The two UPI IDs do not match."; return; }
    if (!confirm(`Parents will pay to ${vpa}. Is this the centre's correct UPI ID?`)) return;
    try {
      await api("POST", "/api/v1/workspace", { name: $("wsName").value.trim(), upi_vpa: vpa, upi_payee_name: $("wsPayee").value.trim(),
        contact_phone: $("wsPhone").value.trim() || null, contact_email: $("wsEmail").value.trim() || null });
      start();
    } catch (err) { $("setupErr").textContent = err.message; }
  });

  async function loadSettings() {
    const w = await api("GET", "/api/v1/workspace");
    $("stName").value = w.name; $("stVpa").value = w.upi_vpa; $("stPayee").value = w.upi_payee_name;
    $("stPhone").value = w.contact_phone || ""; $("stEmail").value = w.contact_email || "";
    $("stGrace").value = w.verification_grace_hours; $("stFreq").value = w.reminder_frequency_hours;
    $("stAck").checked = w.ack_sms_enabled; $("stCode").textContent = w.short_code;
    $("settingsForm").dataset.vpa = w.upi_vpa; $("stErr").textContent = ""; $("stOk").textContent = "";
  }
  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("stErr").textContent = ""; $("stOk").textContent = "";
    const vpa = $("stVpa").value.trim();
    if (vpa !== $("settingsForm").dataset.vpa && !confirm(`Change the centre UPI ID to ${vpa}?\n\nFrom now on every parent pays to this ID.`)) return;
    try {
      const w = await api("PATCH", "/api/v1/workspace", { name: $("stName").value.trim(), upi_vpa: vpa, upi_payee_name: $("stPayee").value.trim(),
        contact_phone: $("stPhone").value.trim(), contact_email: $("stEmail").value.trim(),
        verification_grace_hours: +$("stGrace").value, reminder_frequency_hours: +$("stFreq").value, ack_sms_enabled: $("stAck").checked });
      $("centreTitle").textContent = w.name; $("settingsForm").dataset.vpa = w.upi_vpa; $("stOk").textContent = "Saved.";
    } catch (err) { $("stErr").textContent = err.message; }
  });

  // ---------------- tabs
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    ["verify", "installments", "reports", "notifications", "audit", "settings"].forEach((n) => $("tab-" + n).classList.toggle("hidden", n !== b.dataset.tab));
    ({ verify: loadQueue, installments: loadInstallments, reports: loadReport, notifications: loadNotifications, audit: loadAudit, settings: loadSettings })[b.dataset.tab]();
  }));

  // ---------------- verification queue
  async function loadQueue(resetDetail = true) {
    const { items } = await api("GET", "/api/v1/payment-verification/pending");
    const badge = $("pendingBadge"); badge.textContent = items.length; badge.classList.toggle("hidden", !items.length);
    $("queue").innerHTML = items.length ? items.map((d) => `
      <div class="qitem ${d.id === selected ? "sel" : ""}" data-id="${esc(d.id)}">
        <div class="row"><b>${esc(d.student_name)}</b>${pill(d.verification_status)}</div>
        <div class="row small muted"><span>${esc(d.student_id)} · ${esc(d.installment.description)} ${esc(d.installment.billing_month)}</span></div>
        <div class="row"><span>Declared <b>${rupees(d.declared_amount_paise)}</b>${d.payment_type === "PARTIAL" ? " (partial)" : ""}</span><span class="small muted">${t(d.declared_at || d.created_at)}</span></div>
        ${d.verification_overdue ? '<div class="small" style="color:var(--bad)">Past grace period</div>' : ""}
        ${d.clarification_request ? '<div class="small muted">Clarification requested</div>' : ""}
      </div>`).join("") : '<p class="muted">Nothing to verify right now.</p>';
    $("queue").querySelectorAll(".qitem").forEach((el) => el.addEventListener("click", () => openDecl(el.dataset.id)));
    if (resetDetail && selected && !items.some((d) => d.id === selected)) { selected = null; $("detail").innerHTML = '<p class="muted">Select a payment confirmation to review it.</p>'; }
  }

  async function openDecl(id) {
    selected = id;
    const d = await api("GET", "/api/v1/payment-declarations/" + encodeURIComponent(id));
    loadQueue(false);
    const i = d.installment;
    $("detail").innerHTML = `
      <div class="topbar"><h2 style="margin:0">${esc(d.student_name)} <span class="muted small">${esc(d.student_id)}</span></h2>${pill(d.verification_status)}</div>
      <dl class="kv" style="margin-top:10px">
        <dt>Parent mobile</dt><dd><a href="tel:${esc(d.parent_mobile)}">${esc(d.parent_mobile)}</a></dd>
        <dt>Installment</dt><dd>${esc(i.description)} · ${esc(i.billing_month)} (due ${esc(i.due_date)})</dd>
        <dt>Expected amount</dt><dd>${rupees(d.expected_amount_paise)}</dd>
        <dt>Declared amount</dt><dd class="big">${rupees(d.declared_amount_paise)}${d.payment_type === "PARTIAL" ? " <span class='pill warn'>partial</span>" : ""}</dd>
        <dt>Declared at</dt><dd>${t(d.declared_at)}</dd>
        <dt>UPI app</dt><dd>${esc(d.upi_app || "Not specified")}</dd>
        <dt>Approx. payment time</dt><dd>${esc(d.approximate_payment_time || "-")}</dd>
        <dt>Parent's UPI ref (optional)</dt><dd>${esc(d.parent_upi_reference || "-")}</dd>
        <dt>Total fee / paid</dt><dd>${rupees(i.total_paise)} / ${rupees(i.paid_paise)}</dd>
        <dt>Outstanding</dt><dd>${rupees(d.outstanding_paise)}</dd>
        <dt>Reference</dt><dd>${esc(d.reference)}</dd>
      </dl>
      ${d.parent_notes ? `<h3>Parent notes</h3><pre class="notes">${esc(d.parent_notes)}</pre>` : ""}
      ${d.clarification_request ? `<p class="banner warn" style="margin-top:10px">Waiting for parent: ${esc(d.clarification_request)}</p>` : ""}
      ${d.suggested_bank_matches.length ? `<h3>Possible matches from imported bank statement</h3>${d.suggested_bank_matches.map((m) => `
        <div class="panel small"><b>${rupees(m.amount_paise)}</b> · ${esc(m.bank_reference)} · ${t(m.credited_at)} ${esc(m.payer_vpa || "")}<br><span class="muted">${esc(m.narration || "")}</span>
        <button class="btn secondary sm" data-match='${esc(JSON.stringify(m))}'>Use this transaction</button></div>`).join("")}` : ""}
      <h3>Payment history</h3>
      ${d.verified_payments.length || d.student_payment_history.length ? `<table><tr><th>Fee</th><th>Amount</th><th>When</th></tr>
        ${d.verified_payments.map((p) => `<tr><td>This installment${p.bank_reference ? " · " + esc(p.bank_reference) : ""}</td><td>${rupees(p.amount_paise)}</td><td>${t(p.verified_at)}</td></tr>`).join("")}
        ${d.student_payment_history.map((p) => `<tr><td>${esc(p.installment)} ${esc(p.billing_month)}</td><td>${rupees(p.amount_paise)}</td><td>${t(p.verified_at)}</td></tr>`).join("")}</table>` : '<p class="muted small">No verified payments yet.</p>'}
      ${d.previous_declarations.length ? `<p class="small muted">Earlier confirmations: ${d.previous_declarations.map((p) => `${esc(p.status)} ${rupees(p.declared_amount_paise)}`).join(", ")}</p>` : ""}

      <div class="actions" style="margin-top:14px">
        <button class="btn ok sm" id="actConfirm">Confirm payment received</button>
        <button class="btn bad sm" id="actReject">Payment not received</button>
        <button class="btn secondary sm" id="actClarify">Request clarification</button>
      </div>
      <div id="actionPanel"></div>`;
    $("actConfirm").onclick = () => confirmForm(d);
    $("actReject").onclick = () => rejectForm(d);
    $("actClarify").onclick = () => clarifyForm(d);
    $("detail").querySelectorAll("[data-match]").forEach((b) => b.addEventListener("click", () => confirmForm(d, JSON.parse(b.dataset.match))));
  }

  function txRow(amountPaise, ref, bankTxId) {
    return `<div class="tx" data-btx="${esc(bankTxId || "")}">
      <div><label>Amount received (Rs.)</label><input class="txAmt" type="number" step="0.01" min="0.01" value="${amountPaise ? (amountPaise / 100).toFixed(2) : ""}" ${bankTxId ? "readonly" : ""}></div>
      <div><label>Bank reference / UTR</label><input class="txRef" maxlength="64" value="${esc(ref || "")}" ${bankTxId ? "readonly" : ""} placeholder="From your bank statement"></div>
      <button type="button" class="btn ghost sm txDel">✕</button></div>`;
  }

  function confirmForm(d, match) {
    const p = $("actionPanel");
    p.innerHTML = `<form class="panel" id="cf"><h3 style="margin-top:0">Confirm payment received</h3>
      <p class="small muted">Open your bank account / UPI merchant app and find the actual credit before confirming.</p>
      <div id="txs">${match ? txRow(match.amount_paise, match.bank_reference, match.bank_transaction_id) : txRow(d.declared_amount_paise, "")}</div>
      <button type="button" class="btn ghost sm" id="addTx" style="margin-top:8px">+ Parent paid more than once (add another transaction)</button>
      <label>Evidence note (required if no bank reference)</label><input id="cfEvidence" maxlength="500" placeholder="e.g. Seen in HDFC statement, 11 Nov 10:42, from 98xxxxxx01">
      <label>Internal note (optional)</label><input id="cfNote" maxlength="500">
      <label class="check"><input type="checkbox" id="cfChecked"> I checked the actual bank transaction</label>
      <label class="check"><input type="checkbox" id="cfMine"> The money was received in our centre's account</label>
      <label class="check hidden" id="cfDiffWrap"><input type="checkbox" id="cfDiff"> <span id="cfDiffText"></span></label>
      <button class="btn ok" style="margin-top:12px">Verify payment</button><p class="err" id="cfErr"></p></form>`;
    const txs = $("txs");
    const bindDel = () => txs.querySelectorAll(".txDel").forEach((b) => (b.onclick = () => { if (txs.children.length > 1) b.parentElement.remove(); checkDiff(); }));
    const collect = () => [...txs.querySelectorAll(".tx")].map((r) => {
      const tx = { amount_paise: toPaise(r.querySelector(".txAmt").value), bank_reference: r.querySelector(".txRef").value.trim() || null };
      if (r.dataset.btx) tx.bank_transaction_id = r.dataset.btx;
      return tx;
    });
    const checkDiff = () => {
      const total = collect().reduce((a, x) => a + (x.amount_paise || 0), 0);
      const differs = total !== d.declared_amount_paise;
      $("cfDiffWrap").classList.toggle("hidden", !differs);
      $("cfDiffText").textContent = `Verified total ${rupees(total)} differs from declared ${rupees(d.declared_amount_paise)} - I confirm the verified amount is correct`;
    };
    bindDel(); checkDiff();
    txs.addEventListener("input", checkDiff);
    $("addTx").onclick = () => { txs.insertAdjacentHTML("beforeend", txRow(0, "")); bindDel(); checkDiff(); };
    const idem = newKey();
    $("cf").onsubmit = async (e) => {
      e.preventDefault(); $("cfErr").textContent = "";
      const transactions = collect();
      if (transactions.some((x) => !x.amount_paise || x.amount_paise <= 0)) { $("cfErr").textContent = "Enter every amount."; return; }
      const total = transactions.reduce((a, x) => a + x.amount_paise, 0);
      if (!confirm(`Verify ${rupees(total)} for ${d.student_name}?\n\nThis records a verified payment, issues a receipt and notifies the parent.`)) return;
      try {
        const r = await api("POST", `/api/v1/payment-verification/${encodeURIComponent(d.id)}/confirm`, {
          confirm: true, bank_transaction_checked: $("cfChecked").checked, belongs_to_centre: $("cfMine").checked,
          transactions, amount_difference_acknowledged: $("cfDiff").checked,
          evidence_note: $("cfEvidence").value.trim() || null, admin_note: $("cfNote").value.trim() || null }, idem);
        p.innerHTML = `<div class="banner ok" style="margin-top:10px">Verified. Fee is now ${esc(r.installment_status.replace("_", " "))}; outstanding ${rupees(r.outstanding_paise)}.
          Receipt <a href="${esc(r.receipt.url)}" target="_blank" rel="noopener">${esc(r.receipt.receipt_number)}</a>.
          ${r.excess_flagged_paise ? `<br>Excess ${rupees(r.excess_flagged_paise)} flagged for refund/adjustment.` : ""}</div>`;
        selected = null; loadQueue(false);
      } catch (err) { $("cfErr").textContent = err.message; }
    };
  }

  function rejectForm(d) {
    $("actionPanel").innerHTML = `<form class="panel" id="rf"><h3 style="margin-top:0">Payment not received</h3>
      <label>Reason (internal)</label><textarea id="rfReason" rows="2" required minlength="3" placeholder="e.g. No matching credit in bank statement up to 11 Nov 6pm"></textarea>
      <p class="small muted">The parent will be told the payment could not be verified and can submit a new confirmation.</p>
      <button class="btn bad">Mark as not received</button><p class="err" id="rfErr"></p></form>`;
    const idem = newKey();
    $("rf").onsubmit = async (e) => {
      e.preventDefault();
      if (!confirm("Mark this payment as NOT received? The parent will be notified.")) return;
      try { await api("POST", `/api/v1/payment-verification/${encodeURIComponent(d.id)}/reject`, { reason: $("rfReason").value }, idem);
        $("actionPanel").innerHTML = '<div class="banner bad" style="margin-top:10px">Marked as not received. Parent notified.</div>'; selected = null; loadQueue(false);
      } catch (err) { $("rfErr").textContent = err.message; }
    };
  }

  function clarifyForm(d) {
    $("actionPanel").innerHTML = `<form class="panel" id="qf"><h3 style="margin-top:0">Request clarification</h3>
      <label>Question for the parent (sent by SMS)</label><textarea id="qfMsg" rows="2" required minlength="3">Could you tell us which UPI app you used and roughly what time you paid?</textarea>
      <button class="btn">Send request</button><p class="err" id="qfErr"></p></form>`;
    const idem = newKey();
    $("qf").onsubmit = async (e) => {
      e.preventDefault();
      try { await api("POST", `/api/v1/payment-verification/${encodeURIComponent(d.id)}/request-clarification`, { message: $("qfMsg").value }, idem);
        openDecl(d.id);
      } catch (err) { $("qfErr").textContent = err.message; }
    };
  }

  // ---------------- add student / fee
  $("addStudentBtn").addEventListener("click", () => {
    $("feeForms").innerHTML = `<form class="panel" id="sf"><h3 style="margin-top:0">Add student</h3>
      <label>Student ID</label><input id="sfCode" required maxlength="40" placeholder="e.g. BM-1005">
      <label>Student name</label><input id="sfName" required maxlength="200">
      <label>Parent name (optional)</label><input id="sfParent" maxlength="200">
      <label>Parent mobile</label><input id="sfMobile" required inputmode="tel" placeholder="10-digit mobile">
      <button class="btn" style="margin-top:10px">Save student</button><p class="err" id="sfErr"></p></form>`;
    $("sf").onsubmit = async (e) => {
      e.preventDefault(); $("sfErr").textContent = "";
      try {
        const s = await api("POST", "/api/v1/students", { student_code: $("sfCode").value.trim(), name: $("sfName").value.trim(),
          parent_name: $("sfParent").value.trim() || null, parent_mobile: $("sfMobile").value });
        $("feeForms").innerHTML = `<div class="banner ok">Added ${esc(s.name)} (${esc(s.student_id)}). Parent mobile ${esc(s.parent_mobile)}.</div>`;
      } catch (err) { $("sfErr").textContent = err.message; }
    };
  });

  $("addFeeBtn").addEventListener("click", async () => {
    const { items } = await api("GET", "/api/v1/students");
    if (!items.length) { $("feeForms").innerHTML = '<div class="banner warn">Add a student first.</div>'; return; }
    const now = new Date();
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    $("feeForms").innerHTML = `<form class="panel" id="ff"><h3 style="margin-top:0">Add fee</h3>
      <label>Student</label><select id="ffStudent">${items.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${esc(s.student_id)})</option>`).join("")}</select>
      <label>Description</label><input id="ffDesc" required maxlength="200" value="Monthly tuition fee">
      <label>Billing month</label><input id="ffMonth" type="month" required value="${month}">
      <label>Amount (Rs.)</label><input id="ffAmt" type="number" min="1" step="0.01" required>
      <label>Due date</label><input id="ffDue" type="date" required>
      <label class="check"><input type="checkbox" id="ffSms" checked> Send the payment link to the parent by SMS (from the SMS phone)</label>
      <label class="check"><input type="checkbox" id="ffRem" checked> Schedule reminders</label>
      <button class="btn" style="margin-top:10px">Create fee</button><p class="err" id="ffErr"></p></form>`;
    const idem = newKey();
    $("ff").onsubmit = async (e) => {
      e.preventDefault(); $("ffErr").textContent = "";
      try {
        const r = await api("POST", "/api/v1/installments", { student_id: $("ffStudent").value, description: $("ffDesc").value.trim(),
          billing_month: $("ffMonth").value, total_paise: toPaise($("ffAmt").value), due_date: $("ffDue").value,
          send_link_sms: $("ffSms").checked, schedule_reminders: $("ffRem").checked }, idem);
        $("feeForms").innerHTML = `<div class="banner ok">Fee created${r.link_sms_queued ? " - payment link SMS queued for the SMS phone" : ""}.<br>
          <input readonly value="${esc(r.payment_link)}" onclick="this.select()"></div>`;
        loadInstallments();
      } catch (err) { $("ffErr").textContent = err.message; }
    };
  });

  // ---------------- installments
  $("instFilter").addEventListener("change", loadInstallments);
  async function loadInstallments() {
    const f = $("instFilter").value;
    const { items } = await api("GET", "/api/v1/installments" + (f ? "?status=" + f : ""));
    $("instTable").innerHTML = `<tr><th>Student</th><th>Fee</th><th>Total</th><th>Paid</th><th>Outstanding</th><th>Status</th><th></th></tr>` +
      items.map((i) => `<tr><td>${esc(i.student_name)}<br><span class="small muted">${esc(i.student_id)}</span></td>
        <td>${esc(i.description)}<br><span class="small muted">${esc(i.billing_month)} · due ${esc(i.due_date)}</span></td>
        <td>${rupees(i.total_paise)}</td><td>${rupees(i.paid_paise)}${i.excess_paise ? `<br><span class="small" style="color:var(--warn)">+${rupees(i.excess_paise)} excess</span>` : ""}</td>
        <td>${rupees(i.outstanding_paise)}</td><td>${pill(i.status)}</td>
        <td class="actions"><button class="btn ghost sm" data-st="${esc(i.id)}">Status</button><button class="btn ghost sm" data-link="${esc(i.id)}">New link</button><button class="btn ghost sm" data-pay="${esc(i.id)}">Record payment</button></td></tr>`).join("");
    $("instTable").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => instStatus(b.dataset.st)));
    $("instTable").querySelectorAll("[data-link]").forEach((b) => (b.onclick = async () => {
      const r = await api("POST", `/api/v1/installments/${encodeURIComponent(b.dataset.link)}/payment-links`);
      $("instDetail").innerHTML = `<div class="panel">New payment link (valid until ${t(r.expires_at)}):<br><input readonly value="${esc(r.url)}" onclick="this.select()"></div>`;
    }));
    $("instTable").querySelectorAll("[data-pay]").forEach((b) => (b.onclick = () => manualPayForm(b.dataset.pay)));
  }

  async function instStatus(id) {
    const s = await api("GET", `/api/v1/installments/${encodeURIComponent(id)}/payment-status`);
    $("instDetail").innerHTML = `<div class="panel"><h3 style="margin-top:0">Payment status ${pill(s.display_status)}</h3>
      <p class="small">Installment: ${pill(s.installment_status)} · Latest declaration: ${s.declaration_status ? pill(s.declaration_status) : "none"}
      ${s.reminders_paused_until ? ` · Reminders paused until ${t(s.reminders_paused_until)}` : ""} · SMS waiting for device: ${s.pending_sms}</p>
      <table><tr><th>Reminder</th><th>Amount</th><th>Status</th><th>Device sync</th></tr>
      ${s.reminders.map((r) => `<tr><td>${t(r.scheduled_for)}</td><td>${rupees(r.amount_paise)}</td><td>${pill(r.status)}</td>
        <td>${r.sync_status === "PENDING_SYNC" ? pill("PENDING_SYNC") + ' <span class="small muted">device offline or not yet synced</span>' : pill("SYNCED")}</td></tr>`).join("")}</table></div>`;
  }

  function manualPayForm(id) {
    $("instDetail").innerHTML = `<form class="panel" id="mp"><h3 style="margin-top:0">Record an actual bank credit</h3>
      <p class="small muted">Use for credits without a parent confirmation (e.g. a second accidental transfer). Excess over the balance is flagged for refund/adjustment.</p>
      <div id="mpTx">${txRow(0, "")}</div>
      <label>Evidence note</label><input id="mpEvidence" maxlength="500">
      <label class="check"><input type="checkbox" id="mpChecked"> I checked the actual bank transaction</label>
      <label class="check"><input type="checkbox" id="mpMine"> The money was received in our centre's account</label>
      <button class="btn ok" style="margin-top:10px">Record payment</button><p class="err" id="mpErr"></p></form>`;
    const idem = newKey();
    $("mp").onsubmit = async (e) => {
      e.preventDefault();
      const r0 = $("mpTx").querySelector(".tx");
      const tx = { amount_paise: toPaise(r0.querySelector(".txAmt").value), bank_reference: r0.querySelector(".txRef").value.trim() || null };
      if (!confirm(`Record ${rupees(tx.amount_paise)} as a verified payment?`)) return;
      try {
        const r = await api("POST", `/api/v1/installments/${encodeURIComponent(id)}/payments`, { confirm: true,
          bank_transaction_checked: $("mpChecked").checked, belongs_to_centre: $("mpMine").checked, transactions: [tx],
          evidence_note: $("mpEvidence").value.trim() || null }, idem);
        $("instDetail").innerHTML = `<div class="banner ok">Recorded. Status ${esc(r.installment_status)}. Receipt ${esc(r.receipt.receipt_number)}.${r.excess_flagged_paise ? " Excess " + rupees(r.excess_flagged_paise) + " flagged." : ""}</div>`;
        loadInstallments();
      } catch (err) { $("mpErr").textContent = err.message; }
    };
  }

  // ---------------- reports
  $("reportSel").addEventListener("change", loadReport);
  const MONEY = /_paise$/;
  async function loadReport() {
    const name = $("reportSel").value;
    const { items } = await api("GET", "/api/v1/reports/" + name);
    if (!items.length) { $("reportTable").innerHTML = '<tr><td class="muted">No records.</td></tr>'; return; }
    const cols = Object.keys(items[0]).filter((c) => !/_id$/.test(c) || c === "student_id");
    const extra = name === "rejected-declarations" ? "<th></th>" : name === "excess-payments" ? "<th></th>" : "";
    $("reportTable").innerHTML = `<tr>${cols.map((c) => `<th>${esc(c.replace(/_paise$/, "").replace(/_/g, " "))}</th>`).join("")}${extra}</tr>` +
      items.map((r) => `<tr>${cols.map((c) => `<td>${MONEY.test(c) ? rupees(r[c]) : /(_at)$/.test(c) ? t(r[c]) : c === "status" ? pill(r[c]) : esc(r[c])}</td>`).join("")}
        ${name === "rejected-declarations" ? `<td><button class="btn ghost sm" data-reopen="${esc(r.declaration_id)}">Reopen</button></td>` : ""}
        ${name === "excess-payments" && r.status === "FLAGGED" ? `<td><button class="btn ghost sm" data-adj="${esc(r.adjustment_id)}">Resolve</button></td>` : name === "excess-payments" ? "<td></td>" : ""}</tr>`).join("");
    $("reportTable").querySelectorAll("[data-reopen]").forEach((b) => (b.onclick = async () => {
      const reason = prompt("Why are you reopening this rejected confirmation? (e.g. credit appeared late)");
      if (!reason) return;
      try { await api("POST", `/api/v1/payment-verification/${encodeURIComponent(b.dataset.reopen)}/reopen`, { reason }, newKey()); loadReport(); }
      catch (err) { alert(err.message); }
    }));
    $("reportTable").querySelectorAll("[data-adj]").forEach((b) => (b.onclick = async () => {
      const how = prompt("Type REFUNDED or ADJUSTED", "REFUNDED"); if (!how) return;
      const note = prompt("Resolution note (e.g. refunded via UPI ref ...)"); if (!note) return;
      try { await api("POST", `/api/v1/adjustments/${encodeURIComponent(b.dataset.adj)}/resolve`, { status: how.trim().toUpperCase(), note }); loadReport(); }
      catch (err) { alert(err.message); }
    }));
  }

  // ---------------- notifications / audit
  async function loadNotifBadge() {
    const { items } = await api("GET", "/api/v1/notifications?unread_only=true");
    $("notifBadge").textContent = items.length; $("notifBadge").classList.toggle("hidden", !items.length);
  }
  async function loadNotifications() {
    const { items } = await api("GET", "/api/v1/notifications");
    $("notifList").innerHTML = items.length ? items.map((n) => `<div class="panel ${n.read ? "" : "sel"}"><b>${esc(n.kind.replace(/_/g, " "))}</b> <span class="small muted">${t(n.created_at)}</span><br>${esc(n.message)}</div>`).join("") : '<p class="muted">No notifications.</p>';
  }
  $("markReadBtn").addEventListener("click", async () => { await api("POST", "/api/v1/notifications/mark-read"); loadNotifications(); loadNotifBadge(); });
  async function loadAudit() {
    const { items } = await api("GET", "/api/v1/audit-logs?limit=300");
    $("auditTable").innerHTML = `<tr><th>Time</th><th>Actor</th><th>Action</th><th>Status change</th><th>Reason</th><th>Source</th></tr>` +
      items.map((a) => `<tr><td>${t(a.timestamp)}</td><td>${esc(a.actor_type)}</td><td>${esc(a.action)}</td>
        <td>${a.previous_status || a.new_status ? `${esc(a.previous_status || "-")} → ${esc(a.new_status || "-")}` : ""}</td><td>${esc(a.reason || "")}</td><td class="small muted">${esc(a.source)}</td></tr>`).join("");
  }

  if (token) start();
})();
