// Run against backend/tools/round6_acceptance_server.py, never the ordinary server.
// node frontend/tools/round12-scenes-browser.mjs [http://127.0.0.1:18746]
// Round 12: the library, corridor and office as scenes, and moving between all four places.
// Round 13: those three scenes have picture backdrops; markers are pinned to objects in the picture and are
// checked for staying on them at several window shapes, including very wide and nearly square ones.
// Round 12: the other places are scenes too; the two checks that expected the classic view there were updated.
// Round 11: rewritten for the immersive coffee-corner scene (scene first, rail + overlay panels, settings).
// Optional: PLAYWRIGHT_MODULE_PATH points to an already installed playwright package.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const origin = process.argv[2] ?? 'http://127.0.0.1:18746';
assert.equal(new URL(origin).hostname, '127.0.0.1', 'The browser checks only support the isolated loopback server');
const reportDir = path.join(root, 'output/playwright', `round12-scenes-${new Date().toISOString().replace(/[:.]/g, '-')}`);
mkdirSync(reportDir, { recursive: true });
const require = createRequire(import.meta.url);
let playwright;
const candidates = [process.env.PLAYWRIGHT_MODULE_PATH, 'playwright'];
const npmCache = path.join(process.env.LOCALAPPDATA ?? '', 'npm-cache/_npx');
if (existsSync(npmCache)) for (const entry of readdirSync(npmCache)) candidates.push(path.join(npmCache, entry, 'node_modules/playwright'));
for (const candidate of candidates.filter(Boolean)) {
  try { playwright = require(candidate); break; } catch { /* Try the next existing installation. */ }
}
if (!playwright) throw new Error('No installed Playwright found. Set PLAYWRIGHT_MODULE_PATH; this script never installs packages automatically.');
const info = await fetch(`${origin}/api/__test/info`).then(r => r.json());
assert.equal(info.mock_only, true);
assert.equal(info.live_transport_disabled, true);
assert.equal(info.dotenv_disabled, true);
let executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
if (!executablePath && !existsSync(playwright.chromium.executablePath())) {
  const cache = path.join(process.env.LOCALAPPDATA ?? '', 'ms-playwright');
  if (existsSync(cache)) for (const entry of readdirSync(cache).filter(x => x.startsWith('chromium-')).reverse()) {
    const candidate = path.join(cache, entry, 'chrome-win64/chrome.exe');
    if (existsSync(candidate)) { executablePath = candidate; break; }
  }
}
const browser = await playwright.chromium.launch({ executablePath, headless: process.env.CCQ_HEADED !== '1' });
const checks = [], pageErrors = [], overflow = [], measured = {};
function ok(name, condition = true) { assert.ok(condition, name); checks.push(name); console.log(`PASS ${name}`); }
// WCAG contrast of an element's text. Translucent backgrounds are composited; where a panel lets the
// picture through, the worst case is taken (picture pure white or pure black behind it).
const contrastOf = (locator) => locator.evaluate((el) => {
  const rgb = (c) => { const n = (c.match(/[\d.]+/g) || []).map(Number); return n.length === 3 ? [...n, 1] : n; };
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const layers = [];
  for (let node = el; node; node = node.parentElement) {
    const c = rgb(getComputedStyle(node).backgroundColor);
    if (c[3] > 0) layers.push(c);
    if (c[3] >= 0.999 || node.classList.contains('stage') || node.classList.contains('sidebar')) break;   // behind these is the picture
  }
  const over = (base) => layers.reduceRight((acc, [r, g, b, a]) => [r * a + acc[0] * (1 - a), g * a + acc[1] * (1 - a), b * a + acc[2] * (1 - a)], base);
  const opaque = layers.length && layers[layers.length - 1][3] >= 0.999;
  const fg = rgb(getComputedStyle(el).color).slice(0, 3);
  const ratio = (bg) => { const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x); return (a + 0.05) / (b + 0.05); };
  return opaque ? ratio(over([255, 255, 255])) : Math.min(ratio(over([255, 255, 255])), ratio(over([0, 0, 0])));
});
const box = (locator) => locator.boundingBox();
const inside = (b, w, h) => b && b.x >= 0 && b.y >= 0 && b.x + b.width <= w + 1 && b.y + b.height <= h + 1;
const apart = (a, b) => a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;

// Where each thing is in its picture (fractions of 1672×941), written here independently of the app code.
const PINS = { library: { laptop: [385 / 1672, 662 / 941], syllabus: [603 / 1672, 655 / 941], phone: [752 / 1672, 664 / 941] }, corridor: { noticeboard: [285 / 1672, 300 / 941] }, office: { office_door: [1330 / 1672, 400 / 941] } };
const pictureReady = async (page) => {
  await page.waitForFunction(() => { const imgs = [...document.querySelectorAll('.stage img')]; return imgs.length > 0 && imgs.every(i => i.complete && i.naturalWidth > 0); });
  await page.evaluate(() => Promise.all([...document.querySelectorAll('.stage img')].map(i => i.decode())));   // painted, not just downloaded
};
// The picture covers the window without stretching, and every dot is on its pin in the picture (within 1.5 px).
const pictureCheck = (page, id) => page.evaluate(([pins]) => {
  const img = document.querySelector('.canvas-back .canvas-img'), st = document.querySelector('.stage').getBoundingClientRect(), r = img.getBoundingClientRect();
  const out = { natural: `${img.naturalWidth}x${img.naturalHeight}`, ratioOff: Math.abs(r.width / r.height - img.naturalWidth / img.naturalHeight), covers: r.left <= st.left + 0.5 && r.top <= st.top + 0.5 && r.right >= st.right - 0.5 && r.bottom >= st.bottom - 0.5,
    tight: Math.abs(r.width - st.width) < 1 || Math.abs(r.height - st.height) < 1, drift: 0, shown: Math.round(100 * Math.min(1, st.width / r.width) * Math.min(1, st.height / r.height)) };
  for (const [spot, [fx, fy]] of Object.entries(pins)) {
    const d = document.querySelector(`[data-spot="${spot}"] .spot-dot`).getBoundingClientRect();
    out.drift = Math.max(out.drift, Math.abs(d.left + d.width / 2 - (r.left + r.width * fx)), Math.abs(d.top + d.height / 2 - (r.top + r.height * fy)));
  }
  return out;
}, [PINS[id]]);

for (const [w, h] of [[1366, 768], [1920, 1080], [1280, 720]]) {
  const context = await browser.newContext({ viewport: { width: w, height: h }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  const sid = () => page.evaluate(() => localStorage.getItem('ccq_session_id'));
  const state = async () => page.request.get(`/api/sessions/${await sid()}`).then(r => r.json());
  const shot = async (name) => { await pictureReady(page); await page.mouse.move(Math.round(w * 0.5), 130); await page.screenshot({ path: path.join(reportDir, `${name}-${w}.png`) }); };
  const noOverflow = async (where) => {
    const extra = await page.evaluate(() => Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, document.documentElement.scrollHeight - document.documentElement.clientHeight));
    if (extra > 0) overflow.push(`${where}@${w}: +${extra}px`);
  };
  const stage = page.locator('.stage');
  const dialogue = stage.locator('.dialogue');
  const drawer = page.locator('.drawer');
  const rail = (name) => page.locator('.rail').getByRole('button', { name });
  const spot = (id) => stage.locator(`[data-spot="${id}"]`);
  const tagOf = (id) => spot(id).locator('.spot-tag');
  const tab = (name) => stage.locator('nav.map').getByRole('button', { name });
  const focused = (locator) => locator.evaluate(el => document.activeElement === el);
  const byKeyboard = async (locator) => { await locator.focus(); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab'); };
  const saved = () => dialogue.getByText('✓ Added to your notes.').waitFor();
  const tag = `(${w}×${h})`;
  const ready = () => pictureReady(page);
  // Everything the learner must be able to see and click in a scene, and that nothing may cover.
  const layoutOk = async (ids) => {
    const fixed = [stage.locator('nav.map'), stage.locator('.stage-write'), page.locator('.rail')];
    const boxes = [];
    for (const id of ids) { const b = await box(tagOf(id)); if (!inside(b, w, h)) return `marker ${id} outside the window`; boxes.push([id, b]); }
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) if (!apart(boxes[i][1], boxes[j][1])) return `${boxes[i][0]} overlaps ${boxes[j][0]}`;
    const covers = [...fixed, stage.locator('.caption'), dialogue];   // an open side panel is an overlay the learner opened; see the panel checks
    for (const l of covers) { if (!(await l.count()) || !(await l.first().isVisible())) continue; const c = await box(l.first()); if (!inside(c, w, h)) return 'interface outside the window';
      for (const [id, b] of boxes) if (!apart(b, c)) return `${id} is covered by ${await l.first().evaluate(el => el.className)}`; }
    if (await dialogue.count() && await drawer.isVisible() && !apart(await box(dialogue), await box(drawer))) return 'panel covers the dialogue';
    return '';
  };
  const sceneIs = async (id) => await page.locator(`.stage-${id}`).isVisible() && await stage.count() === 1 && (await stage.evaluate(el => { const r = el.getBoundingClientRect(); return Math.abs(r.width - innerWidth) < 2 && Math.abs(r.height - innerHeight) < 2; }))
    && !(await page.locator('header.topbar').isVisible()) && await page.locator('.rail button').count() === 4 && await stage.locator('nav.map button').count() === 4
    && await stage.locator('.stage-write').isVisible() && await page.locator('.guide, .scene-items, .place').count() === 0;

  // ================= library =================
  await page.goto(origin);
  await page.getByLabel(/Your game nickname/).fill('Mika');
  await page.keyboard.press('Enter');
  await page.locator('.stage-library').waitFor();
  await noOverflow('library');
  ok(`the library opens as a full-window scene with the same place strip, write button and four-button rail as the coffee corner ${tag}`, await sceneIs('library'));
  await ready();
  let pic = await pictureCheck(page, 'library');
  ok(`its backdrop is the bundled scene placeholder, unstretched and covering the window (${pic.shown}% of it shown); no placeholder note ${tag}`, pic.natural === '1672x941' && pic.ratioOff < 0.003 && pic.covers && pic.tight && await stage.locator('svg.stage-bg-draft').count() === 0 && !/Placeholder/.test(await stage.locator('.caption').innerText()));
  ok(`laptop, notebook and phone markers sit on their pins in the picture (off by ${pic.drift.toFixed(1)} px); the phone, which the picture lacks, is drawn on the desk under its marker ${tag}`, pic.drift <= 1.5 && await stage.locator('.prop-phone').isVisible()
    && await page.evaluate(() => { const p = document.querySelector('.prop-phone').getBoundingClientRect(), d = document.querySelector('[data-spot="phone"] .spot-dot').getBoundingClientRect(); const cx = d.left + d.width / 2, cy = d.top + d.height / 2; return p.width >= 40 && cx > p.left && cx < p.right && cy > p.top - 4 && cy < p.bottom; }));
  ok(`on first arrival the caption carries the story opening and the place description; nothing is open ${tag}`, /After three days in bed/.test(await stage.locator('.caption-intro').innerText()) && /Your usual desk on the second floor/.test(await stage.locator('.caption').innerText()) && await dialogue.count() === 0);
  ok(`the three things on the desk are markers named in words, each with its action ${tag}`, await stage.locator('.stage-spot').count() === 3 && (await spot('laptop').getAttribute('aria-label')) === 'Your laptop: look' && /Your phone/.test(await tagOf('phone').innerText()) && /CS101 syllabus\s*查看/.test(await tagOf('syllabus').innerText()));
  let problem = await layoutOk(['laptop', 'phone', 'syllabus']);
  ok(`markers are inside the window and nothing covers them or each other ${tag}${problem && ': ' + problem}`, problem === '');
  for (const [locator, name] of [[tagOf('laptop'), 'marker label'], [stage.locator('.caption-intro'), 'story opening'], [stage.locator('.caption-clock'), 'clock']]) {
    const ratio = await contrastOf(locator.first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await shot('01-library-enter');

  // open the phone by keyboard, with a slow save so "Saving…" can be seen
  await page.route('**/hotspots/phone', async route => { await new Promise(r => setTimeout(r, 600)); await route.continue(); });
  await byKeyboard(spot('phone'));
  ok(`a marker reached by keyboard shows a focus ring on its label ${tag}`, await focused(spot('phone')) && await tagOf('phone').evaluate(el => getComputedStyle(el).outlineStyle !== 'none'));
  await page.keyboard.press('Enter');
  await dialogue.getByText(/Saving this to your notes/).waitFor();
  ok(`while saving it says "Saving…": no tick on the marker and nothing in the notes yet ${tag}`, await spot('phone').locator('.tick').count() === 0 && /0/.test(await rail(/Notes/).locator('.rail-count').innerText()));
  await saved();
  await page.unroute('**/hotspots/phone');
  ok(`Enter opens the message in the low dialogue box: sender as the name, what it is above the text, the full text ${tag}`, (await dialogue.locator('.name-tag').innerText()) === 'Ivy' && /A message from Ivy/.test(await dialogue.locator('.line-kicker').innerText()) && /I could send you my lecture notes by tonight/.test(await dialogue.locator('.line').innerText()) && (await spot('phone').getAttribute('aria-expanded')) === 'true');
  ok(`once saved the marker gets a tick and says so in its label; note and expression counts go up ${tag}`, await spot('phone').locator('.tick').count() === 1 && /in your notes/.test(await spot('phone').getAttribute('aria-label')) && (await state()).notes.includes('phone') && /1/.test(await rail(/Notes/).locator('.rail-count').innerText()) && /1\/4/.test(await rail(/Expressions/).innerText()));
  const fontPx = await dialogue.locator('.line').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
  problem = await layoutOk(['laptop', 'phone', 'syllabus']);
  ok(`the dialogue box is inside the window, leaves every marker clear, and keeps the type size (${fontPx.toFixed(2)}px) ${tag}${problem && ': ' + problem}`, problem === '' && fontPx >= 18.8 && inside(await box(dialogue), w, h));
  for (const [locator, name] of [[dialogue.locator('.line-kicker'), 'kicker'], [tagOf('phone'), 'open marker label'], [dialogue.getByRole('button', { name: /Close · 关闭/ }), 'Close button']]) {
    const ratio = await contrastOf(locator.first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await shot('02-library-phone');
  await page.keyboard.press('Escape');
  ok(`Escape closes the box, the caption returns (without the opening now), and focus is back on the phone marker ${tag}`, await dialogue.count() === 0 && await stage.locator('.caption').isVisible() && await stage.locator('.caption-intro').count() === 0 && await focused(spot('phone')));

  // failed save and retry
  await page.route('**/hotspots/syllabus', route => route.abort('connectionfailed'));
  await spot('syllabus').click();
  await dialogue.getByText('Not saved.').waitFor();
  ok(`a failed save says "Not saved" in the box and on the marker; no tick, nothing in the notes ${tag}`, /not saved/.test(await tagOf('syllabus').innerText()) && await spot('syllabus').locator('.tick').count() === 0 && !(await state()).notes.includes('syllabus') && /1/.test(await rail(/Notes/).locator('.rail-count').innerText()) && await dialogue.getByText('✓ Added to your notes.').count() === 0);
  const errRatio = await contrastOf(dialogue.locator('.save-line-error'));
  const unsavedRatio = await contrastOf(tagOf('syllabus').locator('.unsaved'));
  ok(`the failure message is readable (${errRatio.toFixed(1)}:1), so is "not saved" on the marker (${unsavedRatio.toFixed(1)}:1), and the box still fits ${tag}`, errRatio >= 4.5 && unsavedRatio >= 4.5 && inside(await box(dialogue), w, h));
  await shot('03-library-save-failed');
  await page.unroute('**/hotspots/syllabus');
  await dialogue.getByRole('button', { name: 'Retry saving' }).click();
  await saved();
  ok(`Retry saves it: tick on the marker, note recorded ${tag}`, await spot('syllabus').locator('.tick').count() === 1 && (await state()).notes.includes('syllabus') && /Late submission requires/.test(await dialogue.locator('.line').innerText()));
  await spot('laptop').click();
  await dialogue.locator('.line').getByText(/The file is open where you left it/).waitFor();
  await saved();
  ok(`another marker can be opened straight away; the box changes to it ${tag}`, (await dialogue.locator('.name-tag').innerText()) === 'Your laptop' && (await spot('syllabus').getAttribute('aria-expanded')) === 'false' && (await spot('laptop').getAttribute('aria-expanded')) === 'true');
  await byKeyboard(dialogue.getByRole('button', { name: /Close · 关闭/ }));
  await page.keyboard.press('Enter');
  ok(`the Close button closes the box and returns focus to the laptop marker ${tag}`, await dialogue.count() === 0 && await focused(spot('laptop')));

  // panels over the library
  await byKeyboard(rail(/Task/));
  await page.keyboard.press('Enter');
  const taskText = await drawer.innerText();
  ok(`the task panel shows the goal, the next step and progress (library all looked at, others not visited) ${tag}`, /Negotiate a workable new plan/.test(taskText) && /Next · 下一步/.test(taskText) && /Library · study desk\s*✓ all looked at/.test(taskText) && /CS corridor\s*not visited yet/.test(taskText));
  problem = await layoutOk(['laptop', 'phone', 'syllabus']);
  ok(`the open panel stays in the window and off the dialogue area; markers it does not reach stay clear ${tag}${problem && ': ' + problem}`, problem === '' && inside(await box(drawer), w, h));
  await shot('04-library-task-panel');
  await page.keyboard.press('Escape');
  ok(`Escape closes the panel, focus returns to the Task button, and every marker is visible and clickable again ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Task/))
    && await page.evaluate(() => [...document.querySelectorAll('.stage-spot .spot-tag')].every(t => { const r = t.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return t.contains(top) || top === t; })));
  await rail(/Notes/).click();
  const notesText = await drawer.innerText();
  ok(`the notes panel lists the three clues found here and nothing else ${tag}`, /Ivy offered her lecture notes/.test(notesText) && /prior approval/.test(notesText) && /introduction and a list of headings/.test(notesText) && !/Not explored|Session|New game/.test(notesText));
  await drawer.getByRole('button', { name: /Close · 收起/ }).click();
  ok(`Close returns focus to the Notes button ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Notes/)));
  await rail(/Expressions/).click();
  ok(`the expression book shows the pattern met on the phone, highlighted in its sentence ${tag}`, (await drawer.locator('.phrasebook mark').allInnerTexts()).join('|') === 'I could|by' && /遇到过 ≠ 已掌握/.test(await drawer.innerText()));
  await page.keyboard.press('Escape');
  const before = await sid();
  await rail(/Settings/).click();
  await drawer.getByRole('button', { name: /New game/ }).click();
  const confirmBox = drawer.getByRole('alertdialog', { name: 'Start a new game?' });
  ok(`New game in settings still asks first, with the keyboard on "Keep playing" ${tag}`, await focused(confirmBox.getByRole('button', { name: /Keep playing/ })) && new RegExp(before).test(await drawer.innerText()));
  await page.keyboard.press('Enter');
  await page.keyboard.press('Escape');
  ok(`keeping the game changes nothing: same session, three notes ${tag}`, await sid() === before && (await state()).notes.length === 3 && !(await drawer.isVisible()) && await focused(rail(/Settings/)));

  // refresh in the library
  await page.reload();
  await page.locator('.stage-library').waitFor();
  await ready();
  ok(`refresh returns to the library scene with ticks on all three markers and the counts ${tag}`, await sceneIs('library') && await stage.locator('.stage-spot .tick').count() === 3 && /3/.test(await rail(/Notes/).locator('.rail-count').innerText()) && /1\/4/.test(await rail(/Expressions/).innerText()) && await stage.locator('.caption-intro').count() === 0);

  // ================= corridor =================
  await byKeyboard(tab(/CS corridor/));
  await page.keyboard.press('Enter');
  await page.locator('.stage-corridor').waitFor();
  ok(`the corridor is reached with the keyboard and is the same kind of scene ${tag}`, await sceneIs('corridor') && (await tab(/CS corridor/).getAttribute('aria-current')) === 'location' && /you are here/.test(await tab(/CS corridor/).getAttribute('aria-label')));
  await ready();
  pic = await pictureCheck(page, 'corridor');
  ok(`the corridor has its picture (unstretched, covering, ${pic.shown}% shown), caption and one marker pinned on the cork board (off by ${pic.drift.toFixed(1)} px) ${tag}`, pic.natural === '1672x941' && pic.ratioOff < 0.003 && pic.covers && pic.tight && pic.drift <= 1.5 && /notice board hangs between the office doors/.test(await stage.locator('.caption').innerText()) && !/Placeholder/.test(await stage.locator('.caption').innerText()) && await stage.locator('.stage-spot').count() === 1 && await dialogue.count() === 0);
  problem = await layoutOk(['noticeboard']);
  ok(`the notice-board marker is clear ${tag}${problem && ': ' + problem}`, problem === '');
  await shot('05-corridor-enter');
  await spot('noticeboard').click();
  await saved();
  ok(`the notice is read in the dialogue box and saved ${tag}`, (await dialogue.locator('.name-tag').innerText()) === 'Notice board' && /Office hours: Wed 14:00–15:00/.test(await dialogue.locator('.line').innerText()) && (await state()).notes.includes('noticeboard') && (await state()).location === 'corridor');
  problem = await layoutOk(['noticeboard']);
  ok(`the box does not cover the marker ${tag}${problem && ': ' + problem}`, problem === '');
  await shot('06-corridor-notice');
  await rail(/Notes/).click();
  ok(`a panel can be open together with the dialogue without covering it ${tag}`, (await layoutOk(['noticeboard'])) === '' && /Office hours/.test(await drawer.innerText()));

  // a place change that cannot be saved, with the panel still open
  await page.route('**/location', route => route.abort('connectionfailed'));
  await tab(/Outside Prof. Chen/).click();
  await page.locator('.stage-office').waitFor();
  await page.getByText('Your place on campus was not saved').waitFor();
  ok(`moving on closes the dialogue; a place that could not be saved is reported in the scene and the server keeps the old place ${tag}`, await dialogue.count() === 0 && (await state()).location === 'corridor' && inside(await box(stage.locator('.stage-alert')), w, h));
  ok(`the open panel stays open across the move and still closes with Escape, focus back on its button ${tag}`, await drawer.isVisible());
  await page.keyboard.press('Escape');
  ok(`…closed, focus on Notes ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Notes/)));
  await shot('07-office-place-not-saved');
  await page.unroute('**/location');
  await stage.locator('.stage-alert').getByRole('button', { name: 'Retry' }).click();
  await page.waitForFunction(() => !document.querySelector('.stage-alert'));
  ok(`Retry saves the new place ${tag}`, (await state()).location === 'office');

  // ================= office =================
  ok(`outside the office is the same kind of scene, with its caption and one marker ${tag}`, await sceneIs('office') && /Room 2\.14/.test(await stage.locator('.caption').innerText()) && !/Placeholder/.test(await stage.locator('.caption').innerText()) && await stage.locator('.stage-spot').count() === 1 && (await spot('office_door').getAttribute('aria-label')) === 'Listen at the door: listen');
  await ready();
  pic = await pictureCheck(page, 'office');
  ok(`its picture is unstretched and covers the window (${pic.shown}% shown); the marker is pinned on the door (off by ${pic.drift.toFixed(1)} px) ${tag}`, pic.natural === '1672x941' && pic.ratioOff < 0.003 && pic.covers && pic.tight && pic.drift <= 1.5);
  problem = await layoutOk(['office_door']);
  ok(`the door marker is clear ${tag}${problem && ': ' + problem}`, problem === '');
  await shot('08-office-enter');
  await byKeyboard(spot('office_door'));
  await page.keyboard.press('Enter');
  await saved();
  ok(`listening shows who spoke, that it was overheard, and the line; it is saved and the expression counted ${tag}`, (await dialogue.locator('.name-tag').innerText()) === 'A student, leaving' && /At the half-open door · 你听到了/.test(await dialogue.locator('.line-kicker').innerText()) && /I'll make sure to send it by Friday/.test(await dialogue.locator('.line').innerText()) && (await state()).notes.includes('office_door') && /2\/4/.test(await rail(/Expressions/).innerText()));
  problem = await layoutOk(['office_door']);
  ok(`the box does not cover the marker ${tag}${problem && ': ' + problem}`, problem === '');
  await shot('09-office-door');
  await page.keyboard.press('Escape');
  ok(`Escape closes it and focus returns to the door marker ${tag}`, await dialogue.count() === 0 && await focused(spot('office_door')));
  await page.reload();
  await page.locator('.stage-office').waitFor();
  await ready();
  ok(`refresh returns to the office scene with the tick and counts ${tag}`, await sceneIs('office') && await spot('office_door').locator('.tick').count() === 1 && /5/.test(await rail(/Notes/).locator('.rail-count').innerText()) && /2\/4/.test(await rail(/Expressions/).innerText()));

  // ================= between the four places =================
  await tab(/Coffee corner/).click();
  await page.locator('.stage-cafe').waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('.stage img')].length === 2 && [...document.querySelectorAll('.stage img')].every(i => i.complete && i.naturalWidth > 0));
  await page.evaluate(() => Promise.all([...document.querySelectorAll('.stage img')].map(i => i.decode())));
  ok(`office → coffee corner: its own background, Ivy as the entry, no markers ${tag}`, await sceneIs('cafe') && await stage.locator('.stage-figure').isVisible() && await stage.locator('.stage-spot').count() === 0 && !/Placeholder/.test(await stage.locator('.caption').innerText()) && (await state()).location === 'cafe');
  await stage.locator('.stage-figure').click();
  await dialogue.locator('.line').getByText(/he likes concrete plans/).waitFor();
  await saved();
  await stage.getByRole('button', { name: /Have you ever asked him for more time/ }).click();
  await dialogue.locator('.line').getByText(/I emailed him before I went to see him/).waitFor();
  await saved();
  ok(`Ivy's questions keep their order here (follow-up now open) ${tag}`, await stage.getByRole('button', { name: /What did you ask for, exactly/ }).isEnabled() && /3\/4/.test(await rail(/Expressions/).innerText()));
  await dialogue.getByRole('button', { name: /End chat/ }).click();
  ok(`End chat closes the conversation and returns focus to Ivy ${tag}`, await dialogue.count() === 0 && await focused(stage.locator('.stage-figure')));
  for (const [name, id] of [[/Library/, 'library'], [/Coffee corner/, 'cafe'], [/CS corridor/, 'corridor'], [/Coffee corner/, 'cafe'], [/Outside Prof. Chen/, 'office'], [/Library/, 'library'], [/CS corridor/, 'corridor']]) {
    await tab(name).click();
    await page.locator(`.stage-${id}`).waitFor();
    await ready();
    await page.waitForFunction(async (expected) => { const sidNow = localStorage.getItem('ccq_session_id'); const s = await fetch(`/api/sessions/${sidNow}`).then(r => r.json()); return s.location === expected; }, id);
    assert.ok(await sceneIs(id), `scene ${id} after switching ${tag}`);
    await noOverflow(`switch-${id}`);
  }
  ok(`seven moves in a row between the four places each show the right scene, saved on the server, with no leftovers from the last one ${tag}`, (await state()).location === 'corridor' && (await state()).notes.length === 7 && await spot('noticeboard').locator('.tick').count() === 1);

  // ================= writing and back =================
  await stage.getByRole('button', { name: /Write to Prof. Chen/ }).click();
  const editor = page.getByLabel('Email to Prof. Chen');
  await editor.fill('Dear Prof. Chen, I am writing about the report.');
  ok(`the write button opens the editor with the usual top bar and sidebar (notes and expressions beside it) ${tag}`, await page.locator('header.topbar').isVisible() && await page.locator('aside.sidebar .notes').isVisible() && await page.locator('aside.sidebar .phrasebook').isVisible() && await page.locator('.rail').count() === 0);
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.locator('.stage-corridor').waitFor();
  ok(`Back to campus returns to the corridor scene with the draft saved and the button now "Continue your email" ${tag}`, await sceneIs('corridor') && (await state()).prep.draft === 'Dear Prof. Chen, I am writing about the report.' && /Continue your email/.test(await stage.locator('.stage-write').innerText()));
  await byKeyboard(stage.locator('.stage-write'));
  await page.keyboard.press('Enter');
  await editor.waitFor();
  ok(`the write button works from the keyboard and the draft is back in the editor ${tag}`, await editor.inputValue() === 'Dear Prof. Chen, I am writing about the report.');
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.locator('.stage-corridor').waitFor();
  await tab(/Library/).click();
  await page.locator('.stage-library').waitFor();
  await shot('10-library-all-seen');
  await context.close();
}

// ---- other window shapes: very wide, ultra-wide, and nearly square. The picture is cropped differently in each;
// markers must stay on their objects, inside the window, and clear of the interface and the dialogue box.
for (const [w, h] of [[2560, 1080], [3440, 1440], [1280, 1024], [1024, 768]]) {
  const context = await browser.newContext({ viewport: { width: w, height: h }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  const stage = page.locator('.stage'), dialogue = stage.locator('.dialogue');
  const tagOf = (id) => stage.locator(`[data-spot="${id}"] .spot-tag`);
  const tag = `(${w}×${h})`;
  await page.goto(origin);
  await page.getByLabel(/Your game nickname/).fill('Mika');
  await page.keyboard.press('Enter');
  await page.locator('.stage-library').waitFor();
  for (const [id, tabName, spots] of [['library', /Library/, ['laptop', 'syllabus', 'phone']], ['corridor', /CS corridor/, ['noticeboard']], ['office', /Outside Prof. Chen/, ['office_door']]]) {
    if (id !== 'library') { await stage.locator('nav.map').getByRole('button', { name: tabName }).click(); await page.locator(`.stage-${id}`).waitFor(); }
    await pictureReady(page);
    const pic = await pictureCheck(page, id);
    ok(`${id}: picture unstretched and covering (${pic.shown}% of it shown), markers on their pins (off by ${pic.drift.toFixed(1)} px) ${tag}`, pic.natural === '1672x941' && pic.ratioOff < 0.003 && pic.covers && pic.tight && pic.drift <= 1.5);
    const clear = async () => {
      const fixed = [stage.locator('nav.map'), stage.locator('.stage-write'), page.locator('.rail'), stage.locator('.caption'), dialogue];
      const boxes = []; for (const sp of spots) { const b = await box(tagOf(sp)); if (!inside(b, w, h)) return `${sp} label outside the window`; boxes.push([sp, b]); }
      for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) if (!apart(boxes[i][1], boxes[j][1])) return `${boxes[i][0]} overlaps ${boxes[j][0]}`;
      for (const l of fixed) { if (!(await l.count()) || !(await l.first().isVisible())) continue; const c = await box(l.first()); if (!inside(c, w, h)) return 'interface outside the window'; for (const [sp, b] of boxes) if (!apart(b, c)) return `${sp} covered by ${await l.first().evaluate(el => el.className)}`; }
      return '';
    };
    let problem = await clear();
    ok(`${id}: marker labels inside the window and clear of each other and the interface ${tag}${problem && ': ' + problem}`, problem === '');
    await page.mouse.move(Math.round(w * 0.5), 130);
    await page.screenshot({ path: path.join(reportDir, `20-${id}-enter-${w}x${h}.png`) });
    await stage.locator(`[data-spot="${spots[spots.length - 1]}"]`).click();
    await dialogue.getByText('✓ Added to your notes.').waitFor();
    problem = await clear();
    const ratio = await contrastOf(dialogue.locator('.line'));
    ok(`${id}: with the dialogue open the markers stay clear, the box fits and its text reads ${ratio.toFixed(1)}:1 ${tag}${problem && ': ' + problem}`, problem === '' && ratio >= 4.5 && inside(await box(dialogue), w, h));
    await page.mouse.move(Math.round(w * 0.5), 130);
    await page.screenshot({ path: path.join(reportDir, `21-${id}-open-${w}x${h}.png`) });
    await page.keyboard.press('Escape');
  }
  await stage.locator('nav.map').getByRole('button', { name: /Coffee corner/ }).click();
  await page.locator('.stage-cafe').waitFor();
  await pictureReady(page);
  const cafe = await page.evaluate(() => { const st = document.querySelector('.stage').getBoundingClientRect(), bg = document.querySelector('.stage-bg').getBoundingClientRect(), t = document.querySelector('.figure-tag').getBoundingClientRect(), f = document.querySelector('.figure-art').getBoundingClientRect();
    return { bg: Math.abs(bg.width - st.width) < 2 && Math.abs(bg.height - st.height) < 2, tagIn: t.left >= 0 && t.right <= st.width && t.top >= 0, faceIn: f.left + f.width * 0.29 >= 0 && f.left + f.width * 0.56 <= st.width, ratio: f.width / f.height }; });
  ok(`coffee corner unchanged at this shape: background fills, Ivy keeps her proportions, her face and label are in the window ${tag}`, cafe.bg && cafe.tagIn && cafe.faceIn && Math.abs(cafe.ratio - 1024 / 1536) < 0.01);
  await page.screenshot({ path: path.join(reportDir, `22-cafe-${w}x${h}.png`) });
  const extra = await page.evaluate(() => Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, document.documentElement.scrollHeight - document.documentElement.clientHeight));
  if (extra > 0) overflow.push(`shape ${w}x${h}: +${extra}px`);
  await context.close();
}
await browser.close();
ok('no page overflow at any of the seven window sizes', overflow.length === 0 || (console.log(overflow), false));
ok('no browser page errors', pageErrors.length === 0 || (console.log(pageErrors), false));
writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: checks.length, checks, overflow, pageErrors, origin, mock_only: true }, null, 2));
console.log(`${checks.length} scene checks passed (round 12 suite, public SVG placeholders). Evidence: ${reportDir}`);
