// Signup service for Writing by Email on davalerio.com.
//
//   POST /          the site's form: emails a signed confirmation link,
//                   then sends the reader to /subscribe/sent/ on the site
//   GET  /confirm   shows a Confirm Subscription button (a mail scanner
//                   that follows the link enrolls nobody)
//   POST /confirm   adds the address to the Writing segment in Resend,
//                   then sends the reader to /subscribe/confirmed/
//
// The link carries the address and the time it was issued, encrypted under a
// key derived from SIGNING_KEY (AES-GCM, so it can be neither read nor forged),
// and it expires after LINK_DAYS and works once. The hidden "website" field is
// a bot trap. Confirmation mail is capped by LIMIT_IP and LIMIT_EMAIL (per-minute
// rate-limit bindings in wrangler.toml), by one email per address per day, and
// by DAILY_CAP emails a day in all. Those caps and the used links are kept in
// the Ledger, one Durable Object whose calls run one at a time, so each check
// and its record happen together. It holds only HMAC fingerprints (of addresses
// for a day, of used links until they expire) and a count per day; addresses
// are never stored or logged by the Worker.

import { DurableObject } from "cloudflare:workers";

const SITE = "https://davalerio.com";
const SELF = "https://subscribe.davalerio.com";
const NAME = "Writing by Email";
const FROM = "David Valerio <writing@davalerio.com>";
const REPLY_TO = "david@davalerio.com";
const LINK_DAYS = 7;
const DAILY_CAP = 50;
const DAY = 86400;
const EMAIL = /^[^\s@<>"]{1,64}@[^\s@<>"]+\.[^\s@<>"]{2,}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "POST") return subscribe(request, env);
    if (url.pathname === "/confirm" && request.method === "GET") return confirmPage(url, env);
    if (url.pathname === "/confirm" && request.method === "POST") return confirm(request, env);
    return Response.redirect(SITE + "/", 302);
  },
};

// The posted form, or null when the request carries no form a browser would send.
async function formOf(request) {
  try {
    return await request.formData();
  } catch {
    return null;
  }
}

const NOT_A_FORM = () => page("Request Not Valid", "That request did not come from the sign-up form.", 400);

async function subscribe(request, env) {
  const form = await formOf(request);
  if (!form) return NOT_A_FORM();
  const email = String(form.get("email") || "").trim().toLowerCase();
  if (form.get("website")) return go("/subscribe/sent/");
  if (email.length > 254 || !EMAIL.test(email)) {
    return page("Address Not Valid", "That email address does not look complete. Go back and check it.", 400);
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const byIp = await env.LIMIT_IP.limit({ key: ip });
  const byEmail = await env.LIMIT_EMAIL.limit({ key: email });
  if (!byIp.success || !byEmail.success) {
    return page("Too Many Requests", "Too many confirmation emails have been asked for just now. Try again in a few minutes.", 429);
  }
  // One confirmation per address a day: a repeat lands on the same page as a
  // first request, so the form reveals nothing about who has asked.
  const sentKey = "sent:" + (await mac(`sent|${email}`, env));
  const allowed = await ledger(env).reserve(sentKey, new Date().toISOString().slice(0, 10), DAILY_CAP, DAY);
  if (allowed === "repeat") return go("/subscribe/sent/");
  if (allowed === "full") {
    return page("Sign-Ups Paused", "Sign-ups have reached today's limit. Try again tomorrow.", 429);
  }
  const link = `${SELF}/confirm?k=${await seal(email, env)}`;
  const res = await resend(env, "POST", "/emails", {
    from: FROM,
    reply_to: REPLY_TO,
    to: [email],
    subject: "Confirm Your Subscription",
    html: confirmationEmail(link),
    text: `Confirm your subscription to new writing by David Valerio:\n\n${link}\n\nThe link works for ${LINK_DAYS} days. If you did not ask for this, ignore this email and nothing will be sent.`,
  });
  if (!res.ok) {
    await ledger(env).release(sentKey);
    return page("Something Went Wrong", "The confirmation email could not be sent. Try again in a few minutes.", 502);
  }
  return go("/subscribe/sent/");
}

async function confirmPage(url, env) {
  const k = url.searchParams.get("k") || "";
  const link = await unseal(k, env);
  if (link.status !== "ok") return invalid(link.status);
  if (await ledger(env).has(await usedKey(link.bytes, env))) return alreadyUsed();
  return page("Confirm Your Subscription", `New writing will be sent to ${esc(link.email)}.`, 200,
    `<form class="subscribe" method="post" action="/confirm">
                <input type="hidden" name="k" value="${esc(k)}">
                <button type="submit">Confirm Subscription</button>
            </form>`);
}

// A link works once: it is claimed in the Ledger before the contact is touched,
// and pressed again it says so and changes nothing, so an old email cannot undo a
// later unsubscribe. A claim whose enrollment failed is released for a retry
// (enrolling again is harmless).
async function confirm(request, env) {
  const form = await formOf(request);
  if (!form) return NOT_A_FORM();
  const k = String(form.get("k") || "");
  const link = await unseal(k, env);
  if (link.status !== "ok") return invalid(link.status);
  const used = await usedKey(link.bytes, env);
  if (!(await ledger(env).claim(used, (LINK_DAYS + 1) * DAY))) return alreadyUsed();
  const result = await enroll(link.email, env);
  if (result === "elsewhere") {
    return page("Not Subscribed", `This address unsubscribed from all mail sent from davalerio.com, so it cannot be subscribed again here. Write to <a href="mailto:${REPLY_TO}">${REPLY_TO}</a> to be added.`, 409);
  }
  if (result !== "ok") {
    await ledger(env).release(used);
    return page("Something Went Wrong", "The subscription could not be saved. Try the link again in a few minutes.", 502);
  }
  return go("/subscribe/confirmed/");
}

function alreadyUsed() {
  return page("Link Already Used", `This confirmation link has already been used. To subscribe again, use the form on the <a href="${SITE}/">homepage</a>.`, 410);
}

// A used link's fingerprint, taken from its authenticated bytes.
async function usedKey(bytes, env) {
  return "used:" + (await mac(`used|${b64url(bytes)}`, env));
}

// The Ledger

function ledger(env) {
  return env.LEDGER.get(env.LEDGER.idFromName("ledger"));
}

// Each method runs alone and synchronously, so a check and its record cannot
// interleave with another request's.
export class Ledger extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS marks (key TEXT PRIMARY KEY, until INTEGER NOT NULL)");
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, sent INTEGER NOT NULL)");
  }

  #sweep() {
    this.ctx.storage.sql.exec("DELETE FROM marks WHERE until <= ?", Math.floor(Date.now() / 1000));
  }

  has(key) {
    this.#sweep();
    return this.ctx.storage.sql.exec("SELECT 1 FROM marks WHERE key = ?", key).toArray().length > 0;
  }

  // Records key for `seconds` and returns true, or false if it is already recorded.
  claim(key, seconds) {
    if (this.has(key)) return false;
    this.ctx.storage.sql.exec("INSERT INTO marks (key, until) VALUES (?, ?)", key, Math.floor(Date.now() / 1000) + seconds);
    return true;
  }

  release(key) {
    this.ctx.storage.sql.exec("DELETE FROM marks WHERE key = ?", key);
  }

  // One confirmation for this address today, within the day's cap: "ok", "repeat" or "full".
  reserve(key, day, cap, seconds) {
    if (this.has(key)) return "repeat";
    this.ctx.storage.sql.exec("DELETE FROM days WHERE day < ?", new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10));
    const row = this.ctx.storage.sql.exec("SELECT sent FROM days WHERE day = ?", day).toArray()[0];
    if ((row ? row.sent : 0) >= cap) return "full";
    this.claim(key, seconds);
    this.ctx.storage.sql.exec("INSERT INTO days (day, sent) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET sent = sent + 1", day);
    return "ok";
  }
}

// Adds the address to the Writing segment and makes sure it receives broadcasts:
// "ok", "fail", or "elsewhere". Resend's unsubscribe covers every segment in the
// account, so a contact who unsubscribed is subscribed again here (the confirm
// click being their own fresh request) only when Writing is the only segment
// they belong to; one in any other segment keeps the opt-out ("elsewhere").
// Success is reported only once Resend shows the contact subscribed and in the
// segment.
async function enroll(email, env) {
  return (await enrolled(email, env)) ?? "fail";
}

async function enrolled(email, env) {
  const id = encodeURIComponent(email);
  const found = await resend(env, "GET", `/contacts/${id}`);
  if (found.status === 404) {
    const created = await resend(env, "POST", "/contacts", {
      email, unsubscribed: false, segments: [{ id: env.SEGMENT_ID }],
    });
    if (!created.ok) return null;
  } else if (found.ok) {
    const contact = await found.json();
    if (contact.unsubscribed) {
      // The opt-out is cleared only once Writing is shown to be their one segment.
      const segments = await segmentsOf(id, env);
      if (!segments) return null;
      if (segments.some((s) => s !== env.SEGMENT_ID)) return "elsewhere";
      if (!segments.includes(env.SEGMENT_ID)) {
        const added = await resend(env, "POST", `/contacts/${id}/segments/${env.SEGMENT_ID}`);
        if (!added.ok) return null;
      }
      const now = await segmentsOf(id, env);
      if (!now) return null;
      if (now.length !== 1 || now[0] !== env.SEGMENT_ID) return "elsewhere";
      const updated = await resend(env, "PATCH", `/contacts/${id}`, { unsubscribed: false });
      if (!updated.ok) return null;
    }
    if (!(await inSegment(id, env))) {
      const added = await resend(env, "POST", `/contacts/${id}/segments/${env.SEGMENT_ID}`);
      if (!added.ok) return null;
    }
  } else {
    return null;
  }
  const after = await resend(env, "GET", `/contacts/${id}`);
  if (!after.ok || (await after.json()).unsubscribed) return null;
  return (await inSegment(id, env)) ? "ok" : null;
}

// The ids of the segments a contact belongs to, or null if Resend did not answer.
async function segmentsOf(id, env) {
  const res = await resend(env, "GET", `/contacts/${id}/segments?limit=100`);
  if (!res.ok) return null;
  const { data } = await res.json();
  return (data || []).map((s) => s.id);
}

async function inSegment(id, env) {
  return ((await segmentsOf(id, env)) || []).includes(env.SEGMENT_ID);
}

// Links and fingerprints

const enc = new TextEncoder();
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

// HMAC-SHA-256 of text under SIGNING_KEY: the store's fingerprint of an address or link.
async function mac(text, env) {
  const key = await crypto.subtle.importKey("raw", enc.encode(env.SIGNING_KEY),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64url(new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(text))));
}

async function sealKey(env) {
  const raw = await crypto.subtle.digest("SHA-256", enc.encode(`link|${env.SIGNING_KEY}`));
  return crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

// The link's token: the address and the time, encrypted and authenticated.
async function seal(email, env) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const body = enc.encode(`${Math.floor(Date.now() / 1000)}|${email}`);
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await sealKey(env), body));
  const out = new Uint8Array(iv.length + sealed.length);
  out.set(iv);
  out.set(sealed, iv.length);
  return b64url(out);
}

// { status: "ok", email, bytes } or { status: "expired" | "broken" }. Only the
// canonical spelling of a token is accepted.
async function unseal(k, env) {
  let text, bytes;
  try {
    bytes = unb64url(k);
    if (bytes.length < 29 || bytes.length > 400 || b64url(bytes) !== k) return { status: "broken" };
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.slice(0, 12) }, await sealKey(env), bytes.slice(12));
    text = new TextDecoder().decode(plain);
  } catch {
    return { status: "broken" };
  }
  const bar = text.indexOf("|");
  const t = text.slice(0, bar), email = text.slice(bar + 1);
  if (bar < 1 || !/^\d{1,12}$/.test(t) || !EMAIL.test(email)) return { status: "broken" };
  const age = Date.now() / 1000 - Number(t);
  return age >= -300 && age <= LINK_DAYS * DAY ? { status: "ok", email, bytes } : { status: "expired" };
}

// Resend

function resend(env, method, path, body) {
  return fetch("https://api.resend.com" + path, {
    method,
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
      "User-Agent": "davalerio-subscribe",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

// Responses

const PRIVATE = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };

function go(path) {
  return new Response(null, { status: 303, headers: { Location: SITE + path, ...PRIVATE } });
}

function invalid(why) {
  const text = why === "expired"
    ? `This confirmation link has expired. Subscribe again from the <a href="${SITE}/">homepage</a>.`
    : `This confirmation link is incomplete. Subscribe again from the <a href="${SITE}/">homepage</a>.`;
  return page(why === "expired" ? "Link Expired" : "Link Not Valid", text, 400);
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// A page in the site's own look: same masthead and stylesheet as the writing pages.
function page(title, text, status, extra = "") {
  return new Response(`<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
    <meta name="robots" content="noindex">
    <title>${title} — David Valerio</title>
    <link rel="icon" type="image/png" sizes="64x64" href="${SITE}/favicon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400;1,8..60,600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="${SITE}/writing/style.css">
</head>
<body>
    <header class="site">
        <a href="${SITE}/" class="home">David Valerio</a>
        <a href="${SITE}/#writing">Writing</a>
    </header>
    <main>
        <header class="post">
            <h1>${title}</h1>
        </header>
        <div class="body">
            <p>${text}</p>
            ${extra}
        </div>
        <footer class="post"><a href="${SITE}/">← Home</a></footer>
    </main>
</body>
</html>
`, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...PRIVATE } });
}

// The confirmation email, in the house email template (brand skill).
function confirmationEmail(link) {
  const font = "'Source Serif 4', Georgia, 'Times New Roman', serif";
  return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,wght@0,400;0,600;1,400&display=swap');</style>
</head>
<body style="margin: 0; background: #ffffff;">
<div style="max-width: 600px; margin: 0 auto; padding: 20px; font-family: ${font}; font-size: 15px; line-height: 1.66; color: #22282B;">
<h1 style="color: #22282B; font-size: 26px; font-weight: 600; line-height: 1.15; margin: 0 0 6px;">David Valerio</h1>
<p style="color: #6B7075; font-size: 12.5px; letter-spacing: 0.05em; margin: 0; padding-bottom: 16px; border-bottom: 1px solid #D8D4CC;">${NAME}</p>
<div style="background: #F3F1EC; border: 1px solid #E6E3DC; padding: 20px; margin: 20px 0;">
<p style="margin: 0 0 6px; font-size: 16px; font-weight: 600;">Confirm Your Subscription</p>
<p style="margin: 0 0 14px;">New writing will arrive by email.</p>
<p style="margin: 0;"><a href="${link}" style="color: #5E4B8B; text-decoration: none; font-weight: 600;">Confirm Subscription</a></p>
</div>
<p style="margin: 0; color: #6B7075; font-size: 12.5px;">The link works for ${LINK_DAYS} days. If you did not ask for this, ignore this email and nothing will be sent.</p>
</div>
</body></html>`;
}
