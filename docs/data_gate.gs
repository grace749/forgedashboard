/**
 * The Forge — Private Data Gate (Google Apps Script web app)
 * ------------------------------------------------------------------
 * Serves the dashboard data ONLY to a signed-in user, and receives the freshly
 * built data from the GitHub refresh. The raw data lives in YOUR private Google
 * Drive — it is never a public file, so nobody can read it by guessing a URL.
 *
 * SETUP (one time):
 *  1. https://script.google.com → New project. Delete the sample, paste ALL of
 *     this file, Save.
 *  2. Project Settings (gear, left) → Script properties → add these:
 *       WRITE_SECRET  = a long random string (make one up, 30+ chars). The
 *                       GitHub refresh uses it to push data in.
 *       USERS         = {"grace@theforge.pt":{"code":"YOUR-OWNER-PASSWORD","role":"owner"},
 *                        "jojo@theforge.pt":{"code":"JOJO-PASSWORD","role":"coach"}}
 *                       (pick the two passwords; give JoJo hers.)
 *  3. Deploy ▸ New deployment ▸ Web app. Execute as: Me. Who has access: Anyone.
 *     Deploy, authorise, COPY the /exec URL.
 *  4. Add the URL as GitHub secret  DATA_GATE_URL,  and add  DATA_WRITE_SECRET
 *     = the same WRITE_SECRET value. Then tell Claude the /exec URL to switch the
 *     dashboard over.
 */

function _json(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }
function _props() { return PropertiesService.getScriptProperties(); }

function _folder() {
  var id = _props().getProperty("FOLDER_ID"), f = null;
  if (id) { try { f = DriveApp.getFolderById(id); } catch (e) {} }
  if (!f) { f = DriveApp.createFolder("ForgeDashboardData"); _props().setProperty("FOLDER_ID", f.getId()); }
  return f;
}
function _saveFile(name, content) {
  var it = _folder().getFilesByName(name);
  if (it.hasNext()) { var file = it.next(); file.setContent(content); return; }
  _folder().createFile(name, content, "application/json");
}
function _readFile(name) {
  var it = _folder().getFilesByName(name);
  return it.hasNext() ? it.next().getBlob().getDataAsString() : "{}";
}
function _fileFor(role) { return role === "coach" ? "coach.json" : "owner.json"; }

// Occasionally clear expired session tokens so properties don't pile up.
function _sweepSessions() {
  var p = _props(), all = p.getProperties(), now = Date.now();
  Object.keys(all).forEach(function (k) {
    if (k.indexOf("session:") === 0) {
      try { if (JSON.parse(all[k]).exp < now) p.deleteProperty(k); } catch (e) { p.deleteProperty(k); }
    }
  });
}

function doGet(e) { return _json({ ok: true, note: "Forge data gate is live." }); }

function doPost(e) {
  try {
    var b = JSON.parse((e && e.postData && e.postData.contents) || "{}");

    // ── Refresh pushes the freshly built data (owner + coach) ──
    if (b.type === "put") {
      if (b.secret !== _props().getProperty("WRITE_SECRET")) return _json({ error: "bad secret" });
      if (typeof b.owner === "string") _saveFile("owner.json", b.owner);
      if (typeof b.coach === "string") _saveFile("coach.json", b.coach);
      return _json({ ok: true });
    }

    // ── Sign in: email + code → a session token + that user's data ──
    if (b.type === "auth") {
      var users = JSON.parse(_props().getProperty("USERS") || "{}");
      var email = (b.email || "").toLowerCase();
      var u = users[email];
      if (!u || String(b.code) !== String(u.code)) return _json({ error: "Wrong email or password." });
      _sweepSessions();
      var token = Utilities.getUuid();
      _props().setProperty("session:" + token, JSON.stringify({ role: u.role, exp: Date.now() + 1000 * 60 * 60 * 24 * 30 }));
      return _json({ ok: true, role: u.role, token: token, data: _readFile(_fileFor(u.role)) });
    }

    // ── Resume a session (on reload) with the saved token ──
    if (b.type === "data") {
      var raw = _props().getProperty("session:" + (b.token || ""));
      if (!raw) return _json({ error: "expired" });
      var s = JSON.parse(raw);
      if (s.exp < Date.now()) { _props().deleteProperty("session:" + b.token); return _json({ error: "expired" }); }
      return _json({ ok: true, role: s.role, data: _readFile(_fileFor(s.role)) });
    }

    return _json({ error: "unknown type" });
  } catch (err) {
    return _json({ error: String(err) });
  }
}
