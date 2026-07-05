/**
 * The Forge — At-Risk shared store (Google Apps Script web app)
 * ------------------------------------------------------------------
 * Lets the dashboard save "contacted / no-longer-at-risk" ticks in one
 * shared place, so Grace, JoJo, Eilis etc. all see the same state on
 * any laptop — instead of each browser keeping its own copy.
 *
 * SETUP (one time, ~3 minutes):
 *  1. Go to https://script.google.com  →  New project.
 *  2. Delete the sample code, paste ALL of this file, Save.
 *  3. Deploy ▸ New deployment ▸ type "Web app".
 *       - Execute as:  Me
 *       - Who has access:  Anyone
 *     Click Deploy, authorise, then COPY the "Web app URL".
 *  4. In GitHub → repo Settings → Secrets and variables → Actions,
 *     add a secret named  ATRISK_SCRIPT_URL  = that URL.
 *  5. Re-run the dashboard refresh. Done — ticks now sync for everyone.
 *
 * A spreadsheet called "Forge At-Risk Store" is created automatically
 * the first time it runs; you never need to touch it.
 */

var SHEET_NAME = 'atrisk';

function _sheet() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SPREADSHEET_ID');
  var ss;
  if (id) {
    try { ss = SpreadsheetApp.openById(id); } catch (e) { ss = null; }
  }
  if (!ss) {
    ss = SpreadsheetApp.create('Forge At-Risk Store');
    props.setProperty('SPREADSHEET_ID', ss.getId());
  }
  var sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sh.getLastRow() === 0) sh.appendRow(['name', 'state']);   // header
  return sh;
}

function _readAll() {
  var sh = _sheet();
  var rows = sh.getDataRange().getValues();
  var out = {};
  for (var i = 1; i < rows.length; i++) {
    var name = String(rows[i][0] || '').trim();
    if (!name) continue;
    try { out[name] = JSON.parse(rows[i][1] || '{}'); } catch (e) { out[name] = {}; }
  }
  return out;
}

function _write(name, state) {
  var sh = _sheet();
  var rows = sh.getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    if (String(rows[i][0]).trim() === name) {
      sh.getRange(i + 1, 2).setValue(JSON.stringify(state || {}));
      return;
    }
  }
  sh.appendRow([name, JSON.stringify(state || {})]);
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  return _json({ ok: true, state: _readAll() });
}

function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.type === 'atrisk' && body.name) {
      _write(String(body.name).trim(), body.state || {});
    }
    return _json({ ok: true });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}
