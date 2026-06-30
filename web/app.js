"use strict";
// Camera Recognizer - controleur UI.
// Securite: toute donnee dynamique passe par esc() (echappe & < > ") avant
// insertion HTML. App 100% locale mono-utilisateur, aucune source distante.

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let CONFIG = null;
let CURRENT_PHOTO_PID = null;
let MODELS_OK = false, PERSONS_OK = false, CAM_ON = false;

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function setHTML(el, html) { el.innerHTML = html; } // contenu deja echappe via esc()

const ACTION_TYPES = {
  say: { label: "Dire (TTS)", fields: [["text", "Texte ({name})"]] },
  open_url: { label: "Ouvrir une URL", fields: [["url", "https://…"]] },
  launch_app: { label: "Lancer une app", fields: [["path", "Chemin .exe"]] },
  open_folder: { label: "Ouvrir un dossier", fields: [["path", "C:\\Users\\…"]] },
  type_text: { label: "Taper du texte", fields: [["text", "Texte à taper ({name})"]] },
  keys: { label: "Touches clavier", fields: [["combo", "ex. ctrl+shift+s"]] },
  media: { label: "Média (lecture)", fields: [["key", "play / pause / next / prev / stop"]] },
  volume: { label: "Volume", fields: [["dir", "up / down / mute"], ["steps", "pas (ex. 4)"]] },
  screenshot: { label: "Capture d'écran", fields: [] },
  clipboard: { label: "Copier dans le presse-papiers", fields: [["text", "Texte ({name})"]] },
  paste: { label: "Coller (Ctrl+V)", fields: [] },
  mouse_click: { label: "Clic souris", fields: [["button", "left / right / double"], ["x", "X (option)"], ["y", "Y (option)"]] },
  mouse_move: { label: "Déplacer la souris", fields: [["x", "X"], ["y", "Y"]] },
  window: { label: "Fenêtre", fields: [["op", "minimize_all / maximize / close / switch"]] },
  play_sound: { label: "Jouer un son (.wav)", fields: [["path", "C:\\…\\son.wav"]] },
  say_time: { label: "Annoncer l'heure", fields: [] },
  http_request: { label: "Requête HTTP (webhook)", fields: [["url", "https://…"], ["method", "GET / POST"]] },
  run_rule: { label: "Lancer une autre règle", fields: [["rule_id", "id de la règle"]] },
  alarm: { label: "Déclencher l'alarme", fields: [] },
  stop_alarm: { label: "Arrêter l'alarme", fields: [] },
  lock: { label: "Verrouiller la session", fields: [] },
  power: { label: "Alimentation PC", fields: [["op", "sleep / shutdown / restart / logoff / hibernate"]] },
  notification: { label: "Notification", fields: [["text", "Message"]] },
  shell: { label: "Commande système", fields: [["command", "commande"]] },
  delay: { label: "Pause", fields: [["ms", "millisecondes"]] },
};
const TRIGGER_TYPES = {
  voice_command: "Commande vocale",
  face_recognized: "Visage reconnu",
  speaker_recognized: "Voix reconnue",
  motion_detected: "Mouvement détecté",
  hand_detected: "Main détectée",
  hand_sequence: "Suite de gestes",
  manual: "Manuel (bouton)",
  startup: "Au démarrage",
};
const GESTURES = ["any", "poing", "main ouverte", "1 doigts", "2 doigts", "3 doigts", "4 doigts"];

// ---------------- API helpers ----------------
async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  return r.json();
}
function toast(msg, level = "info") {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

// ---------------- Navigation ----------------
$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  $$(".nav button").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${btn.dataset.view}`));
});

// ---------------- WebSocket events ----------------
function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connectWS, 1500);
}
function logEvent(html) {
  const box = $("#events");
  const t = new Date().toLocaleTimeString("fr-FR");
  const row = document.createElement("div");
  row.className = "ev event-new";
  setHTML(row, `<span class="ts">${esc(t)}</span> ${html}`);
  box.insertBefore(row, box.firstChild);
  while (box.children.length > 60) box.removeChild(box.lastChild);
}
function goView(name) {
  const btn = document.querySelector(`.nav button[data-view="${name}"]`);
  if (btn) btn.click();
}
function handleEvent(m) {
  switch (m.event) {
    case "status": applyStatus(m); break;
    case "transcript": {
      const box = $("#transcript");
      const clean = box.innerHTML.replace(/^<span class="partial">[\s\S]*?<\/span>\n/, "");
      if (m.final) setHTML(box, `<span class="ts">▸</span> ${esc(m.text)}\n` + clean);
      else setHTML(box, `<span class="partial">… ${esc(m.text)}</span>\n` + clean);
      break;
    }
    case "face": logEvent(m.id ? `Visage reconnu : <b>${esc(m.name)}</b> (${esc(m.score)})` : `Visage inconnu (${esc(m.score)})`); break;
    case "speaker": logEvent(m.id ? `Voix reconnue : <b>${esc(m.name)}</b> (${esc(m.score)})` : `Voix inconnue (${esc(m.score)})`); break;
    case "speaker_enrolled": onSpeakerEnrolled(m); break;
    case "motion": logEvent(`Mouvement détecté (${esc(m.pixels)}px)`); break;
    case "hand": logEvent(`Main détectée : ${esc(m.count)} · geste « ${esc(m.gesture)} »`); break;
    case "command": logEvent(`Commande vocale : « ${esc(m.phrase)} » → ${esc(m.rule)}`); break;
    case "alarm": setAlarm(m.active); break;
    case "model_progress": updateModelProgress(m); break;
    case "models_done": toast("Modèles à jour.", "ok"); applyStatus(m); loadModels(); refreshDevices(); break;
    case "update_progress": onUpdateProgress(m); break;
    case "update_done": onUpdateDone(m); break;
    case "toast": toast(m.message, m.level || "info"); break;
  }
}
function applyStatus(s) {
  if (s.fps !== undefined) $("#fps").textContent = `${s.fps} fps`;
  if (s.camera !== undefined) { CAM_ON = s.camera; updateQuickStart(); }
  toggleChip("chip-camera", s.camera);
  toggleChip("chip-voice", s.voice);
  toggleChip("chip-face", s.face_ready);
  toggleChip("chip-speaker", s.speaker_ready);
  toggleChip("chip-hands", s.hands_ready);
  toggleChip("chip-tts", s.tts_ready);
  if (s.alarm !== undefined) setAlarm(s.alarm);
}
function toggleChip(id, on) { $(`#${id}`).classList.toggle("on", !!on); }
function setAlarm(active) { $("#alarm-banner").classList.toggle("show", !!active); }

// ---------------- Quick start (intuitivité) ----------------
function updateQuickStart() {
  $("#qs-models").classList.toggle("done", MODELS_OK);
  $("#qs-enroll").classList.toggle("done", PERSONS_OK);
  $("#qs-start").classList.toggle("done", CAM_ON);
  // étape active = première non faite
  const order = [["qs-models", MODELS_OK], ["qs-start", CAM_ON], ["qs-enroll", PERSONS_OK]];
  const next = order.find(([, ok]) => !ok);
  order.forEach(([id]) => $(`#${id}`).classList.toggle("active", !!next && next[0] === id));
  $("#quickstart").style.display = (MODELS_OK && CAM_ON && PERSONS_OK) ? "none" : "";
}
$("#quickstart").addEventListener("click", (e) => {
  const step = e.target.closest(".qs-step");
  if (!step) return;
  if (step.dataset.go) goView(step.dataset.go);
  else if (step.dataset.act === "all-start") $("#btn-all-start").click();
});

// ---------------- Live controls ----------------
async function startCameraFeed() {
  const wrap = $("#feed-wrap");
  wrap.classList.add("booting");
  setTimeout(() => wrap.classList.remove("booting"), 1200);
  const r = await api("/api/camera/start", "POST");
  if (r.ok) $("#feed").src = `/video_feed?t=${Date.now()}`;
  return r;
}
$("#btn-cam-start").onclick = async () => {
  const r = await startCameraFeed();
  toast(r.message, r.ok ? "ok" : "error");
};
$("#btn-cam-stop").onclick = async () => { await api("/api/camera/stop", "POST"); $("#feed").src = ""; };
$("#btn-voice-start").onclick = async () => { const r = await api("/api/voice/start", "POST"); toast(r.message, r.ok ? "ok" : "error"); };
$("#btn-voice-stop").onclick = async () => { await api("/api/voice/stop", "POST"); };
$("#btn-all-start").onclick = async () => {
  const c = await startCameraFeed();
  if (!c.ok) toast(c.message, "error");
  const v = await api("/api/voice/start", "POST");
  if (!v.ok) toast(v.message, "error");
  if (c.ok && v.ok) toast("Caméra + micro démarrés.", "ok");
};
$("#btn-all-stop").onclick = async () => { await api("/api/camera/stop", "POST"); await api("/api/voice/stop", "POST"); $("#feed").src = ""; };
$("#btn-stop-alarm").onclick = () => api("/api/alarm/stop", "POST");

// ---------------- Persons ----------------
async function loadPersons() {
  const { persons } = await api("/api/persons");
  PERSONS_OK = persons.some((p) => p.enrolled);
  updateQuickStart();
  const box = $("#persons-list");
  if (!persons.length) { setHTML(box, `<div class="empty">Aucune personne enrôlée. Crée-en une à droite.</div>`); return; }
  setHTML(box, persons.map((p) => `
    <div class="person" data-id="${esc(p.id)}">
      <header>
        <span class="name">${esc(p.name)}</span>
        <span class="badge ${p.enrolled ? "ok" : "warn"}">${p.enrolled ? "enrôlé" : "sans photo"}</span>
      </header>
      <div class="meta">${esc(p.photos.length)} photo(s) · salutation : ${esc(p.greeting || "Bonjour " + p.name)}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn" data-act="upload">+ Photos</button>
        <button class="btn" data-act="capture">Capturer caméra</button>
        <button class="btn ghost" data-act="rename">Renommer</button>
        <button class="btn danger" data-act="delete">Supprimer</button>
      </div>
    </div>`).join(""));
}
$("#persons-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const pid = btn.closest(".person").dataset.id;
  const act = btn.dataset.act;
  if (act === "upload") { CURRENT_PHOTO_PID = pid; $("#file-input").click(); }
  else if (act === "capture") { const r = await api(`/api/persons/${pid}/capture`, "POST"); toast(r.message, r.ok ? "ok" : "error"); loadPersons(); }
  else if (act === "delete") { if (confirm("Supprimer cette personne et ses photos ?")) { await api(`/api/persons/${pid}`, "DELETE"); loadPersons(); } }
  else if (act === "rename") { renamePerson(pid); }
});
async function renamePerson(pid) {
  const name = prompt("Nouveau nom :");
  if (name === null) return;
  const greeting = prompt("Salutation ({name} supporté) :") ?? "";
  await api(`/api/persons/${pid}`, "PUT", { name, greeting });
  loadPersons();
}
$("#btn-create-person").onclick = async () => {
  const name = $("#new-person-name").value.trim();
  if (!name) return toast("Donne un nom.", "error");
  await api("/api/persons", "POST", { name, greeting: $("#new-person-greeting").value.trim() });
  $("#new-person-name").value = ""; $("#new-person-greeting").value = "";
  loadPersons();
};
$("#file-input").onchange = async (e) => {
  const files = Array.from(e.target.files);
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`/api/persons/${CURRENT_PHOTO_PID}/photo`, { method: "POST", body: fd }).then((x) => x.json());
    toast(`${f.name}: ${r.message}`, r.ok ? "ok" : "error");
  }
  e.target.value = "";
  loadPersons();
};

// ---------------- Enrollment camera preview ----------------
async function startEnrollCam() {
  const wrap = $("#enroll-feed-wrap");
  const r = await api("/api/camera/start", "POST");
  if (r.ok) {
    $("#enroll-feed").src = `/video_feed?t=${Date.now()}`;
    wrap.classList.add("live-on");
    CAM_ON = true; updateQuickStart();
  } else toast(r.message, "error");
}
$("#btn-enroll-cam").onclick = startEnrollCam;
$("#btn-enroll-cam-stop").onclick = () => {
  $("#enroll-feed").src = "";
  $("#enroll-feed-wrap").classList.remove("live-on");
};

// ---------------- Speakers (reconnaissance de la voix) ----------------
let ENROLLING_SID = null;
async function loadSpeakers() {
  const { speakers, spk_ready, listening } = await api("/api/speakers");
  const box = $("#speakers-list");
  if (!spk_ready) { setHTML(box, `<div class="empty">Modèle locuteur absent. Va dans Paramètres → Modèles → Télécharger.</div>`); return; }
  if (!speakers.length) { setHTML(box, `<div class="empty">Aucune voix enrôlée. Crée-en une à droite.</div>`); return; }
  setHTML(box, speakers.map((s) => `
    <div class="person" data-id="${esc(s.id)}">
      <header>
        <span class="name">${esc(s.name)}</span>
        <span class="badge ${s.enrolled ? "ok" : "warn"}">${s.enrolled ? esc(s.samples) + " échantillon(s)" : "non enrôlé"}</span>
      </header>
      <div class="meta">salutation : ${esc(s.greeting || "Bonjour " + s.name)}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn" data-act="enroll">🎙 Enrôler ma voix</button>
        <button class="btn ghost" data-act="rename">Renommer</button>
        <button class="btn danger" data-act="delete">Supprimer</button>
      </div>
    </div>`).join(""));
  if (!listening) toast("Démarre le micro (Live) pour enrôler une voix.", "info");
}
$("#speakers-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const sid = btn.closest(".person").dataset.id;
  const act = btn.dataset.act;
  if (act === "enroll") {
    const r = await api(`/api/speakers/${sid}/enroll`, "POST");
    toast(r.message, r.ok ? "info" : "error");
    if (r.ok) { ENROLLING_SID = sid; btn.textContent = "⏺ Parle…"; btn.disabled = true; }
  } else if (act === "delete") {
    if (confirm("Supprimer cette voix ?")) { await api(`/api/speakers/${sid}`, "DELETE"); loadSpeakers(); }
  } else if (act === "rename") {
    const name = prompt("Nouveau nom :"); if (name === null) return;
    const greeting = prompt("Salutation ({name} supporté) :") ?? "";
    await api(`/api/speakers/${sid}`, "PUT", { name, greeting });
    loadSpeakers();
  }
});
function onSpeakerEnrolled(m) {
  ENROLLING_SID = null;
  toast(m.ok ? `Voix enregistrée (${m.samples} échantillon(s)). Répète pour affiner.` : m.message, m.ok ? "ok" : "error");
  loadSpeakers();
}
$("#btn-create-speaker").onclick = async () => {
  const name = $("#new-speaker-name").value.trim();
  if (!name) return toast("Donne un nom.", "error");
  await api("/api/speakers", "POST", { name, greeting: $("#new-speaker-greeting").value.trim() });
  $("#new-speaker-name").value = ""; $("#new-speaker-greeting").value = "";
  loadSpeakers();
};
$("#btn-save-speaker-th").onclick = async () => {
  const t = { ...CONFIG.thresholds, speaker: parseFloat($("#speaker-threshold").value) };
  await api("/api/config", "POST", { thresholds: t });
  CONFIG = await api("/api/config");
  toast("Seuil voix enregistré.", "ok");
};

// ---------------- Rules (actions) ----------------
function renderRules() {
  const box = $("#rules-list");
  const rules = CONFIG.rules || [];
  if (!rules.length) { setHTML(box, `<div class="empty">Aucune règle. Crée ta première règle déclencheur → action.</div>`); return; }
  setHTML(box, rules.map((r) => `
    <div class="rule" data-id="${esc(r.id)}">
      <header>
        <span class="name">${esc(r.name)}</span>
        <span class="badge ${r.enabled ? "ok" : "off"}">${r.enabled ? "actif" : "inactif"}</span>
      </header>
      <div class="meta">Quand : ${esc(TRIGGER_TYPES[r.trigger.type] || r.trigger.type)}${r.trigger.phrase ? ` « ${esc(r.trigger.phrase)} »` : ""}${r.trigger.person ? ` (${esc(r.trigger.person)})` : ""}</div>
      <div>${r.actions.map((a) => `<span class="action-pill">${esc(ACTION_TYPES[a.type]?.label || a.type)}</span>`).join("")}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn" data-act="test">Tester</button>
        <button class="btn" data-act="fire">Déclencher</button>
        <button class="btn ghost" data-act="toggle">${r.enabled ? "Désactiver" : "Activer"}</button>
        <button class="btn ghost" data-act="edit">Éditer</button>
        <button class="btn danger" data-act="del">Supprimer</button>
      </div>
    </div>`).join(""));
}
$("#rules-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".rule").dataset.id;
  const rule = CONFIG.rules.find((r) => r.id === id);
  const act = btn.dataset.act;
  if (act === "test") { await api("/api/actions/test", "POST", { actions: rule.actions }); toast("Test lancé."); }
  else if (act === "fire") { await api(`/api/actions/fire/${id}`, "POST"); }
  else if (act === "toggle") { rule.enabled = !rule.enabled; await saveRules(); }
  else if (act === "del") { if (confirm("Supprimer cette règle ?")) { CONFIG.rules = CONFIG.rules.filter((r) => r.id !== id); await saveRules(); } }
  else if (act === "edit") { openRuleModal(rule); }
});
$("#btn-new-rule").onclick = () => openRuleModal(null);

async function saveRules() {
  await api("/api/config", "POST", { rules: CONFIG.rules });
  renderRules();
}
function openRuleModal(rule) {
  const editing = !!rule;
  const r = rule ? JSON.parse(JSON.stringify(rule)) : { id: "rule_" + Math.random().toString(36).slice(2, 9), name: "", enabled: true, trigger: { type: "voice_command", phrase: "" }, actions: [] };
  const modal = $("#modal");
  const trigOpts = Object.entries(TRIGGER_TYPES).map(([k, v]) => `<option value="${k}" ${r.trigger.type === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  setHTML(modal, `
    <h3>${editing ? "Éditer la règle" : "Nouvelle règle"}</h3>
    <label class="field"><span>Nom</span><input type="text" id="m-name" value="${esc(r.name)}" placeholder="ex. Salut vocal"></label>
    <label class="field"><span>Déclencheur</span><select id="m-trigger">${trigOpts}</select></label>
    <div id="m-trigger-extra"></div>
    <h3 style="margin-top:16px">Actions</h3>
    <div id="m-actions"></div>
    <button class="btn" id="m-add-action" style="margin-top:8px">+ Ajouter une action</button>
    <div class="row" style="margin-top:18px; justify-content:flex-end">
      <button class="btn ghost" id="m-cancel">Annuler</button>
      <button class="btn primary" id="m-save">Enregistrer</button>
    </div>`);
  $("#modal-bg").classList.add("show");

  const renderTriggerExtra = () => {
    const t = $("#m-trigger").value;
    const box = $("#m-trigger-extra");
    if (t === "voice_command") setHTML(box, `
      <label class="field"><span>Phrase déclencheur</span><input type="text" id="m-phrase" value="${esc(r.trigger.phrase || "")}" placeholder="ex. ouvre eaglercraft"></label>
      <label class="field"><span>Formulations équivalentes (une par ligne · « dire la même chose »)</span><textarea id="m-synonyms" rows="3" placeholder="lance eaglercraft&#10;démarre eaglercraft">${esc((r.trigger.synonyms || []).join("\n"))}</textarea></label>`);
    else if (t === "face_recognized") {
      const opts = `<option value="any_known">Toute personne connue</option><option value="unknown">Inconnu détecté</option>` +
        (CONFIG.persons || []).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
      setHTML(box, `<label class="field"><span>Cible</span><select id="m-person">${opts}</select></label>`);
      if (r.trigger.person) $("#m-person").value = r.trigger.person;
    } else if (t === "speaker_recognized") {
      const opts = `<option value="any_known">Toute voix connue</option><option value="unknown">Voix inconnue</option>` +
        (CONFIG.speakers || []).map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
      setHTML(box, `<label class="field"><span>Cible</span><select id="m-speaker">${opts}</select></label>`);
      if (r.trigger.speaker) $("#m-speaker").value = r.trigger.speaker;
    } else if (t === "hand_detected") {
      const opts = GESTURES.map((g) => `<option value="${esc(g)}">${g === "any" ? "N'importe quel geste" : esc(g)}</option>`).join("");
      setHTML(box, `<label class="field"><span>Geste</span><select id="m-gesture">${opts}</select></label>`);
      if (r.trigger.gesture) $("#m-gesture").value = r.trigger.gesture;
    } else if (t === "hand_sequence") {
      const list = (r.trigger.sequence || []).join(", ");
      setHTML(box, `<label class="field"><span>Suite de gestes (séparés par virgule · dans l'ordre)</span><input type="text" id="m-sequence" value="${esc(list)}" placeholder="poing, main ouverte, poing"></label><div class="meta">Gestes possibles : poing, main ouverte, 1 doigts … 4 doigts. À faire en moins de 4 s.</div>`);
    } else setHTML(box, "");
  };
  const renderActions = () => {
    const box = $("#m-actions");
    setHTML(box, r.actions.map((a, i) => {
      const typeOpts = Object.entries(ACTION_TYPES).map(([k, v]) => `<option value="${k}" ${a.type === k ? "selected" : ""}>${esc(v.label)}</option>`).join("");
      const fields = (ACTION_TYPES[a.type]?.fields || []).map(([key, ph]) => `<input type="text" data-field="${key}" data-i="${i}" value="${esc(a[key] ?? "")}" placeholder="${esc(ph)}">`).join("") || `<span class="meta">aucun paramètre</span>`;
      return `<div class="act-edit"><select data-type="${i}">${typeOpts}</select><div>${fields}</div><button class="btn danger" data-del="${i}">×</button></div>`;
    }).join("") || `<div class="meta">aucune action — ajoute-en une.</div>`);
  };
  renderTriggerExtra();
  renderActions();

  $("#m-trigger").onchange = renderTriggerExtra;
  $("#m-add-action").onclick = () => { r.actions.push({ type: "say", text: "" }); renderActions(); };
  $("#m-actions").addEventListener("change", (e) => {
    const ti = e.target.dataset.type;
    if (ti !== undefined) { r.actions[ti] = { type: e.target.value }; renderActions(); }
    const fi = e.target.dataset.i;
    if (fi !== undefined) r.actions[fi][e.target.dataset.field] = e.target.value;
  });
  $("#m-actions").addEventListener("click", (e) => {
    const di = e.target.dataset.del;
    if (di !== undefined) { r.actions.splice(di, 1); renderActions(); }
  });
  $("#m-cancel").onclick = closeModal;
  $("#m-save").onclick = async () => {
    r.name = $("#m-name").value.trim() || "Règle";
    r.trigger.type = $("#m-trigger").value;
    if (r.trigger.type === "voice_command") r.trigger = { type: "voice_command", phrase: ($("#m-phrase")?.value || "").trim(), synonyms: ($("#m-synonyms")?.value || "").split("\n").map((s) => s.trim()).filter(Boolean) };
    else if (r.trigger.type === "face_recognized") r.trigger = { type: "face_recognized", person: $("#m-person")?.value || "any_known" };
    else if (r.trigger.type === "speaker_recognized") r.trigger = { type: "speaker_recognized", speaker: $("#m-speaker")?.value || "any_known" };
    else if (r.trigger.type === "hand_detected") r.trigger = { type: "hand_detected", gesture: $("#m-gesture")?.value || "any" };
    else if (r.trigger.type === "hand_sequence") r.trigger = { type: "hand_sequence", sequence: ($("#m-sequence")?.value || "").split(",").map((s) => s.trim()).filter(Boolean) };
    else r.trigger = { type: r.trigger.type };
    $$("#m-actions .act-edit").forEach((row, i) => {
      row.querySelectorAll("input[data-field]").forEach((inp) => { r.actions[i][inp.dataset.field] = inp.value; });
    });
    const idx = CONFIG.rules.findIndex((x) => x.id === r.id);
    if (idx >= 0) CONFIG.rules[idx] = r; else CONFIG.rules.push(r);
    await saveRules();
    closeModal();
    toast("Règle enregistrée.", "ok");
  };
}
function closeModal() { $("#modal-bg").classList.remove("show"); }
$("#modal-bg").onclick = (e) => { if (e.target.id === "modal-bg") closeModal(); };

// import / export
$("#btn-export").onclick = () => {
  const blob = new Blob([JSON.stringify({ rules: CONFIG.rules }, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "camera-recognizer-actions.json";
  a.click();
};
$("#btn-import").onclick = () => $("#import-input").click();
$("#import-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (Array.isArray(data.rules)) { CONFIG.rules = data.rules; await saveRules(); toast("Règles importées.", "ok"); }
    else toast("Fichier invalide.", "error");
  } catch { toast("JSON illisible.", "error"); }
  e.target.value = "";
};

// ---------------- Settings ----------------
function bindSlider(id, valId, fmt) {
  const el = $(`#${id}`), out = $(`#${valId}`);
  const upd = () => { out.textContent = fmt ? fmt(el.value) : el.value; };
  el.addEventListener("input", upd);
  return upd;
}
async function refreshDevices() {
  const [{ cameras }, { microphones }, { voices, current }] = await Promise.all([
    api("/api/cameras"), api("/api/microphones"), api("/api/voices"),
  ]);
  setHTML($("#sel-camera"), cameras.length ? cameras.map((i) => `<option value="${esc(i)}">Caméra ${esc(i)}</option>`).join("") : `<option value="0">Caméra 0</option>`);
  $("#sel-camera").value = CONFIG.camera_index ?? 0;
  setHTML($("#sel-mic"), `<option value="">Par défaut</option>` + microphones.map((m) => `<option value="${esc(m.index)}">${esc(m.name)}</option>`).join(""));
  $("#sel-mic").value = CONFIG.mic_index ?? "";
  setHTML($("#sel-voice"), voices.length ? voices.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("") : `<option value="">(aucune voix — télécharger les modèles)</option>`);
  if (current) $("#sel-voice").value = current;
}
$("#btn-save-devices").onclick = async () => {
  await api("/api/config", "POST", {
    camera_index: parseInt($("#sel-camera").value || "0"),
    mic_index: $("#sel-mic").value === "" ? null : parseInt($("#sel-mic").value),
    tts_voice: $("#sel-voice").value,
    tts_volume: parseFloat($("#tts-volume").value),
    stt_engine: $("#sel-stt").value,
  });
  CONFIG = await api("/api/config");
  toast("Périphériques enregistrés.", "ok");
};
function updateSttNote() {
  const v = $("#sel-stt").value;
  $("#stt-note").textContent = v === "parakeet"
    ? "⚠ Parakeet v3 (NVIDIA) nécessite un GPU et l'installation source (torch + nemo). Indisponible dans l'exe portable — Vosk reste actif."
    : "Vosk fonctionne 100% en local sur CPU, sans connexion.";
}
$("#sel-stt").addEventListener("change", updateSttNote);

// ---------------- Mises à jour ----------------
let PENDING_UPDATE_URL = null;
$("#btn-check-update").onclick = async () => {
  $("#update-status").textContent = "Vérification…";
  const r = await api("/api/update/check", "POST");
  $("#update-status").textContent = r.message || "";
  if (r.available) {
    PENDING_UPDATE_URL = r.download_url;
    $("#update-status").textContent = `Nouvelle version ${r.latest} disponible (actuelle ${r.current}).`;
    $("#btn-apply-update").style.display = "";
  } else {
    $("#btn-apply-update").style.display = "none";
  }
};
$("#btn-apply-update").onclick = async () => {
  if (!PENDING_UPDATE_URL) return;
  if (!confirm("Télécharger et installer la mise à jour ? L'application va redémarrer.")) return;
  $("#update-bar").style.display = "";
  await api("/api/update/apply", "POST", { download_url: PENDING_UPDATE_URL });
};
function onUpdateProgress(m) {
  const bar = $("#update-bar > span");
  if (bar) bar.style.width = `${m.pct}%`;
  $("#update-status").textContent = `Mise à jour : ${m.message} (${m.pct}%)`;
}
function onUpdateDone(m) {
  $("#update-status").textContent = m.message || "";
  toast(m.message, m.ok ? "ok" : "error");
}
$("#btn-save-thresholds").onclick = async () => {
  await api("/api/config", "POST", {
    thresholds: {
      face: parseFloat($("#face-threshold").value),
      voice: parseFloat($("#voice-threshold").value),
      motion: parseInt($("#motion-threshold").value),
    },
    greeting_debounce_s: parseInt($("#greet-debounce").value),
    motion_cooldown_s: parseInt($("#motion-cooldown").value),
    hand_cooldown_s: parseInt($("#hand-cooldown").value),
    hands_enabled: $("#hands-enabled").checked,
  });
  CONFIG = await api("/api/config");
  toast("Seuils enregistrés.", "ok");
};

// ---------------- Models ----------------
async function loadModels() {
  const { models } = await api("/api/models/status");
  MODELS_OK = Object.values(models).every((m) => m.present);
  updateQuickStart();
  const box = $("#models-list");
  setHTML(box, Object.entries(models).map(([key, m]) => `
    <div class="model-line" data-key="${esc(key)}">
      <span class="badge ${m.present ? "ok" : "warn"}">${m.present ? "présent" : "manquant"}</span>
      <span class="lbl">${esc(m.label)}</span>
      <div class="bar"><span style="width:${m.present ? 100 : 0}%"></span></div>
    </div>`).join(""));
}
function updateModelProgress(m) {
  const line = $(`.model-line[data-key="${m.key}"] .bar > span`);
  if (line) line.style.width = `${m.pct}%`;
}
$("#btn-dl-models").onclick = async () => { await api("/api/models/download", "POST"); toast("Téléchargement démarré…"); };

// ---------------- init ----------------
async function init() {
  CONFIG = await api("/api/config");
  $("#data-dir").textContent = "Données : ./data/";
  $("#face-threshold").value = CONFIG.thresholds.face;
  $("#voice-threshold").value = CONFIG.thresholds.voice;
  $("#motion-threshold").value = CONFIG.thresholds.motion;
  $("#greet-debounce").value = CONFIG.greeting_debounce_s;
  $("#motion-cooldown").value = CONFIG.motion_cooldown_s;
  $("#hand-cooldown").value = CONFIG.hand_cooldown_s ?? 5;
  $("#hands-enabled").checked = CONFIG.hands_enabled !== false;
  $("#tts-volume").value = CONFIG.tts_volume ?? 1;
  $("#speaker-threshold").value = CONFIG.thresholds.speaker ?? 0.55;
  $("#sel-stt").value = CONFIG.stt_engine || "vosk";
  const fth = bindSlider("face-threshold", "face-th-val");
  const vth = bindSlider("voice-threshold", "voice-th-val", (v) => `${v}%`);
  const mth = bindSlider("motion-threshold", "motion-th-val", (v) => `${v}px`);
  const gv = bindSlider("greet-debounce", "greet-val", (v) => `${v}s`);
  const mcd = bindSlider("motion-cooldown", "motion-cd-val", (v) => `${v}s`);
  const hcd = bindSlider("hand-cooldown", "hand-cd-val", (v) => `${v}s`);
  const vol = bindSlider("tts-volume", "vol-val", (v) => `${Math.round(v * 100)}%`);
  const sth = bindSlider("speaker-threshold", "spk-th-val");
  [fth, vth, mth, gv, mcd, hcd, vol, sth].forEach((f) => f());
  updateSttNote();

  renderRules();
  await Promise.all([loadPersons(), refreshDevices(), loadModels(), loadSpeakers()]);
  try {
    const v = await api("/api/version");
    $("#app-version").textContent = v.version || "?";
  } catch (_) {}
  applyStatus(await api("/api/status"));
  connectWS();
}
init();
