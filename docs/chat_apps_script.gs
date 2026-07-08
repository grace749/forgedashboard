/**
 * The Forge — Tab AI Chat proxy (Google Apps Script web app)
 * ------------------------------------------------------------------
 * The dashboard is a static page, so it can't safely hold the Anthropic API
 * key or call the API directly. This tiny web app does it: the browser POSTs
 * { tab, question, history, context } and gets back { answer }. The API key
 * lives in Script Properties and is never exposed to the browser.
 *
 * SETUP (one time):
 *  1. Go to https://script.google.com  →  New project.
 *  2. Delete the sample code, paste ALL of this file, Save.
 *  3. On the line below that says API_KEY, paste your Anthropic key between
 *     the quotes. Save again.
 *  4. Deploy ▸ New deployment ▸ type "Web app".
 *       - Execute as:  Me
 *       - Who has access:  Anyone
 *     Deploy, authorise, COPY the "Web app URL".
 *  5. In GitHub → repo Settings → Secrets and variables → Actions,
 *     add a secret  CHAT_SCRIPT_URL  = that URL.
 *  6. Re-run the dashboard refresh. The chat box on each tab now answers.
 *
 * Cost note: each question is one Haiku call over a small slice of that tab's
 * data — pennies. Nothing runs unless someone actually asks a question.
 */

// ── PASTE YOUR ANTHROPIC KEY HERE (between the quotes) ──────────────
var API_KEY = "";
// ───────────────────────────────────────────────────────────────────

var MODEL = "claude-haiku-4-5-20251001";

// Per-tab persona. Falls back to _default for anything not listed.
var PERSONAS = {
  finances:  "You are a UK fractional CFO for a small Northern Ireland gym (a limited company). You read live Starling bank data, category breakdowns, cash reserves and card/DD revenue.",
  retention: "You are a member-retention specialist for a boutique gym. You read churn history, at-risk members, lapsed members and trials, and class attendance.",
  members:   "You are a membership analyst for a boutique gym. You read the membership snapshot and mix.",
  inbody:    "You are a body-composition analyst. You read members' recent InBody scans and trends.",
  jumpstart: "You are a coach for a 6-week 'Jumpstart' onboarding programme. You read the current cohort, check-ins and conversion status.",
  leads:     "You are a sales assistant for a gym. You read inbound enquiries and their sources.",
  ads:       "You are a paid-social (Meta ads) expert. You read ad spend, leads and cost-per-lead.",
  growth:    "You are a growth strategist for a gym. You read the current growth-sprint data.",
  staff:     "You are a team/operations assistant for a gym. You read staff and class coverage.",
  sop:       "You are an operations assistant. You read the gym's standard operating procedures.",
  home:      "You are the studio assistant for a gym owner. You read today's brief, enquiries and alerts.",
  _default:  "You are a helpful analyst for a boutique gym owner."
};

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  return _json({ ok: true, note: "Forge chat proxy is live. POST { tab, question, history, context }." });
}

function doPost(e) {
  try {
    var b = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    var key = API_KEY || PropertiesService.getScriptProperties().getProperty("ANTHROPIC_API_KEY");
    if (!key) return _json({ error: "No Anthropic key — paste it into the API_KEY line at the top of the script." });
    if (!b.question) return _json({ error: "No question." });

    // The programming agent writes full sessions; every other agent stays concise.
    var brevity = (b.tab === "programming")
      ? " Write the FULL session in detail — every section, move, reps and the finisher. Do not truncate or summarise; finish the whole programme."
      : " Prefer a short direct answer, then at most 3 bullet points.";
    var system = (PERSONAS[b.tab] || PERSONAS._default) +
      " Answer a busy gym owner specifically, in British English. Use ONLY the DATA provided below; if it doesn't contain the answer, say so plainly rather than guessing. Money is GBP (£)." + brevity;

    var messages = [];
    (b.history || []).forEach(function (m) {
      if (m && m.role && m.content) messages.push({ role: m.role, content: String(m.content) });
    });
    messages.push({ role: "user", content: "DATA (JSON) for the " + (b.tab || "dashboard") + " tab:\n" + (b.context || "{}") + "\n\nQUESTION: " + b.question });

    // max_tokens is set by the client per tab (programming needs a lot); capped for safety.
    var maxTok = Math.min(parseInt(b.max_tokens, 10) || 900, 4096);
    var res = UrlFetchApp.fetch("https://api.anthropic.com/v1/messages", {
      method: "post",
      contentType: "application/json",
      muteHttpExceptions: true,
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01" },
      payload: JSON.stringify({ model: MODEL, max_tokens: maxTok, system: system, messages: messages })
    });
    var data = JSON.parse(res.getContentText());
    if (data && data.content && data.content[0] && data.content[0].text) {
      return _json({ answer: data.content[0].text });
    }
    return _json({ error: (data && data.error && data.error.message) || "No response from the model." });
  } catch (err) {
    return _json({ error: String(err) });
  }
}
