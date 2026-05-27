#!/usr/bin/env node
// Fetches session 240895 metadata + availability from ONEBOX and rewrites:
//   1. The match-info banner at the top of index.html
//   2. The grada <select> dropdown with real sectors

import fs   from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── 1. Load .env ──────────────────────────────────────────────
const env = Object.fromEntries(
  fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
    .split('\n')
    .filter(l => l.trim() && !l.startsWith('#'))
    .map(l => { const i = l.indexOf('='); return [l.slice(0,i).trim(), l.slice(i+1).trim()]; })
    .filter(([k]) => k)
);

const BASE_URL      = (env.ONEBOX_BASE_URL     || '').replace(/\/$/, '');
const CHANNEL_ID    =  env.ONEBOX_CHANNEL_ID   || '';
const CLIENT_ID     =  env.ONEBOX_CLIENT_ID    || '';
const CLIENT_SECRET =  env.ONEBOX_CLIENT_SECRET || '';
const SESSION_ID    = 240895;

if (!BASE_URL || !CHANNEL_ID || !CLIENT_ID || !CLIENT_SECRET) {
  console.error('Missing ONEBOX credentials in .env'); process.exit(1);
}

// ── 2. Authenticate ───────────────────────────────────────────
console.log(`Authenticating against ${BASE_URL}…`);
const authResp = await fetch(`${BASE_URL}/oauth/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ grant_type: 'client_credentials',
    channel_id: CHANNEL_ID, client_id: CLIENT_ID, client_secret: CLIENT_SECRET }),
});
if (!authResp.ok) { console.error(`Auth failed: HTTP ${authResp.status}`); process.exit(1); }
const { access_token: token } = await authResp.json();
console.log('  Token received.');

// ── 3. Fetch session metadata + availability in parallel ──────
console.log(`Fetching session ${SESSION_ID} data…`);
const headers = { Authorization: `Bearer ${token}` };

const [sessionsResp, availResp, pricesResp, sessionDetailResp] = await Promise.all([
  fetch(`${BASE_URL}/catalog-api/v1/sessions?limit=100`,                       { headers }),
  fetch(`${BASE_URL}/catalog-api/v1/sessions/${SESSION_ID}/availability`,     { headers }),
  fetch(`${BASE_URL}/catalog-api/v1/sessions/${SESSION_ID}/prices`,           { headers }),
  fetch(`${BASE_URL}/catalog-api/v1/sessions/${SESSION_ID}`,                  { headers }),
]);

if (!sessionsResp.ok) { console.error(`Sessions failed: HTTP ${sessionsResp.status}`);         process.exit(1); }
if (!availResp.ok)    { console.error(`Availability failed: HTTP ${availResp.status}`);       process.exit(1); }
if (!pricesResp.ok)   { console.error(`Prices failed: HTTP ${pricesResp.status}`);            process.exit(1); }
if (!sessionDetailResp.ok) { console.error(`Session detail failed: HTTP ${sessionDetailResp.status}`); process.exit(1); }

const sessionsBody     = await sessionsResp.json();
const availBody        = await availResp.json();
const pricesBody       = await pricesResp.json();
const sessionDetail    = await sessionDetailResp.json();

// Fetch the parent event for the richer localized title
const eventId = sessionDetail.event?.id;
let eventFull = {};
if (eventId) {
  const eventResp = await fetch(`${BASE_URL}/catalog-api/v1/events/${eventId}`, { headers });
  if (eventResp.ok) eventFull = await eventResp.json();
}

const isPlaceholderTitle = s => {
  if (!s) return true;
  const x = String(s).trim();
  return x.startsWith('*')
      || /\bAPI\b/i.test(x)
      || ['NUMERADO', 'EVENT', 'TEST', 'DEMO'].includes(x.toUpperCase());
};
const titleCandidates = [
  eventFull.texts?.title?.['es-ES'],
  eventFull.texts?.title?.['en-US'],
  eventFull.name,
  sessionDetail.event?.name,
  sessionDetail.name,
];
const realTitle = titleCandidates.find(c => c && !isPlaceholderTitle(c)) || null;

const session = (sessionsBody.data ?? []).find(s => s.id === SESSION_ID);
if (!session) { console.error(`Session ${SESSION_ID} not found in channel.`); process.exit(1); }

const sectors   = availBody.sectors ?? [];
const availData = availBody.availability ?? {};

// Build tier-name → total-price map (TOTAL = base + surcharges, what user actually pays)
const defaultRate = (pricesBody.rates ?? []).find(r => r.default) ?? pricesBody.rates?.[0];
const tierPriceByName = Object.fromEntries(
  (defaultRate?.price_types ?? []).map(pt => [pt.name, pt.price?.total ?? null])
);
console.log(`  Tier prices (${defaultRate?.name}):`, tierPriceByName);

console.log(`  Session: "${session.name}"  |  ${sectors.length} sectors  |  ${availData.available}/${availData.total} available`);

// ── 4. Build match-info banner HTML ───────────────────────────
const eventName  = session.event?.name ?? session.name;   // raw API name (still sent to Mailchimp)
const subtitle   = session.event?.texts?.subtitle?.['es-ES']
               ?? session.event?.texts?.subtitle?.['en-US']
               ?? '';
const dateStr    = new Date(session.date.start).toLocaleDateString('es-ES', {
  day: 'numeric', month: 'short', year: 'numeric' });
const timeStr    = new Date(session.date.start).toLocaleTimeString('es-ES', {
  hour: '2-digit', minute: '2-digit' });
const city       = session.venue?.location?.city ?? '';
const venueName  = session.venue?.name ?? '';
const venueLabel = city ? `${venueName}, ${city}` : venueName;
const priceMin   = session.price?.min?.value ?? 0;
const priceMax   = session.price?.max?.value ?? 0;
const priceLabel = priceMin === priceMax ? `${priceMin} €`
                 : `${priceMin} € – ${priceMax} €`;
const onSale     = session.on_sale;

// Display title: prefer the real event name from Onebox; derive only when Onebox returns placeholder
const derivedTitle = venueName
  ? `Partido en ${venueName} — ${dateStr}`
  : (eventName || 'Próximo partido');
const displayTitle = realTitle || derivedTitle;
console.log(`  Title: "${displayTitle}"  (real=${!!realTitle})`);
const total      = availData.total ?? 0;
const free       = availData.available ?? 0;
const pct        = total ? Math.round((free / total) * 100) : 0;
const availLabel = `${free.toLocaleString('es-ES')} / ${total.toLocaleString('es-ES')} (${pct}%)`;

const banner = `
    <section aria-label="Información del partido" class="match-banner">
      <style>
        .match-banner {
          background: transparent;
          padding: 1.25rem 1.5rem;
        }
        .match-card {
          background: #fff;
          border-radius: 12px;
          padding: 1.25rem 1.5rem;
          max-width: 640px;
          margin: 0 auto;
          box-shadow: 0 4px 32px rgba(13, 27, 42, 0.1);
        }
        .match-card-top {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 1rem;
          margin-bottom: 0.6rem;
        }
        .match-event-name {
          font-family: 'Barlow', system-ui, sans-serif;
          font-size: 1.1rem;
          font-weight: 700;
          color: #1a2a3a;
        }
        .match-subtitle {
          font-family: 'Barlow', system-ui, sans-serif;
          font-size: 0.8rem;
          font-weight: 500;
          color: #8fa3b8;
          margin-top: 0.15rem;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .match-on-sale {
          font-family: 'Barlow', system-ui, sans-serif;
          font-size: 0.7rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          border: 1.5px solid #2ea043;
          color: #2ea043;
          border-radius: 20px;
          padding: 0.25rem 0.75rem;
          white-space: nowrap;
        }
        .match-meta-row {
          display: flex;
          flex-wrap: wrap;
          gap: 1.5rem;
          margin-top: 0.75rem;
        }
        .match-meta-item {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          font-family: 'Barlow', system-ui, sans-serif;
          font-size: 0.875rem;
          color: #4a5e70;
        }
        .match-meta-item svg { color: #6cace4; flex-shrink: 0; }
        .match-avail-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          background: #f0f5fb;
          border-radius: 8px;
          padding: 0.6rem 1rem;
          margin-top: 1rem;
          font-family: 'Barlow', system-ui, sans-serif;
          font-size: 0.875rem;
        }
        .match-avail-label { color: #4a5e70; }
        .match-avail-value { font-weight: 700; color: #6cace4; }
        @media (max-width: 480px) {
          .match-meta-row { gap: 0.75rem; }
          .match-card-top { flex-direction: column; }
        }
      </style>
      <div class="match-card">
        <div class="match-card-top">
          <div>
            <div class="match-event-name" id="m-name">${displayTitle}</div>
            <div class="match-subtitle" id="m-subtitle"${subtitle ? '' : ' hidden'}>${subtitle}</div>
          </div>
          <div class="match-on-sale" id="m-sale"${onSale ? '' : ' hidden'}>On sale</div>
        </div>
        <div class="match-meta-row">
          <span class="match-meta-item">
            <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>
              <line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
            </svg>
            <span id="m-date">${dateStr}, ${timeStr}</span>
          </span>
          <span class="match-meta-item">
            <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path d="M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
            </svg>
            <span id="m-venue">${venueLabel}</span>
          </span>
          <span class="match-meta-item">
            <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 7H10a4 4 0 0 0 0 8h4a4 4 0 0 1 0 8H6"/>
              <line x1="7" y1="7" x2="17" y2="7"/>
              <line x1="7" y1="11" x2="13" y2="11"/>
            </svg>
            <span id="m-price">${priceLabel}</span>
          </span>
        </div>
        <div class="match-avail-row">
          <span class="match-avail-label">Disponibilidad</span>
          <span class="match-avail-value" id="m-avail">${availLabel}</span>
        </div>
      </div>
    </section>`;

// ── 5. Rewrite index.html ───────────────────────────────────
const landingPath = path.join(__dirname, 'index.html');
let html = fs.readFileSync(landingPath, 'utf8');

// Replace match banner placeholder
let updated = html.replace(
  /<!-- MATCH_INFO_START -->[\s\S]*?<!-- MATCH_INFO_END -->/,
  `<!-- MATCH_INFO_START -->${banner}    <!-- MATCH_INFO_END -->`
);

// Populate hidden event fields
updated = updated.replace(
  /(<input\s+type="hidden"\s+name="event_id"\s+value=")[^"]*(")/,
  `$1${SESSION_ID}$2`
);
updated = updated.replace(
  /(<input\s+type="hidden"\s+name="event_name"\s+value=")[^"]*(")/,
  `$1${eventName.replace(/"/g, '&quot;')}$2`
);

// Replace grada <select> options
const defaultOpt    = '              <option value="" disabled selected>Selecciona una grada</option>';
const sectorOptions = sectors.map(s => {
  const tierName = s.price_types?.[0]?.name ?? '';
  const price    = tierPriceByName[tierName] ?? null;
  const priceAttr = price !== null ? ` data-price="${price}"` : '';
  const priceLabel = price !== null ? ` — ${price}€` : '';
  return `              <option value="${s.id}"${priceAttr}>${s.name}${priceLabel}</option>`;
}).join('\n');

updated = updated.replace(
  /(<select\s+id="grada"[^>]*>)([\s\S]*?)(<\/select>)/,
  `$1\n${defaultOpt}\n${sectorOptions}\n            $3`
);

if (updated === html) {
  console.error('No changes made — check placeholders exist in index.html.');
  process.exit(1);
}

fs.writeFileSync(landingPath, updated, 'utf8');

// ── 6. Done ───────────────────────────────────────────────────
console.log(`\nDone — banner injected, event_id=${SESSION_ID}, event_name="${eventName}", ${sectors.length} sectors baked in.`);
