// Run against backend/tools/round6_acceptance_server.py, never the ordinary server.
// node frontend/tools/round8-ui-browser.mjs [http://127.0.0.1:18746]
// Optional: PLAYWRIGHT_MODULE_PATH points to an already installed playwright package.
// RETIRED in round 12. These checks describe the classic campus page (place cards, phone screen, chat
// bubbles, folding sidebar). All four places now use the scene layout, so they no longer apply.
// frontend/tools/round12-scenes-browser.mjs covers the scenes. The script is kept for the record and
// stops here unless CCQ_RUN_RETIRED=1 is set (it will then fail against the current layout).
if (process.env.CCQ_RUN_RETIRED !== '1') { console.error('round8-ui-browser.mjs is retired (classic campus view replaced in round 12). Run round12-scenes-browser.mjs instead.'); process.exit(2); }
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const origin = process.argv[2] ?? 'http://127.0.0.1:18746';
assert.equal(new URL(origin).hostname, '127.0.0.1', 'The browser checks only support the isolated loopback server');
const reportDir = path.join(root, 'output/playwright', `round8-ui-${new Date().toISOString().replace(/[:.]/g, '-')}`);
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
const checks = [], pageErrors = [], overflow = [];
function ok(name, condition = true) { assert.ok(condition, name); checks.push(name); console.log(`PASS ${name}`); }

// WCAG contrast of an element's text against the first opaque background behind it.
const contrastOf = (locator) => locator.evaluate((el) => {
  const rgb = (c) => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  let bg = null;
  for (let node = el; node; node = node.parentElement) {
    const c = rgb(getComputedStyle(node).backgroundColor);
    if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) { bg = c.slice(0, 3); break; }
  }
  const fg = rgb(getComputedStyle(el).color).slice(0, 3);
  const [a, b] = [lum(fg), lum(bg || [255, 255, 255])].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
});

for (const [w, h] of [[1366, 768], [1920, 1080]]) {
  const context = await browser.newContext({ viewport: { width: w, height: h }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  const sid = () => page.evaluate(() => localStorage.getItem('ccq_session_id'));
  const state = async () => page.request.get(`/api/sessions/${await sid()}`).then(r => r.json());
  const shot = (name) => page.screenshot({ path: path.join(reportDir, `${name}-${w}.png`), fullPage: true });
  const noOverflow = async (where) => {
    const extra = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (extra > 0) overflow.push(`${where}@${w}: +${extra}px`);
  };
  const scene = page.locator('.scene-now');
  const side = (cls) => page.locator(`aside.sidebar .${cls}`);
  const tag = `(${w}×${h})`;

  await page.goto(origin);
  await page.getByRole('button', { name: /Enter the campus/ }).waitFor();
  await noOverflow('nickname');
  ok(`nickname screen keeps keyboard focus visible on the name box ${tag}`,
    await page.getByLabel(/Your game nickname/).evaluate(el => document.activeElement === el && getComputedStyle(el).outlineStyle !== 'none'));
  await page.getByLabel(/Your game nickname/).fill('Mika');
  await page.keyboard.press('Enter');
  await page.locator('.campus').waitFor();
  await noOverflow('desk');

  // keyboard: Tab reaches the places and shows a focus ring
  await page.locator('.guide button').focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => ({ cls: document.activeElement?.className ?? '', outline: getComputedStyle(document.activeElement).outlineStyle }));
  ok(`Tab moves from the write button to the first place with a visible focus ring ${tag}`, /place/.test(focused.cls) && focused.outline !== 'none');
  ok(`the current place is marked in text and for assistive technology ${tag}`,
    await page.locator('nav.map [aria-current="location"]').count() === 1 && /You are here · 你在这里/.test(await page.locator('nav.map [aria-current="location"]').innerText()));
  const colours = await page.locator('nav.map .place').evaluateAll(els => els.map(el => getComputedStyle(el).borderBottomColor));
  ok(`each place has its own colour ${tag}`, new Set(colours).size === 4);

  // loading state, then the phone
  await page.route('**/hotspots/phone', async route => { await new Promise(r => setTimeout(r, 700)); await route.continue(); });
  await scene.getByRole('button', { name: /Your phone/ }).click();
  await scene.getByText(/Saving this to your notes/).waitFor();
  ok(`a slow save shows "Saving…" and no tick yet ${tag}`, await scene.getByRole('button', { name: /Your phone/ }).locator('.tick').count() === 0);
  await scene.getByText('✓ Added to your notes.').waitFor();
  await page.unroute('**/hotspots/phone');
  ok(`the phone message is shown on a phone screen with its sender ${tag}`,
    /Ivy/.test(await scene.locator('.phone .phone-head').innerText()));
  ok(`the full message text is on the phone ${tag}`, /I could send you my lecture notes by tonight/.test(await scene.locator('.phone .sms').innerText()));
  await noOverflow('phone');
  await shot('01-phone');

  // expression book: pattern first, real words marked, context folded
  const phrase = side('phrasebook').locator('.phrase').first();
  ok(`the expression book shows the pattern first ${tag}`, (await phrase.locator('.phrase-pattern').innerText()) === 'I could … by …');
  ok(`only the pattern's own words are highlighted in the real sentence ${tag}`,
    JSON.stringify(await phrase.locator('mark').allInnerTexts()) === JSON.stringify(['I could', 'by']));
  ok(`the sentence is shown, the whole message is folded ${tag}`,
    /send you my lecture notes/.test(await phrase.locator('.phrase-snippet').innerText()) && !(await phrase.locator('.phrase-context').isVisible()));
  await phrase.getByText(/Full context/).click();
  ok(`opening the context shows where it was met ${tag}`, /Your phone · Ivy/.test(await phrase.locator('.phrase-context').innerText()));
  ok(`the expression book has a short Chinese function and never says "mastered" ${tag}`,
    /提出替代方案/.test(await phrase.innerText()) && /遇到过 ≠ 已掌握/.test(await side('phrasebook').innerText()) && !/mastered|已掌握了/.test(await side('phrasebook').innerText()));

  // folding never hides save errors or required actions
  await page.route('**/hotspots/syllabus', route => route.abort('connectionfailed'));
  await side('notes').locator('summary').click();
  ok(`folded notes still show the count and the unexplored line ${tag}`, /Not explored yet/.test(await side('notes').innerText()) && !(await side('notes').getByText(/Ivy offered/).isVisible()));
  await scene.getByRole('button', { name: /CS101 syllabus/ }).click();
  await scene.getByText('Not saved.').waitFor();
  ok(`a failed save is shown in the scene even with the sidebar folded ${tag}`, await scene.getByRole('button', { name: 'Retry saving' }).isVisible());
  await shot('02-save-failed-folded');
  await page.unroute('**/hotspots/syllabus');
  await scene.getByRole('button', { name: 'Retry saving' }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  await side('notes').locator('summary').click();
  ok(`unfolding the notes shows the new note ${tag}`, /prior approval/.test(await side('notes').innerText()));

  // Ivy (round 9: the coffee corner is a story scene; same script, questions and unlock order)
  await page.locator('nav.map').getByRole('button', { name: /Coffee corner/ }).click();
  await scene.getByRole('heading', { name: 'Coffee corner' }).waitFor();
  await scene.getByRole('button', { name: /Ivy/ }).click();
  await scene.locator('.dialogue .line').getByText(/he likes concrete plans/).waitFor();
  ok(`Ivy appears as a placeholder figure with her name on the dialogue ${tag}`, (await scene.locator('.dialogue .name-tag').innerText()) === 'Ivy' && await scene.locator('.stage-figure .figure-art').isVisible());
  ok(`the follow-up is a disabled choice until the first question is asked ${tag}`, await scene.getByRole('button', { name: /What did you ask for/ }).isDisabled());
  await scene.getByRole('button', { name: /Have you ever asked him/ }).click();
  await scene.locator('.dialogue .line').getByText(/I emailed him before/).waitFor();
  ok(`the learner's question is shown as theirs, followed by Ivy's reply ${tag}`,
    /Have you ever asked him for more time/.test(await scene.locator('.you-asked').innerText()) && await scene.getByRole('button', { name: /Have you ever asked him/ }).count() === 0);
  await scene.getByRole('button', { name: /What did you ask for/ }).click();
  await scene.locator('.dialogue .line').getByText(/kept it polite/).waitFor();
  await scene.getByRole('button', { name: /Earlier/ }).click();
  await scene.getByRole('button', { name: /Earlier/ }).click();
  ok(`both questions and answers are kept in order and can be read again ${tag}`,
    /he likes concrete plans/.test(await scene.locator('.dialogue .line').innerText()) && /1 \/ 3/.test(await scene.locator('.line-nav').innerText()));
  await noOverflow('ivy');
  await shot('03-ivy');

  // overheard moment, paper, writing page
  await page.locator('nav.map').getByRole('button', { name: /Outside Prof/ }).click();
  await scene.getByRole('button', { name: /Listen at the door/ }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  ok(`the overheard moment names the speaker and shows the line as speech ${tag}`, /A student, leaving/.test(await scene.locator('.overheard').innerText()));
  await page.waitForFunction(() => /4\/4/.test(document.querySelector('.phrasebook summary')?.textContent ?? ''));
  const marks = await side('phrasebook').locator('mark').allInnerTexts();
  ok(`all four met patterns are highlighted in their sentences ${tag}`, marks.includes("I'll make sure") && marks.includes('Would it be possible to'));
  await page.getByRole('button', { name: /Email him before you knock/ }).click();
  await page.getByLabel('Email to Prof. Chen').waitFor();
  await noOverflow('writing');
  ok(`the writing page keeps the notes and expression book beside the editor ${tag}`, /4 of 4 met/.test(await side('phrasebook').innerText()));
  await shot('04-writing');

  // readable contrast for the main text styles
  const pairs = [['.task-goal', 'task goal'], ['.task-situation', 'situation'], ['.note-list li', 'notes'], ['.phrase-pattern', 'pattern'],
    ['.phrase-snippet mark', 'highlight'], ['.unexplored', 'muted note']];
  for (const [selector, name] of pairs) {
    const ratio = await contrastOf(page.locator(selector).first());
    ok(`${name} text contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.locator('.campus').waitFor();
  for (const [selector, name] of [['.here-badge', 'you-are-here badge'], ['.scene-text p', 'place description'], ['.hotspot .kind-zh', 'Chinese action label'], ['.place-meta', 'place progress']]) {
    const ratio = await contrastOf(page.locator(selector).first());
    ok(`${name} contrast ${ratio.toFixed(1)}:1 ≥ 4.5:1 ${tag}`, ratio >= 4.5);
  }
  await context.close();
}
ok('no horizontal overflow at 1366×768 or 1920×1080', overflow.length === 0 || (console.log(overflow), false));
ok('no browser page errors', pageErrors.length === 0);
writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: checks.length, checks, overflow, pageErrors, origin, mock_only: true }, null, 2));
console.log(`${checks.length} round-8 UI checks passed. Evidence: ${reportDir}`);
await browser.close();
