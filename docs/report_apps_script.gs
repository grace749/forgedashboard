/**
 * The Forge — Monthly Report generator (Google Apps Script web app)
 * ------------------------------------------------------------------
 * Receives report data from the dashboard refresh and creates a formatted
 * Google Doc (owned by you), returning its link. Runs as YOU, so the Doc lives
 * in your Drive.
 *
 * SETUP (one time):
 *  1. https://script.google.com → New project.
 *  2. Delete the sample code, paste ALL of this file, Save.
 *  3. Deploy ▸ New deployment ▸ Web app.
 *       - Execute as: Me
 *       - Who has access: Anyone
 *     Deploy, authorise (it will ask for Docs/Drive permission), COPY the URL.
 *  4. GitHub → repo Settings → Secrets and variables → Actions → new secret
 *     REPORT_SCRIPT_URL = that URL.
 *  5. The report is auto-generated on the last Friday of each month. To make one
 *     now, run the "Refresh Dashboard Data" workflow with REPORT_FORCE (or ask
 *     Claude to trigger a forced run).
 */

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  return _json({ ok: true, note: "Forge monthly-report generator is live." });
}

function doPost(e) {
  try {
    var b = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    var doc = DocumentApp.create(b.title || "Forge Female Fitness — Monthly Report");
    var body = doc.getBody();
    body.appendParagraph(b.title || "Monthly Report").setHeading(DocumentApp.ParagraphHeading.TITLE);
    if (b.subtitle) {
      var sub = body.appendParagraph(b.subtitle);
      sub.setForegroundColor("#888888");
    }
    (b.sections || []).forEach(function (s) {
      body.appendParagraph(s.heading || "").setHeading(DocumentApp.ParagraphHeading.HEADING1);
      (s.lines || []).forEach(function (l) {
        body.appendListItem(String(l)).setGlyphType(DocumentApp.GlyphType.BULLET);
      });
    });
    doc.saveAndClose();
    // Link-shareable so it opens from the dashboard.
    try { DriveApp.getFileById(doc.getId()).setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW); } catch (e2) {}
    return _json({ ok: true, url: doc.getUrl(), id: doc.getId() });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}
