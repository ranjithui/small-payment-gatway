(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const t = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "-");
  const PILL = { TRIAL: "", ACTIVE: "ok", EXPIRED: "warn", SUSPENDED: "bad" };
  const pill = (s) => `<span class="pill ${PILL[s] || ""}">${esc(s)}</span>`;
  // Shares the session with the workspace admin page.
  let token = sessionStorage.getItem("ff-admin");
  let rows = [];

  async function api(method, url, body) {
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
      body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { logout(); throw new Error("Please sign in again"); }
    if (!res.ok) {
      let msg = data.detail; if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
      throw new Error(msg || res.statusText);
    }
    return data;
  }

  $("pLoginForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("pErr").textContent = "";
    const res = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("pEmail").value, password: $("pPw").value }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { $("pErr").textContent = data.detail || "Sign in failed"; return; }
    if (!data.is_platform_admin) { $("pErr").textContent = "This account is not a platform admin."; return; }
    token = data.access_token; sessionStorage.setItem("ff-admin", token); start();
  });
  function logout() { sessionStorage.removeItem("ff-admin"); token = null; $("pMain").classList.add("hidden"); $("pLogin").classList.remove("hidden"); }
  $("pLogout").addEventListener("click", logout);
  $("pRefresh").addEventListener("click", load);
  $("pFilter").addEventListener("change", render);
  $("pSearch").addEventListener("input", render);

  async function start() {
    try {
      const me = await api("GET", "/api/v1/auth/me");
      if (!me.is_platform_admin) { logout(); $("pErr").textContent = "This account is not a platform admin."; return; }
      $("pWho").textContent = me.email;
      $("pLogin").classList.add("hidden"); $("pMain").classList.remove("hidden");
      load();
    } catch (_) { logout(); }
  }

  async function load() {
    const data = await api("GET", "/api/v1/platform/workspaces");
    rows = data.items;
    const s = data.summary;
    $("pCards").innerHTML = [["Workspaces", s.total], ["On trial", s.TRIAL], ["Active", s.ACTIVE], ["Trial ended", s.EXPIRED],
      ["Suspended", s.SUSPENDED], ["Signed up, no workspace yet", s.signed_up_without_workspace]]
      .map(([k, v]) => `<div class="card"><span class="big">${v}</span><span class="small muted">${k}</span></div>`).join("");
    render();
  }

  function render() {
    const f = $("pFilter").value, q = $("pSearch").value.trim().toLowerCase();
    const list = rows.filter((w) => (!f || w.status === f) && (!q || `${w.name} ${w.owner_email || ""}`.toLowerCase().includes(q)));
    $("pTable").innerHTML = `<tr><th>Centre</th><th>Owner</th><th>Signed up</th><th>Status</th><th>Students</th><th>Fees (paid)</th><th>SMS phone</th><th>Last activity</th><th></th></tr>` +
      (list.length ? list.map((w) => `<tr>
        <td><b>${esc(w.name)}</b><br><span class="small muted">${esc(w.short_code)} · ${esc(w.upi_vpa)}</span></td>
        <td>${esc(w.owner_name || "")}<br><span class="small muted">${esc(w.owner_email || "-")}</span></td>
        <td>${t(w.created_at)}</td>
        <td>${pill(w.status)}<br><span class="small muted">${w.status === "TRIAL" ? `${w.trial_days_left} days left` : w.status === "EXPIRED" ? `ended ${t(w.trial_ends_at)}` : w.status === "SUSPENDED" ? esc(w.suspended_reason || "") : ""}</span></td>
        <td>${w.students}</td><td>${w.fees} (${w.fees_paid})</td><td>${w.sms_phone_active ? "Yes" : "No"}</td><td>${t(w.last_activity_at)}</td>
        <td class="actions">
          ${w.status !== "SUSPENDED" ? `<button class="btn ghost sm" data-act="extend" data-id="${esc(w.id)}">+30 days trial</button>` : ""}
          ${w.status !== "ACTIVE" && w.status !== "SUSPENDED" ? `<button class="btn sm ok" data-act="activate" data-id="${esc(w.id)}">Activate</button>` : ""}
          ${w.status !== "SUSPENDED" ? `<button class="btn sm bad" data-act="suspend" data-id="${esc(w.id)}">Suspend</button>` : `<button class="btn sm" data-act="reactivate" data-id="${esc(w.id)}">Reactivate</button>`}
        </td></tr>`).join("") : '<tr><td colspan="9" class="muted">No workspaces match.</td></tr>');
    $("pTable").querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => act(b.dataset.act, b.dataset.id)));
  }

  async function act(action, id) {
    const w = rows.find((r) => r.id === id);
    try {
      if (action === "extend") {
        if (!confirm(`Extend the trial of ${w.name} by 30 days?`)) return;
        await api("POST", `/api/v1/platform/workspaces/${encodeURIComponent(id)}/extend-trial`, { days: 30 });
      } else if (action === "activate") {
        if (!confirm(`Activate ${w.name}? It will no longer be limited by the trial.`)) return;
        await api("POST", `/api/v1/platform/workspaces/${encodeURIComponent(id)}/activate`);
      } else if (action === "suspend") {
        const reason = prompt(`Suspend ${w.name}?\n\nThe owner is signed out, its payment pages close and its SMS phone stops.\n\nReason (recorded in the audit log):`);
        if (!reason) return;
        await api("POST", `/api/v1/platform/workspaces/${encodeURIComponent(id)}/suspend`, { reason });
      } else if (action === "reactivate") {
        if (!confirm(`Reactivate ${w.name}?`)) return;
        await api("POST", `/api/v1/platform/workspaces/${encodeURIComponent(id)}/reactivate`);
      }
      load();
    } catch (err) { alert(err.message); }
  }

  if (token) start();
})();
