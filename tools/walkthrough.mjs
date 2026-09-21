/**
 * Records a screencast walkthrough of the whole application.
 *
 * The app must already be running on http://127.0.0.1:5000 with seeded data:
 *
 *     flask --app wsgi seed --reset
 *     python wsgi.py
 *
 * Then:  node tools/walkthrough.mjs
 *
 * Needs puppeteer and ffmpeg, both development-only. There is no audio, so
 * each step is captioned on screen and a fake cursor is drawn so clicks are
 * possible to follow.
 */
import puppeteer from 'puppeteer';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5000';
const OUT = process.env.OUT || '/tmp/walkthrough.webm';
const W = 1440, H = 900;

const wait = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- overlay
const OVERLAY_CSS = `
#wt-caption{position:fixed;left:28px;bottom:28px;z-index:2147483000;
  max-width:620px;background:rgba(15,30,38,.93);color:#fff;padding:14px 18px;
  border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.3);
  font:400 15px/1.5 "Segoe UI",Roboto,-apple-system,Helvetica,Arial,sans-serif;
  opacity:0;transition:opacity .35s ease;pointer-events:none}
#wt-caption.on{opacity:1}
#wt-caption b{display:block;font-size:12px;letter-spacing:.09em;text-transform:uppercase;
  color:#6fd8c0;margin-bottom:4px;font-weight:700}
#wt-cursor{position:fixed;z-index:2147483001;width:22px;height:22px;margin:-11px 0 0 -11px;
  border-radius:50%;background:rgba(18,161,134,.35);border:2px solid #12a186;
  pointer-events:none;transition:left .5s cubic-bezier(.4,0,.2,1),top .5s cubic-bezier(.4,0,.2,1),
  transform .12s ease;left:-100px;top:-100px}
#wt-cursor.click{transform:scale(.55);background:rgba(18,161,134,.7)}
#wt-title{position:fixed;inset:0;z-index:2147483002;background:linear-gradient(160deg,#0a6a5a,#12a186 60%,#17b89a);
  color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;
  font-family:"Segoe UI",Roboto,-apple-system,Helvetica,Arial,sans-serif;
  opacity:0;transition:opacity .6s ease;pointer-events:none}
#wt-title.on{opacity:1}
#wt-title h1{font-size:52px;margin:0 0 12px;letter-spacing:-.03em;font-weight:700}
#wt-title p{font-size:20px;margin:0;opacity:.9}
#wt-title .tag{margin-top:26px;font-size:14px;letter-spacing:.1em;text-transform:uppercase;opacity:.75}
`;

async function installOverlay(page) {
  await page.evaluate((css) => {
    if (document.getElementById('wt-style')) return;
    const st = document.createElement('style');
    st.id = 'wt-style'; st.textContent = css;
    document.head.appendChild(st);
    for (const [id, tag] of [['wt-caption', 'div'], ['wt-cursor', 'div'], ['wt-title', 'div']]) {
      const el = document.createElement(tag); el.id = id;
      document.body.appendChild(el);
    }
    document.getElementById('wt-title').innerHTML =
      '<h1>MediQueue</h1><p>AI Powered Hospital Appointment &amp; Queue Management</p>' +
      '<div class="tag">Feature walkthrough</div>';
  }, OVERLAY_CSS);
}

const caption = (page, kicker, text) =>
  page.evaluate((k, t) => {
    const el = document.getElementById('wt-caption');
    if (!el) return;
    el.innerHTML = '<b>' + k + '</b>' + t;
    el.classList.add('on');
  }, kicker, text).catch(() => {});

const hideCaption = page =>
  page.evaluate(() => document.getElementById('wt-caption')?.classList.remove('on')).catch(() => {});

async function showTitle(page, on) {
  await page.evaluate(v => document.getElementById('wt-title')?.classList.toggle('on', v), on)
           .catch(() => {});
}

// --------------------------------------------------------------- cursor
async function cursorTo(page, selector) {
  const box = await page.evaluate(sel => {
    const el = document.querySelector(sel);
    if (!el) return null;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, selector);
  if (!box) return null;
  await wait(450);
  const fresh = await page.evaluate(sel => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, selector);
  const pt = fresh || box;
  await page.evaluate(p => {
    const c = document.getElementById('wt-cursor');
    if (c) { c.style.left = p.x + 'px'; c.style.top = p.y + 'px'; }
  }, pt);
  await wait(550);
  return pt;
}

async function click(page, selector, { settle = 900, nav = false } = {}) {
  await page.waitForSelector(selector, { timeout: 8000 });
  await cursorTo(page, selector);
  await page.evaluate(() => document.getElementById('wt-cursor')?.classList.add('click'));
  await wait(140);
  await page.evaluate(() => document.getElementById('wt-cursor')?.classList.remove('click'));
  if (nav) {
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => {}),
      page.click(selector),
    ]);
    await installOverlay(page);
  } else {
    await page.click(selector);
  }
  await wait(settle);
}

async function typeInto(page, selector, text, delay = 38) {
  await page.waitForSelector(selector, { timeout: 8000 });
  await cursorTo(page, selector);
  await page.click(selector);
  await page.type(selector, text, { delay });
  await wait(500);
}

async function goto(page, path, settle = 1100) {
  await page.goto(BASE + path, { waitUntil: 'networkidle2' });
  await installOverlay(page);
  await wait(settle);
}

async function scrollTo(page, y, ms = 1400) {
  await page.evaluate(v => window.scrollTo({ top: v, behavior: 'smooth' }), y);
  await wait(ms);
}

// ------------------------------------------------------------------ scenes
const scenes = [];
const scene = (name, fn) => scenes.push({ name, fn });

scene('title', async (page) => {
  await goto(page, '/');
  await showTitle(page, true);
  await wait(2600);
  await showTitle(page, false);
  await wait(700);
});

scene('home', async (page) => {
  await caption(page, 'Public website', 'The home page: symptom checker, live figures, departments and top-rated doctors.');
  await wait(2600);
  await scrollTo(page, 620);
  await caption(page, 'Public website', 'Four steps from symptom to consultation.');
  await wait(2400);
  await scrollTo(page, 1250);
  await caption(page, 'Public website', '13 departments, each with its own doctors.');
  await wait(2400);
  await scrollTo(page, 0, 900);
  await hideCaption(page);
});

scene('chatbot-normal', async (page) => {
  await goto(page, '/chat/');
  await caption(page, 'AI assistant', 'The triage engine runs offline. No API key, no internet.');
  await wait(2400);
  await typeInto(page, '#input', "I've had a throbbing headache for three days and light hurts my eyes");
  await caption(page, 'AI assistant', 'Symptoms in plain language, the way a patient would type them.');
  await wait(900);
  await click(page, '#send', { settle: 2600 });
  await caption(page, 'AI assistant', 'It suggests a department, and shows the confidence and the words that led to it.');
  await wait(4200);
  await scrollTo(page, 260, 1200);
  await caption(page, 'AI assistant', 'Runner-up departments, and the doctors who can see you soonest.');
  await wait(3800);
  await scrollTo(page, 0, 800);
});

scene('chatbot-redflag', async (page) => {
  await caption(page, 'AI assistant', 'Now an emergency presentation.');
  await wait(1800);
  await typeInto(page, '#input', 'chest pain radiating to my left arm with cold sweat', 34);
  await click(page, '#send', { settle: 2600 });
  await caption(page, 'Safety', 'Red-flag rules override the model completely and show emergency advice.');
  await wait(4600);
});

scene('login-patient', async (page) => {
  await goto(page, '/auth/login');
  await caption(page, 'Sign in', 'Three roles share one login: admin, doctor and patient.');
  await wait(2200);
  await typeInto(page, '#email', 'ravi.kumar@example.com', 28);
  await typeInto(page, '#password', 'patient123', 40);
  await click(page, 'button[type=submit]', { nav: true, settle: 1600 });
  await caption(page, 'Patient dashboard', 'Upcoming appointments, recent visits and anything awaiting a rating.');
  await wait(3400);
});

scene('booking', async (page) => {
  await goto(page, '/patient/book');
  await caption(page, 'Booking', 'Booking can start from symptoms rather than from a department.');
  await wait(2400);
  await typeInto(page, 'textarea[name=symptoms]', 'severe lower back pain that spreads down my left leg', 30);
  await click(page, 'button[type=submit]', { nav: true, settle: 2200 });
  await caption(page, 'Booking', 'The same engine picks the department and ranks the doctors.');
  await wait(3600);
  const chosen = await page.$('a[href*="/patient/book/doctor/"]');
  if (chosen) {
    await click(page, 'a[href*="/patient/book/doctor/"]', { nav: true, settle: 1800 });
  }
  await caption(page, 'Booking', 'Free-slot counts for the next 14 days, generated from the weekly rota minus leave.');
  await wait(3200);
  const slot = await page.$('.slot');
  if (slot) {
    await click(page, '.slot', { settle: 1400 });
    await caption(page, 'Booking', 'Confirming issues a token number for that clinic.');
    await wait(1800);
    await click(page, '#slotform button[type=submit]', { nav: true, settle: 2400 });
  }
  await caption(page, 'Booking', 'Booked. The token number is what places you in the queue.');
  await wait(3600);
});

scene('queue-patient', async (page) => {
  await goto(page, '/patient/appointments');
  await caption(page, 'Live queue', "Today's appointment has a live queue link.");
  await wait(2600);
  const live = await page.$('a[href*="/queue"]');
  if (live) {
    await click(page, 'a[href*="/queue"]', { nav: true, settle: 2000 });
    await caption(page, 'Live queue', 'Your token, who is being seen now, and an estimated wait. Refreshes every 5 seconds.');
    await wait(4200);
    await scrollTo(page, 400, 1200);
    await wait(2600);
    await scrollTo(page, 0, 800);
  }
});

scene('doctor-queue', async (page) => {
  await goto(page, '/auth/logout', 700);
  await goto(page, '/auth/login');
  await caption(page, 'Doctor', 'Signing in as a doctor whose clinic is already running.');
  await wait(1800);
  await typeInto(page, '#email', 'arun.mehta@mediqueue.local', 26);
  await typeInto(page, '#password', 'doctor123', 40);
  await click(page, 'button[type=submit]', { nav: true, settle: 2000 });
  await caption(page, 'Queue console', 'Now serving, waiting, completed, and the average consultation time so far today.');
  await wait(4000);
  await scrollTo(page, 420, 1300);
  await caption(page, 'Queue console', 'Every token, with the symptoms the patient described and what the AI suggested.');
  await wait(3800);
});

scene('consultation', async (page) => {
  // Open the consultation that is already in progress. Picking the first
  // consult link on the page is wrong: completed tokens link to a read-only
  // view of their notes, which has no submit button.
  const active = await page.$('.queue-row.now a[href*="/doctor/consult/"]');
  if (active) {
    await caption(page, 'Consultation', 'Opening the patient currently in the room.');
    await wait(1800);
    await click(page, '.queue-row.now a[href*="/doctor/consult/"]', { nav: true, settle: 2200 });
  }

  await caption(page, 'Consultation', 'Allergies and ongoing conditions are shown without the doctor going looking.');
  await wait(3800);
  await scrollTo(page, 300, 1200);
  await caption(page, 'Consultation', 'Previous visits for this patient are on the right.');
  await wait(2600);
  await scrollTo(page, 0, 900);

  if (await page.$('#diagnosis:not([disabled])')) {
    await typeInto(page, '#diagnosis', 'Gastro-oesophageal reflux', 32);
    await typeInto(page, '#notes', 'Symptoms worse lying flat and after meals. No alarm features.', 20);
    await caption(page, 'Prescribing', 'Medicines are separate fields, so the prescription prints as a real table.');
    await wait(1500);
    await typeInto(page, 'input[name=drug]', 'Pantoprazole 40mg', 28);
    await typeInto(page, 'input[name=dosage]', '1 tablet', 28);
    await typeInto(page, 'input[name=frequency]', 'Once daily', 28);
    await typeInto(page, 'input[name=duration]', '14 days', 28);
    await typeInto(page, 'input[name=instructions]', '30 minutes before breakfast', 24);
    await wait(800);
    await caption(page, 'Prescribing', 'Completing closes the token and moves the queue on.');
    await wait(1600);
    await click(page, 'button.btn.lg[type=submit]', { nav: true, settle: 2600 });
    await caption(page, 'Queue console', 'Token closed. The average consultation time updates and the patient is notified.');
    await wait(3200);
  }

  // With the room free, Call Next becomes available.
  const callable = await page.$('form[action*="call-next"] button:not([disabled])');
  if (callable) {
    await caption(page, 'Queue console', 'Call Next takes the lowest waiting token, preferring patients who have checked in.');
    await wait(2400);
    await click(page, 'form[action*="call-next"] button:not([disabled])', { nav: true, settle: 2400 });
    await caption(page, 'Queue console', 'The next patient is called straight into the consultation screen.');
    await wait(3000);
  }
});

scene('prescription', async (page) => {
  await goto(page, '/auth/logout', 700);
  await goto(page, '/auth/login');
  await typeInto(page, '#email', 'ravi.kumar@example.com', 24);
  await typeInto(page, '#password', 'patient123', 38);
  await click(page, 'button[type=submit]', { nav: true, settle: 1600 });
  await goto(page, '/patient/appointments');
  await caption(page, 'After the visit', 'Completed visits carry a diagnosis and a prescription.');
  await wait(2600);
  const rx = await page.$('a[href*="/patient/prescription/"]');
  if (rx) {
    await click(page, 'a[href*="/patient/prescription/"]', { nav: true, settle: 2000 });
    await caption(page, 'Prescription', 'Laid out for printing, with the medicines as a table.');
    await wait(4200);
  }
});

scene('rating', async (page) => {
  await goto(page, '/patient/');
  const rate = await page.$('a[href*="/patient/appointments/"]');
  if (rate) {
    await click(page, 'a[href*="/patient/appointments/"]', { nav: true, settle: 1800 });
    if (await page.$('#stars')) {
      await caption(page, 'Feedback', 'Rating a completed visit feeds the doctor ranking the AI uses.');
      await cursorTo(page, '#stars');
      await page.select('#stars', '5');
      await wait(1200);
      await typeInto(page, '#comment', 'Explained everything clearly and did not rush.', 24);
      await click(page, 'form[action*="/rate"] button[type=submit]', { nav: true, settle: 2400 });
      await wait(2200);
    }
  }
});

scene('admin', async (page) => {
  await goto(page, '/auth/logout', 700);
  await goto(page, '/auth/login');
  await caption(page, 'Administrator', 'The third role: staff, schedules and reporting.');
  await wait(1600);
  await typeInto(page, '#email', 'admin@mediqueue.local', 24);
  await typeInto(page, '#password', 'admin123', 38);
  await click(page, 'button[type=submit]', { nav: true, settle: 2200 });
  await caption(page, 'Admin dashboard', 'Four weeks of seeded history, so the charts have something real in them.');
  await wait(3800);
  await scrollTo(page, 500, 1400);
  await caption(page, 'Admin dashboard', 'Live clinics, no-show rate, average consultation time, busiest doctors.');
  await wait(3600);
  await scrollTo(page, 0, 800);
});

scene('admin-reports', async (page) => {
  await goto(page, '/admin/reports');
  await caption(page, 'Reports', '30-day volume, outcomes, AI usage and patient satisfaction.');
  await wait(3400);
  await scrollTo(page, 520, 1500);
  await wait(3000);
  await scrollTo(page, 1150, 1500);
  await caption(page, 'Reports', 'How often the assistant was used, and which departments it routed to.');
  await wait(3400);
});

scene('admin-schedule', async (page) => {
  await goto(page, '/admin/doctors');
  await caption(page, 'Staff management', 'Doctors are added here and deactivated rather than deleted, so history survives.');
  await wait(3200);
  const sched = await page.$('a[href*="/schedule"]');
  if (sched) {
    await click(page, 'a[href*="/schedule"]', { nav: true, settle: 2000 });
    await caption(page, 'Schedules', 'Weekly sittings and leave. Recording leave notifies everyone already booked.');
    await wait(4000);
  }
});

scene('knowledge-base', async (page) => {
  await goto(page, '/admin/knowledge-base');
  await caption(page, 'Triage rules', 'The half of the AI a human can edit directly. No retraining needed.');
  await wait(3600);
  await scrollTo(page, 380, 1300);
  await caption(page, 'Triage rules', 'Red-flag rules override the classifier outright.');
  await wait(3200);
  await scrollTo(page, 0, 800);
});

scene('docs', async (page) => {
  await goto(page, '/docs/');
  await caption(page, 'Documentation', 'The project documentation is served by the app itself.');
  await wait(3000);
  await goto(page, '/docs/05-ai-engine');
  await caption(page, 'Documentation', 'Architecture, database design and how the AI works, with diagrams.');
  await wait(3000);
  await scrollTo(page, 700, 1600);
  await wait(3000);
  await scrollTo(page, 1500, 1600);
  await wait(2800);
});

scene('outro', async (page) => {
  await goto(page, '/');
  await hideCaption(page);
  await page.evaluate(() => {
    const t = document.getElementById('wt-title');
    if (t) t.innerHTML =
      '<h1>MediQueue</h1><p>Flask · SQLite · scikit-learn · 191 tests</p>' +
      '<div class="tag">Runs entirely offline</div>';
  });
  await showTitle(page, true);
  await wait(3200);
});

// -------------------------------------------------------------------- run
const browser = await puppeteer.launch({
  headless: true,
  args: ['--no-sandbox', `--window-size=${W},${H}`, '--force-device-scale-factor=1',
         '--hide-scrollbars', '--disable-gpu'],
});
const page = await browser.newPage();
await page.setViewport({ width: W, height: H });
page.setDefaultTimeout(15000);

await page.goto(BASE + '/', { waitUntil: 'networkidle2' });
await installOverlay(page);

// node tools/walkthrough.mjs                    -> everything
// node tools/walkthrough.mjs doctor-queue,consultation  -> just those, in order
const only = process.argv[2];
const wanted = only ? only.split(',').map(x => x.trim()) : null;
const list = wanted ? scenes.filter(s => wanted.includes(s.name)) : scenes;

const rec = await page.screencast({ path: OUT, fps: 20 });
const started = Date.now();
for (const s of list) {
  const t0 = Date.now();
  try {
    await s.fn(page);
    console.log(`  ok    ${s.name.padEnd(18)} ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  } catch (e) {
    console.log(`  FAIL  ${s.name.padEnd(18)} ${String(e.message).split('\n')[0].slice(0, 80)}`);
  }
}
await rec.stop();
await browser.close();
console.log(`\n  total ${((Date.now() - started) / 1000).toFixed(0)}s -> ${OUT}`);
