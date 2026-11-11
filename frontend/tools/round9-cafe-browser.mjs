// Run against backend/tools/round6_acceptance_server.py, never the ordinary server.
// node frontend/tools/round9-cafe-browser.mjs [http://127.0.0.1:18746]
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
const reportDir = path.join(root, 'output/playwright', `round9-cafe-${new Date().toISOString().replace(/[:.]/g, '-')}`);
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
const LONG = ' I also told him exactly which parts were finished and which were not, because he always asks. He wants to know what you will send, and when, and he does not like vague promises. If you can name a day and a time, say it. If you cannot, say what you can do first.';

for (const [w, h] of [[1366, 768], [1920, 1080], [1280, 720]]) {
  const context = await browser.newContext({ viewport: { width: w, height: h }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  const sid = () => page.evaluate(() => localStorage.getItem('ccq_session_id'));
  const state = async (id) => page.request.get(`/api/sessions/${id ?? await sid()}`).then(r => r.json());
  const shot = (name) => page.screenshot({ path: path.join(reportDir, `${name}-${w}.png`) });
  const noOverflow = async (where) => {
    const extra = await page.evaluate(() => Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, document.documentElement.scrollHeight - document.documentElement.clientHeight));
    if (extra > 0) overflow.push(`${where}@${w}: +${extra}px`);
  };
  const painted = async () => {
    await page.waitForFunction(() => [...document.querySelectorAll('.stage img')].length === 2 && [...document.querySelectorAll('.stage img')].every(i => i.complete && i.naturalWidth > 0));
    await page.evaluate(() => Promise.all([...document.querySelectorAll('.stage img')].map(i => i.decode())));   // painted, not just downloaded
  };
  const stage = page.locator('.stage');
  const dialogue = stage.locator('.dialogue');
  const figure = stage.locator('.stage-figure');
  const drawer = page.locator('.drawer');
  const rail = (name) => page.locator('.rail').getByRole('button', { name });
  const focused = (locator) => locator.evaluate(el => document.activeElement === el);
  const byKeyboard = async (locator) => { await locator.focus(); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab'); };
  // Parts of the Ivy picture (1024×1536), as shares of the drawn image: face, and hands + notebook.
  const part = (x0, y0, x1, y1) => stage.locator('.figure-art').evaluate((el, [a, b, c, d]) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width * a, y: r.y + r.height * b, width: r.width * (c - a), height: r.height * (d - b) }; }, [x0, y0, x1, y1]);
  const face = () => part(0.29, 0.04, 0.56, 0.17);
  const hands = () => part(0.42, 0.33, 0.68, 0.58);
  const clearOf = async (region, ...locators) => { for (const l of locators) { if (await l.count() && await l.first().isVisible() && !apart(region, await box(l.first()))) return false; } return true; };
  const tag = `(${w}×${h})`;

  await page.goto(origin);
  await page.getByLabel(/Your game nickname/).fill('Mika');
  await page.keyboard.press('Enter');
  await page.locator('.campus').waitFor();
  ok(`the game opens in the library scene (round 12: every place uses the scene layout) ${tag}`,
    await page.locator('.stage-library').isVisible() && !(await page.locator('header.topbar').isVisible()) && await page.locator('.rail button').count() === 4);
  await page.locator('.scene-now').getByRole('button', { name: /Your phone/ }).click();
  await page.locator('.scene-now').getByText('✓ Added to your notes.').waitFor();

  // ---- arrive
  await page.locator('nav.map').getByRole('button', { name: /Coffee corner/ }).click();
  await stage.waitFor();
  await painted();
  await noOverflow('arrive');
  const art = await page.evaluate(() => {
    const bg = document.querySelector('.stage-bg'), ivy = document.querySelector('.figure-art'), st = document.querySelector('.stage').getBoundingClientRect();
    const b = bg.getBoundingClientRect(), r = ivy.getBoundingClientRect();
    return { bgNatural: [bg.naturalWidth, bg.naturalHeight], bgFit: getComputedStyle(bg).objectFit, bgFills: Math.abs(b.width - st.width) < 3 && Math.abs(b.height - st.height) < 3,
      stageFills: Math.abs(st.width - innerWidth) < 2 && Math.abs(st.height - innerHeight) < 2, ivyNatural: [ivy.naturalWidth, ivy.naturalHeight], ivyRatio: r.width / r.height, ivyRect: [r.x, r.y, r.width, r.height].map(Math.round).join(',') };
  });
  ok(`the scene fills the whole window; the supplied background and Ivy are two separate layers ${tag}`, art.stageFills && art.bgNatural.join('x') === '1672x941' && art.ivyNatural.join('x') === '1024x1536');
  ok(`the background fills the scene by cropping, not stretching; Ivy keeps her proportions (${art.ivyRatio.toFixed(3)} vs 0.667) ${tag}`, art.bgFit === 'cover' && art.bgFills && Math.abs(art.ivyRatio - 1024 / 1536) < 0.01);
  ok(`the big top bar is hidden here; nickname and XP are not on the scene ${tag}`, !(await page.locator('header.topbar').isVisible()) && !/Playing as|XP/.test(await stage.innerText()) && !/XP/.test(await page.locator('.rail').innerText()));
  ok(`before talking there is no dialogue box: a caption and a "Talk to Ivy" label ${tag}`, await dialogue.count() === 0 && /A few tables beside the vending machines/.test(await stage.locator('.caption').innerText()) && /Talk to Ivy/.test(await stage.locator('.figure-tag').innerText()));
  ok(`the current place is marked by text and icon, not colour alone ${tag}`,
    await stage.locator('nav.map [aria-current="location"]').count() === 1 && /you are here/.test(await stage.locator('nav.map [aria-current="location"]').getAttribute('aria-label')) && await stage.locator('nav.map .tab.here .icon').count() === 1);
  ok(`all four places and the write button are available in the scene ${tag}`, await stage.locator('nav.map button').count() === 4 && await stage.getByRole('button', { name: /Write to Prof. Chen/ }).isVisible());
  ok(`Ivy's face, hands and notebook are clear of the place strip, write button, caption, rail and her label ${tag}`,
    await clearOf(await face(), stage.locator('nav.map'), stage.locator('.stage-write'), stage.locator('.caption'), page.locator('.rail'))
    && await clearOf(await part(0.34, 0.05, 0.56, 0.17), stage.locator('.figure-tag'))   // the label may brush her hair, never her face
    && await clearOf(await hands(), stage.locator('nav.map'), stage.locator('.stage-write'), stage.locator('.caption'), page.locator('.rail'), stage.locator('.figure-tag')) && inside(await face(), w, h));
  for (const [selector, name] of [['.tab.here', 'current place'], ['.tab:not(.here)', 'other place'], ['.stage-write', 'write button'], ['.caption p', 'caption'], ['.rail-btn', 'rail button'], ['.rail-count', 'rail count'], ['.figure-tag', 'Ivy label']]) {
    const ratio = await contrastOf(page.locator(selector).first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await shot('01-enter');

  // ---- rail and panels by keyboard
  ok(`the rail has task, notes, expressions and settings, with counts ${tag}`,
    await page.locator('.rail button').count() === 4 && /1/.test(await rail(/Notes/).innerText()) && /1\/4/.test(await rail(/Expressions/).innerText()));
  await byKeyboard(rail(/Task/));
  ok(`rail buttons show a focus ring when reached by keyboard ${tag}`, await rail(/Task/).evaluate(el => getComputedStyle(el).outlineStyle !== 'none' && parseFloat(getComputedStyle(el).outlineWidth) >= 2));
  await page.keyboard.press('Enter');
  const taskText = await drawer.innerText();
  ok(`Enter opens the task panel: goal, then exploration progress and what is unexplored ${tag}`, (await rail(/Task/).getAttribute('aria-expanded')) === 'true' && /Negotiate a workable new plan/.test(taskText)
    && /Exploring campus/.test(taskText) && /Not explored yet · 尚未探索: \d+ things?/.test(taskText) && /CS corridor\s*not visited yet/.test(taskText) && /Coffee corner\s*0 of 3 looked at/.test(taskText));
  ok(`the panel lies over the scene: Ivy has not moved or resized ${tag}`, (await page.evaluate(() => { const r = document.querySelector('.figure-art').getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })) === art.ivyRect);
  ok(`the open panel is inside the window and clear of Ivy's face, hands and notebook ${tag}`, inside(await box(drawer), w, h) && await clearOf(await face(), drawer) && await clearOf(await hands(), drawer));
  await shot('02-task-panel');
  await page.keyboard.press('Escape');
  ok(`Escape closes the panel and focus returns to the Task button ${tag}`, !(await drawer.isVisible()) && (await rail(/Task/).getAttribute('aria-expanded')) === 'false' && await focused(rail(/Task/)));
  await rail(/Notes/).click();
  const notesText = await drawer.innerText();
  ok(`the notes panel lists only collected clues: no unexplored line, no session ID, no New game ${tag}`, /Ivy offered her lecture notes/.test(notesText) && !/Not explored|not visited|Session|New game|S-[0-9A-F]{6}/.test(notesText));
  await drawer.getByRole('button', { name: /Close · 收起/ }).click();
  ok(`the Close button closes the panel and focus returns to the Notes button ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Notes/)));

  // ---- talk to Ivy with the keyboard
  await byKeyboard(figure);
  ok(`Ivy can be reached by keyboard and her label shows a focus ring ${tag}`, await focused(figure) && await figure.evaluate(el => getComputedStyle(el.querySelector('.figure-tag')).outlineStyle !== 'none'));
  ok(`focusing Ivy does not scroll the scene ${tag}`, await stage.evaluate(el => el.scrollTop === 0 && el.scrollLeft === 0) && inside(await box(stage.locator('nav.map')), w, h));
  await page.keyboard.press('Enter');
  await dialogue.locator('.line').getByText(/he likes concrete plans/).waitFor();
  await dialogue.getByText('✓ Added to your notes.').waitFor();
  const first = stage.getByRole('button', { name: /Have you ever asked him for more time/ });
  const second = stage.getByRole('button', { name: /What did you ask for, exactly/ });
  ok(`Ivy's pre-written opening appears under her name; both questions are offered, the follow-up locked with a reason in words ${tag}`,
    (await dialogue.locator('.name-tag').innerText()) === 'Ivy' && await first.isEnabled() && await second.isDisabled() && /Ask the other question first/.test(await stage.locator('.stage-talk').innerText()));
  const fontPx = await dialogue.locator('.line').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
  const dBox = await box(dialogue);
  measured[`${w}x${h}`] = { dialogueHeight: Math.round(dBox.height), dialogueTop: Math.round(dBox.y), lineFontPx: fontPx };
  ok(`dialogue type is not reduced (${fontPx.toFixed(2)}px = 1.18rem) and the box is compact (${Math.round(dBox.height)}px, ${(100 * dBox.height / h).toFixed(0)}% of the window) ${tag}`, fontPx >= 18.8 && dBox.height <= h * 0.2);
  const talkClear = async () => await clearOf(await face(), dialogue, stage.locator('.choices'), stage.locator('nav.map'), stage.locator('.stage-write'), page.locator('.drawer'))
    && await clearOf(await hands(), dialogue, stage.locator('.choices'), page.locator('.drawer'));
  ok(`dialogue and choices are inside the window and leave Ivy's face, hands and notebook clear ${tag}`, inside(dBox, w, h) && inside(await box(first), w, h) && inside(await box(second), w, h) && await talkClear());
  const gap = (await box(dialogue)).y - ((await box(stage.locator('.choices'))).y + (await box(stage.locator('.choices'))).height);
  const endBox = await box(dialogue.getByRole('button', { name: /End chat/ }));
  ok(`choices do not crowd the dialogue box (gap ${Math.round(gap)}px) and End chat is a comfortable target (${Math.round(endBox.height)}px high) ${tag}`, gap >= 8 && endBox.height >= 28);
  for (const [locator, name] of [[dialogue.locator('.line'), 'dialogue line'], [dialogue.locator('.name-tag'), 'name tag'], [first, 'open question'], [second, 'locked question (disabled)'], [stage.locator('.lock-note'), 'lock reason'], [dialogue.locator('.save-line-saved'), 'saved state'], [dialogue.getByRole('button', { name: /End chat/ }), 'End chat']]) {
    const ratio = await contrastOf(locator.first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await page.mouse.move(Math.round(w * 0.5), Math.round(h * 0.35));
  await shot('03-talk');

  // ---- a failed save of an answer is shown in the dialogue, with retry
  await page.route('**/hotspots/ivy?topic=extension', route => route.abort('connectionfailed'));
  await byKeyboard(first);
  ok(`question buttons show a focus ring ${tag}`, await first.evaluate(el => getComputedStyle(el).outlineStyle !== 'none'));
  await page.keyboard.press('Enter');
  await dialogue.getByText('Not saved.').waitFor();
  ok(`after choosing a question by keyboard, focus moves to the dialogue (the button is gone) ${tag}`, await dialogue.evaluate(el => el.contains(document.activeElement)));
  ok(`a failed save says "Not saved" in the dialogue and adds nothing ${tag}`, !(await state()).notes.includes('ivy:extension') && /1\/4/.test(await rail(/Expressions/).innerText()) && /2/.test(await rail(/Notes/).locator('.rail-count').innerText()));
  const errRatio = await contrastOf(dialogue.locator('.save-line-error'));
  ok(`the failure message is readable (${errRatio.toFixed(1)}:1), says it in words and offers Retry; the box still clears her hands ${tag}`, errRatio >= 4.5 && await dialogue.getByRole('button', { name: 'Retry saving' }).isVisible() && inside(await box(dialogue), w, h) && await talkClear());
  await shot('04-save-failed');
  await page.unroute('**/hotspots/ivy?topic=extension');
  await dialogue.getByRole('button', { name: 'Retry saving' }).click();
  await dialogue.getByText('✓ Added to your notes.').waitFor();
  ok(`retry saves the answer; the rail counts go up ${tag}`, (await state()).notes.includes('ivy:extension') && /2\/4/.test(await rail(/Expressions/).innerText()) && /3/.test(await rail(/Notes/).locator('.rail-count').innerText()));
  ok(`the answer is shown with the learner's question above it ${tag}`, /Have you ever asked him/.test(await dialogue.locator('.you-asked').innerText()) && /I emailed him before I went to see him/.test(await dialogue.locator('.line').innerText()));
  await page.mouse.move(Math.round(w * 0.5), Math.round(h * 0.35));
  await shot('05-answer');
  await second.click();
  await dialogue.locator('.line').getByText(/Would it be possible to hand it in on Monday instead/).waitFor();
  await dialogue.getByText('✓ Added to your notes.').waitFor();
  ok(`questions keep their order; after both nothing is left to ask, and that is said ${tag}`, await stage.locator('.choice').count() === 0 && /asked Ivy everything/.test(await dialogue.innerText()));
  await dialogue.getByRole('button', { name: 'Earlier line' }).click();
  ok(`earlier lines can be read again ${tag}`, /2 \/ 3/.test(await dialogue.locator('.line-nav').innerText()) && /I emailed him/.test(await dialogue.locator('.line').innerText()));
  await dialogue.getByRole('button', { name: 'Later line' }).click();
  const offRatio = await contrastOf(dialogue.getByRole('button', { name: 'Later line' }));
  ok(`at the newest line "Later" is disabled and still readable (${offRatio.toFixed(1)}:1) ${tag}`, await dialogue.getByRole('button', { name: 'Later line' }).isDisabled() && offRatio >= 4.5);
  ok(`with the longest scripted line the dialogue still fits and clears her face, hands and notebook ${tag}`, inside(await box(dialogue), w, h) && await talkClear());
  await noOverflow('answers');

  // ---- expression cards, warm palette
  await rail(/Expressions/).click();
  const book = page.locator('.drawer .phrasebook');
  ok(`the expression book opens with the patterns met and their highlights ${tag}`, /3\/4/.test(await rail(/Expressions/).innerText()) && (await book.locator('mark').allInnerTexts()).includes('Would it be possible to') && /遇到过 ≠ 已掌握/.test(await book.innerText()));
  const card = await book.locator('.phrase').first().evaluate(el => ({ bg: getComputedStyle(el).backgroundColor, edge: getComputedStyle(el).borderLeftColor, pattern: getComputedStyle(el.querySelector('.phrase-pattern')).color, mark: getComputedStyle(el.querySelector('mark')).backgroundColor }));
  ok(`expression cards use cream, warm brown and amber (no blue left) ${tag}`, card.bg === 'rgb(255, 250, 240)' && card.edge === 'rgb(201, 138, 43)' && card.pattern === 'rgb(107, 58, 20)' && card.mark === 'rgb(246, 213, 138)');
  for (const [selector, name] of [['.phrase-pattern', 'expression pattern'], ['.phrase-function .muted', 'expression function'], ['.phrase-snippet mark', 'highlighted words'], ['.fold-inline > summary', 'full-context link'], ['p.muted.small', 'expression footnote']]) {
    const ratio = await contrastOf(book.locator(selector).first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await byKeyboard(book.locator('.fold-inline > summary').first());
  ok(`the full-context toggle shows a focus ring and opens with the keyboard ${tag}`, await book.locator('.fold-inline > summary').first().evaluate(el => getComputedStyle(el).outlineStyle !== 'none'));
  await page.keyboard.press('Enter');
  ok(`full context opens inside the card; the panel scrolls inside itself and stays in the window ${tag}`, await book.locator('.phrase-context').first().isVisible() && inside(await box(drawer), w, h) && await talkClear());
  await page.mouse.move(Math.round(w * 0.5), Math.round(h * 0.35));
  await shot('06-expressions-panel');
  await page.keyboard.press('Escape');
  ok(`Escape closes the expressions panel and focus returns to its button ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Expressions/)));

  // ---- end chat hands focus back to Ivy
  await byKeyboard(dialogue.getByRole('button', { name: /End chat/ }));
  await page.keyboard.press('Enter');
  ok(`End chat closes the dialogue, brings the caption back and returns focus to Ivy ${tag}`, await dialogue.count() === 0 && await stage.locator('.caption').isVisible() && await focused(figure) && /you have talked/.test(await figure.getAttribute('aria-label')));

  // ---- refresh restores the scene and the conversation
  await page.reload();
  await stage.waitFor();
  await painted();
  ok(`refresh comes back to the coffee-corner scene with the rail counts ${tag}`, /3\/4/.test(await rail(/Expressions/).innerText()) && /4/.test(await rail(/Notes/).locator('.rail-count').innerText()) && (await state()).location === 'cafe');
  await figure.click();
  await dialogue.locator('.line').getByText(/Would it be possible to hand it in on Monday instead/).waitFor();
  ok(`the conversation so far is restored (newest line shown, 3 of 3) ${tag}`, /3 \/ 3/.test(await dialogue.locator('.line-nav').innerText()));

  // ---- a place change that cannot be saved is reported in the scene; then the round trip
  await page.route('**/location', route => route.abort('connectionfailed'));
  await page.locator('nav.map').getByRole('button', { name: /Library/ }).click();
  await page.getByText('Your place on campus was not saved').waitFor();
  await page.unroute('**/location');
  await page.locator('.save-line-error').getByRole('button', { name: 'Retry' }).click();
  await page.waitForFunction(() => !document.querySelector('.save-line-error'));
  ok(`leaving for another place shows that place's scene with the same rail; the place is saved after retry ${tag}`, (await state()).location === 'library' && await page.locator('.stage-library').isVisible() && await page.locator('.stage-cafe').count() === 0 && await page.locator('.rail button').count() === 4);
  await page.locator('nav.map').getByRole('button', { name: /Coffee corner/ }).click();
  await page.locator('.stage-cafe').waitFor();
  await painted();
  ok(`coming back to the coffee corner returns the scene with the same progress ${tag}`, (await state()).location === 'cafe' && /3\/4/.test(await rail(/Expressions/).innerText()) && !(await page.locator('header.topbar').isVisible()));

  // ---- writing and back
  await stage.getByRole('button', { name: /Write to Prof. Chen/ }).click();
  const editor = page.getByLabel('Email to Prof. Chen');
  await editor.fill('Dear Prof. Chen, I am writing about the report.');
  ok(`the write button opens the editor with the usual top bar and sidebar ${tag}`, await page.locator('aside.sidebar .phrasebook').isVisible() && await page.locator('.rail').count() === 0 && await page.locator('header.topbar').isVisible());
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await stage.waitFor();
  await painted();
  ok(`coming back from writing returns to the coffee corner with the draft saved ${tag}`, (await state()).prep.draft === 'Dear Prof. Chen, I am writing about the report.' && /Continue your email/.test(await stage.locator('.stage-write').innerText()));

  // ---- settings: session ID and New game with a confirmation that cannot be triggered by accident
  const before = await sid();
  await byKeyboard(rail(/Settings/));
  await page.keyboard.press('Enter');
  const settings = drawer.locator('.settings');
  ok(`the settings panel shows the nickname, XP and this save's session ID ${tag}`, new RegExp(before).test(await settings.innerText()) && /Mika/.test(await settings.innerText()) && /XP/.test(await settings.innerText()));
  await settings.getByRole('button', { name: /New game/ }).click();
  const confirmBox = settings.getByRole('alertdialog', { name: 'Start a new game?' });
  await confirmBox.waitFor();
  ok(`New game asks first; the keyboard lands on "Keep playing", not on the destructive choice ${tag}`, await focused(confirmBox.getByRole('button', { name: /Keep playing/ })) && /current progress\s+stays in the log/.test(await confirmBox.innerText()));
  for (const [locator, name] of [[confirmBox.locator('p'), 'confirmation text'], [confirmBox.getByRole('button', { name: /Keep playing/ }), 'Keep playing'], [confirmBox.getByRole('button', { name: /Start new game/ }), 'Start new game'], [settings.locator('.settings-note'), 'settings note']]) {
    const ratio = await contrastOf(locator.first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  ok(`the settings panel with the confirmation fits in the window ${tag}`, inside(await box(drawer), w, h) && inside(await box(confirmBox), w, h));
  await shot('07-settings-confirm');
  await page.keyboard.press('Enter');
  ok(`Enter keeps the current game: same session, panel still open, focus back on New game ${tag}`, await sid() === before && await confirmBox.count() === 0 && await drawer.isVisible() && await focused(settings.getByRole('button', { name: /New game/ })));
  await settings.getByRole('button', { name: /New game/ }).click();
  await page.keyboard.press('Escape');
  ok(`Escape cancels only the confirmation: same session, settings still open ${tag}`, await sid() === before && await confirmBox.count() === 0 && await drawer.isVisible());
  await page.keyboard.press('Escape');
  ok(`a second Escape closes settings; the game is untouched (4 notes, at the coffee corner) ${tag}`, !(await drawer.isVisible()) && await focused(rail(/Settings/)) && (await state()).notes.length === 4 && (await state()).location === 'cafe');
  await rail(/Settings/).click();
  await settings.getByRole('button', { name: /New game/ }).click();
  await confirmBox.getByRole('button', { name: /Start new game/ }).click();
  await page.getByLabel(/Your game nickname/).waitFor();
  const old = await state(before);
  ok(`confirming starts a new game at the nickname screen; the previous save is still on the server, unchanged ${tag}`, await sid() !== before && old.session_id === before && old.notes.length === 4 && old.prep.draft === 'Dear Prof. Chen, I am writing about the report.' && old.player_name === 'Mika');
  await context.close();
}

// ---- long dialogue: the scripted line is made four times longer in the browser only (the server and content file are untouched)
for (const [w, h] of [[1366, 768], [1920, 1080]]) {
  const context = await browser.newContext({ viewport: { width: w, height: h }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  await page.route('**/api/content/chapter2', async route => {
    const response = await route.fetch();
    const content = await response.json();
    const ivy = content.hotspots.find(x => x.id === 'ivy');
    ivy.text += LONG + LONG;
    await route.fulfill({ response, json: content });
  });
  const tag = `(${w}×${h}, long line)`;
  await page.goto(origin);
  await page.getByLabel(/Your game nickname/).fill('Mika');
  await page.keyboard.press('Enter');
  await page.locator('.campus').waitFor();
  await page.locator('nav.map').getByRole('button', { name: /Coffee corner/ }).click();
  const stage = page.locator('.stage');
  await stage.waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('.stage img')].length === 2 && [...document.querySelectorAll('.stage img')].every(i => i.complete && i.naturalWidth > 0));
  await page.evaluate(() => Promise.all([...document.querySelectorAll('.stage img')].map(i => i.decode())));
  await stage.locator('.stage-figure').click();
  const dialogue = stage.locator('.dialogue');
  const line = dialogue.locator('.line');
  await dialogue.getByText('✓ Added to your notes.').waitFor();
  const m = await line.evaluate(el => ({ font: parseFloat(getComputedStyle(el).fontSize), lh: parseFloat(getComputedStyle(el).lineHeight), client: el.clientHeight, scroll: el.scrollHeight, chars: el.textContent.length }));
  const part = (x0, y0, x1, y1) => stage.locator('.figure-art').evaluate((el, [a, b, c, d]) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width * a, y: r.y + r.height * b, width: r.width * (c - a), height: r.height * (d - b) }; }, [x0, y0, x1, y1]);
  const dBox = await box(dialogue), cBox = await box(stage.locator('.choices'));
  ok(`a ${m.chars}-character line keeps its type size (${m.font.toFixed(2)}px) and shows ${Math.round(m.client / m.lh)} whole lines, the rest by scrolling inside the text area ${tag}`, m.font >= 18.8 && m.scroll > m.client && Math.abs(m.client / m.lh - Math.round(m.client / m.lh)) < 0.06 && m.client / m.lh >= 3);
  ok(`the long dialogue stays in the window and off Ivy's face, hands and notebook; choices and actions stay in place ${tag}`, inside(dBox, w, h) && apart(await part(0.29, 0.04, 0.56, 0.17), dBox) && apart(await part(0.42, 0.33, 0.68, 0.58), dBox) && apart(await part(0.42, 0.33, 0.68, 0.58), cBox) && apart(cBox, dBox)
    && inside(await box(dialogue.getByRole('button', { name: /End chat/ })), w, h) && await dialogue.getByText('✓ Added to your notes.').isVisible());
  await line.focus();
  await page.keyboard.press('End');
  await page.waitForFunction(() => { const el = document.querySelector('.dialogue .line'); return el.scrollTop + el.clientHeight >= el.scrollHeight - 2; });
  ok(`the text area can be focused and scrolled to the end with the keyboard ${tag}`, await line.evaluate(el => document.activeElement === el && el.scrollTop > 0));
  await page.screenshot({ path: path.join(reportDir, `08-long-line-end-${w}.png`) });
  await page.keyboard.press('Home');
  await page.waitForFunction(() => document.querySelector('.dialogue .line').scrollTop === 0);
  await page.mouse.move(Math.round(w * 0.5), Math.round(h * 0.35));
  await page.screenshot({ path: path.join(reportDir, `08-long-line-${w}.png`) });
  const extra = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (extra > 0) overflow.push(`long@${w}: +${extra}px`);
  await context.close();
}
await browser.close();
ok('no page overflow at 1366×768, 1920×1080 or 1280×720', overflow.length === 0 || (console.log(overflow), false));
ok('no browser page errors', pageErrors.length === 0 || (console.log(pageErrors), false));
writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: checks.length, checks, measured, overflow, pageErrors, origin, mock_only: true, browser: browser.version?.() }, null, 2));
console.log(JSON.stringify(measured));
console.log(`${checks.length} round-11 cafe checks passed. Evidence: ${reportDir}`);
