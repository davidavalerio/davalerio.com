// Signup service for Writing and Maps by Email on davalerio.com.
//
//   POST /          the site's form: emails a signed confirmation link,
//                   then sends the reader to /subscribe/sent/ on the site
//   GET  /confirm   shows a Confirm Subscription button (a mail scanner
//                   that follows the link enrolls nobody)
//   POST /confirm   adds the address to the Writing segment in Resend,
//                   then sends the reader to /subscribe/confirmed/
//
// Nothing is stored before confirmation: the link carries the address, the
// time it was issued and an HMAC of both under SIGNING_KEY, and it expires
// after LINK_DAYS. The hidden "website" field is a bot trap, and LIMIT_IP and
// LIMIT_EMAIL (rate-limit bindings in wrangler.toml) cap confirmation mail.
// Addresses are never logged.

const SITE = "https://davalerio.com";
const SELF = "https://subscribe.davalerio.com";
const NAME = "Writing and Maps by Email";
const FROM = "David Valerio <writing@davalerio.com>";
const REPLY_TO = "david@davalerio.com";
const LINK_DAYS = 7;
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

async function subscribe(request, env) {
  const form = await request.formData();
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
  const t = String(Math.floor(Date.now() / 1000));
  const link = `${SELF}/confirm?e=${encodeURIComponent(email)}&t=${t}&s=${await sign(email, t, env)}`;
  const res = await resend(env, "POST", "/emails", {
    from: FROM,
    reply_to: REPLY_TO,
    to: [email],
    subject: "Confirm Your Subscription",
    html: confirmationEmail(link),
    text: `Confirm your subscription to new writing and maps by David Valerio:\n\n${link}\n\nThe link works for ${LINK_DAYS} days. If you did not ask for this, ignore this email and nothing will be sent.`,
  });
  if (!res.ok) return page("Something Went Wrong", "The confirmation email could not be sent. Try again in a few minutes.", 502);
  return go("/subscribe/sent/");
}

async function confirmPage(url, env) {
  const email = url.searchParams.get("e") || "";
  const t = url.searchParams.get("t") || "";
  const s = url.searchParams.get("s") || "";
  const check = await valid(email, t, s, env);
  if (check !== "ok") return invalid(check);
  return page("Confirm Your Subscription", `New writing and maps will be sent to ${esc(email)}.`, 200,
    `<form class="subscribe" method="post" action="/confirm">
                <input type="hidden" name="e" value="${esc(email)}">
                <input type="hidden" name="t" value="${esc(t)}">
                <input type="hidden" name="s" value="${esc(s)}">
                <button type="submit">Confirm Subscription</button>
            </form>`);
}

async function confirm(request, env) {
  const form = await request.formData();
  const email = String(form.get("e") || "");
  const check = await valid(email, String(form.get("t") || ""), String(form.get("s") || ""), env);
  if (check !== "ok") return invalid(check);
  if (!(await enroll(email, env))) {
    return page("Something Went Wrong", "The subscription could not be saved. Try the link again in a few minutes.", 502);
  }
  return go("/subscribe/confirmed/");
}

// Adds the address to the Writing segment and makes sure it receives broadcasts.
// The confirm click is the reader's own explicit request, so a contact who once
// unsubscribed is subscribed again only here. Success is reported only once
// Resend shows the contact subscribed and in the segment.
async function enroll(email, env) {
  const id = encodeURIComponent(email);
  const found = await resend(env, "GET", `/contacts/${id}`);
  if (found.status === 404) {
    const created = await resend(env, "POST", "/contacts", {
      email, unsubscribed: false, segments: [{ id: env.SEGMENT_ID }],
    });
    if (!created.ok) return false;
  } else if (found.ok) {
    const contact = await found.json();
    if (contact.unsubscribed) {
      const updated = await resend(env, "PATCH", `/contacts/${id}`, { unsubscribed: false });
      if (!updated.ok) return false;
    }
    if (!(await inSegment(id, env))) {
      const added = await resend(env, "POST", `/contacts/${id}/segments/${env.SEGMENT_ID}`);
      if (!added.ok) return false;
    }
  } else {
    return false;
  }
  const after = await resend(env, "GET", `/contacts/${id}`);
  if (!after.ok || (await after.json()).unsubscribed) return false;
  return inSegment(id, env);
}

async function inSegment(id, env) {
  const res = await resend(env, "GET", `/contacts/${id}/segments?limit=100`);
  if (!res.ok) return false;
  const { data } = await res.json();
  return (data || []).some((s) => s.id === env.SEGMENT_ID);
}

// Signing

async function sign(email, t, env) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SIGNING_KEY),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${email}|${t}`));
  return btoa(String.fromCharCode(...new Uint8Array(mac))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// "ok", "expired" or "broken"
async function valid(email, t, s, env) {
  if (!email || !s || !/^\d{1,12}$/.test(t) || !EMAIL.test(email)) return "broken";
  const expected = await sign(email, t, env);
  if (expected.length !== s.length) return "broken";
  let diff = 0;
  for (let i = 0; i < s.length; i++) diff |= expected.charCodeAt(i) ^ s.charCodeAt(i);
  if (diff !== 0) return "broken";
  const age = Date.now() / 1000 - Number(t);
  return age >= -300 && age <= LINK_DAYS * 86400 ? "ok" : "expired";
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
<p style="margin: 0 0 6px; font-size: 16px; font-weight: 600;">Confirm your subscription</p>
<p style="margin: 0 0 14px; color: #6B7075;">New writing and maps will arrive by email.</p>
<p style="margin: 0;"><a href="${link}" style="color: #5E4B8B; text-decoration: none; font-weight: 600;">Confirm Subscription</a></p>
</div>
<p style="margin: 0; color: #6B7075; font-size: 13px;">The link works for ${LINK_DAYS} days. If you did not ask for this, ignore this email and nothing will be sent.</p>
</div>
</body></html>`;
}
