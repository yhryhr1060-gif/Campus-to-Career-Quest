// Run against backend/tools/round6_acceptance_server.py, never the ordinary server.
// node frontend/tools/round6-browser.mjs [http://127.0.0.1:18746]
// Optional: PLAYWRIGHT_MODULE_PATH points to an already installed playwright package.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const origin = process.argv[2] ?? 'http://127.0.0.1:18746';
assert.equal(new URL(origin).hostname, '127.0.0.1', 'The browser checks only support the isolated loopback server');
const reportDir = path.join(root, 'output/playwright', `round6-${new Date().toISOString().replace(/[:.]/g, '-')}`);
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
const checks = [], pageErrors = [], pages = [];
let activePage;
const V1 = "Dear Prof. Chen,\n\nI want to ask for more time for the project report. I has been sick for three days, so I can't finish it. Can I give it to you next Wednesday? Sorry.\n\nThanks,\nAlex";
const V2 = V1.replace('I has been sick', 'I have been sick');
function ok(name, condition = true) { assert.ok(condition, name); checks.push(name); console.log(`PASS ${name}`); }
async function body(page) { return page.locator('body').innerText(); }
async function sid(page) { return page.evaluate(() => localStorage.getItem('ccq_session_id')); }
async function state(page) { return page.request.get(`/api/sessions/${await sid(page)}`).then(r => r.json()); }
async function counts(page) { return page.request.get(`/api/__test/sessions/${await sid(page)}/counts`).then(r => r.json()).then(x => x.operations); }
async function scenario(page, name, delay_ms = 0) {
  const response = await page.request.post(`/api/__test/sessions/${await sid(page)}/scenario`, { data: { scenario: name, delay_ms } });
  assert.ok(response.ok(), `set scenario ${name}`);
}
// Round 7 added a nickname screen and the campus; these checks start at the editor as before.
async function enterAndWrite(page) {
  await page.getByRole('button', { name: /Enter the campus/ }).click();
  await page.getByRole('button', { name: /Write to Prof. Chen/ }).click();
}
async function newGame(name = 'normal') {
  const context = await browser.newContext({ viewport: { width: 1365, height: 1000 }, baseURL: origin });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage(); activePage = page; pages.push(page);
  page.on('pageerror', e => pageErrors.push(e.message));
  page.setDefaultTimeout(15000);
  await page.goto(origin);
  await enterAndWrite(page);
  await page.getByLabel('Email to Prof. Chen').waitFor();
  await scenario(page, name);
  return page;
}
async function draft(page, text) { await page.getByLabel('Email to Prof. Chen').fill(text); }
async function action(page, button, endpoint) {
  const response = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === 'POST');
  await page.getByRole('button', { name: button }).click();
  const result = await response;
  assert.ok(result.ok(), `${endpoint} returned ${result.status()}`);
  const json = await result.json();
  await page.waitForFunction(() => !document.querySelector('textarea.email')?.disabled);
  return json;
}
async function check(page) { return action(page, /Check this draft|Full check of this version/, '/prep/feedback'); }
async function reveal(page) {
  while (await page.getByRole('button', { name: /Show next/ }).count()) await page.getByRole('button', { name: /Show next/ }).click();
}
async function say(page, text) {
  await page.getByLabel('Your reply', { exact: true }).fill(text);
  const response = page.waitForResponse(r => r.url().endsWith('/negotiate/turn'));
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  assert.ok((await response).ok());
  await page.getByLabel('Your reply', { exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Your reply"]')?.disabled);
}
async function finish(page, ending) {
  await page.getByRole('button', { name: 'Send email', exact: true }).click();
  if (await page.getByRole('button', { name: /Send anyway/ }).count()) await page.getByRole('button', { name: /Send anyway/ }).click();
  await page.getByText('Prof. Chen will read these points.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.getByRole('button', { name: /Go to Prof. Chen's office/ }).click();
  await page.getByLabel('Your reply', { exact: true }).waitFor();
  const voice = page.getByRole('checkbox', { name: 'voice', exact: true });
  if (await voice.count()) await voice.uncheck();
  if (ending === 'staged') {
    await say(page, "Yes. Unfortunately, I've been sick since Monday, so I haven't been able to work on it.");
    await say(page, 'I could send you the outline by Friday and the full report by Sunday evening.');
  } else {
    await say(page, "I'm sorry, I've been ill.");
    await say(page, 'I could finish the whole report by Saturday at noon.');
  }
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.getByRole('button', { name: /Leave the office/ }).click();
  await page.getByRole('heading', { name: /Consequences/ }).waitFor();
  await page.getByText(`Ending · ${ending === 'staged' ? 'B' : 'A'}`, { exact: true }).waitFor();
  const outcome = await page.request.get(`/api/sessions/${await sid(page)}/outcome`).then(r => r.json());
  ok(`full browser journey reaches ${ending} ending`, outcome.ending.id === ending);
  ok(`${ending} ending keeps the alternative face down`, outcome.other_ending.face_down === true);
  await page.screenshot({ path: path.join(reportDir, `${ending}-ending.png`), fullPage: true });
}

try {
  const page = await newGame('second_review_fail_once');
  await draft(page, V1);
  await check(page);
  await page.getByRole('button', { name: /Retry second reading/ }).waitFor();
  const before = await state(page);
  ok('failed second reading preserves first-pass points on version 1', before.prep.versions[0].feedback.items.length >= 3 && before.prep.versions.length === 1);
  ok('failure has an explicit same-draft retry control', await page.getByRole('button', { name: /Retry second reading/ }).isEnabled());
  await page.screenshot({ path: path.join(reportDir, 'second-reading-failed.png'), fullPage: true });
  await page.reload();
  await page.getByRole('button', { name: /Retry second reading/ }).waitFor();
  ok('refresh preserves the failed second-reading retry control', (await counts(page)).DraftFeedbackOut === 1);
  await action(page, /Retry second reading/, '/prep/feedback');
  const after = await state(page), callCounts = await counts(page);
  ok('same-draft retry calls only the second reader', callCounts.DraftFeedbackOut === 1 && callCounts.FeedbackReviewOut === 2);
  ok('retry retains version and item identities', after.prep.versions.length === 1 && JSON.stringify(after.prep.versions[0].feedback.items.map(x => x.id)) === JSON.stringify(before.prep.versions[0].feedback.items.map(x => x.id)));
  await reveal(page);
  await page.getByRole('button', { name: /Keep my wording/ }).last().click();
  await page.getByText(/You kept your wording/).first().waitFor();
  await draft(page, V2);
  await page.getByText(/an earlier version/).first().waitFor();
  ok('editing keeps old feedback with an earlier-version label', (await body(page)).includes('Feedback on version 1'));
  await action(page, /Recheck earlier points/, '/prep/recheck');
  await page.reload();
  await page.getByText(/rechecked against your current text/).waitFor();
  await reveal(page);
  ok('refresh restores revised draft, version history and kept choice', await page.getByLabel('Email to Prof. Chen').inputValue() === V2 && (await body(page)).includes('You kept your wording'));
  await page.getByRole('button', { name: 'Send email', exact: true }).click();
  await page.getByRole('alertdialog', { name: 'Before you send' }).waitFor();
  ok('learner can send with open feedback', await page.getByRole('button', { name: /Send anyway/ }).isEnabled());
  await page.getByRole('button', { name: /Keep editing/ }).click();
  await finish(page, 'staged');

  for (const failure of ['missing_review_once', 'recheck_missing_once', 'invalid_quote_once', 'unsure']) {
    const p = await newGame(failure); await draft(p, V1); await check(p);
    if (failure === 'missing_review_once') {
      await p.getByRole('button', { name: /Retry second reading/ }).waitFor();
      const failed = (await state(p)).prep.versions[0].feedback;
      ok('missing second-reader answer is retryable', failed.review_retryable === true);
      await action(p, /Retry second reading/, '/prep/feedback');
      const c = await counts(p);
      ok('partial review retry preserves first pass', c.DraftFeedbackOut === 1 && c.FeedbackReviewOut === 2);
    } else {
      await draft(p, V2); await action(p, /Recheck earlier points/, '/prep/recheck');
      if (failure === 'unsure') {
        ok('valid uncertainty is shown as cannot judge', (await body(p)).includes("Can't tell"));
        ok('valid uncertainty is not offered as a technical retry', await p.getByRole('button', { name: /Recheck earlier points/ }).isDisabled() && await p.getByRole('button', { name: /Retry recheck/ }).count() === 0);
      } else {
        await p.getByRole('button', { name: /Retry recheck/ }).waitFor();
        ok(`${failure} exposes retry instead of completed assessment`, (await body(p)).includes('Recheck incomplete'));
        await action(p, /Retry recheck/, '/prep/recheck');
        ok(`${failure} retries the same revised version`, (await state(p)).prep.versions.length === 2);
      }
    }
  }

  const cap = await newGame('cap'); await draft(cap, info.samples.cap); await check(cap);
  await cap.getByText(/Feedback may be incomplete/).waitFor();
  await reveal(cap);
  const capFeedback = (await state(cap)).prep.versions[0].feedback;
  ok('more than 12 feedback points has a visible incomplete notice', capFeedback.items.length === 12 && capFeedback.possibly_incomplete === true && capFeedback.over_limit_count === 2);
  ok('cap does not duplicate displayed issues', new Set(capFeedback.items.map(x => x.quote)).size === 12);
  await cap.screenshot({ path: path.join(reportDir, 'feedback-cap.png'), fullPage: true });

  const context = await newGame('context'); await draft(context, info.samples.context); await check(context);
  await draft(context, info.samples.context_revised); await action(context, /Recheck earlier points/, '/prep/recheck');
  const contextResult = (await state(context)).prep.versions.at(-1).recheck.results[0];
  ok('changed context can resolve an unchanged pronoun sentence', contextResult.status === 'resolved' && (await counts(context)).RecheckOut === 1);

  const mechanics = await newGame();
  const mechanicalDraft = 'Dear Prof. Chen, i am writing about the report,, could I recieve more time until Sunday? Thanks, Alex.';
  await draft(mechanics, mechanicalDraft); await check(mechanics); await reveal(mechanics);
  const mechanicsFeedback = (await state(mechanics)).prep.versions[0].feedback;
  ok('spelling, punctuation and capitalization are distinct feedback types', ['spelling', 'punctuation', 'capitalization'].every(type => mechanicsFeedback.items.some(x => x.type === type)));
  ok('feedback never silently rewrites the learner draft or deadline', await mechanics.getByLabel('Email to Prof. Chen').inputValue() === mechanicalDraft);
  await mechanics.screenshot({ path: path.join(reportDir, 'mechanics.png'), fullPage: true });

  const duplicate = await newGame('normal'); await scenario(duplicate, 'normal', 300); await draft(duplicate, V1);
  const duplicateResponse = duplicate.waitForResponse(r => r.url().endsWith('/prep/feedback'));
  await duplicate.getByRole('button', { name: /Check this draft/ }).evaluate(button => { button.click(); button.click(); });
  await duplicateResponse; await duplicate.getByText(/Feedback on version 1/).waitFor();
  ok('same-tick double click creates one feedback operation', (await counts(duplicate)).DraftFeedbackOut === 1 && (await state(duplicate)).prep.versions.length === 1);

  const stale = await newGame('normal'); await draft(stale, V1);
  let releaseResponse;
  const held = new Promise(resolve => { releaseResponse = resolve; });
  let responseFetched;
  const fetched = new Promise(resolve => { responseFetched = resolve; });
  await stale.route('**/prep/feedback', async route => {
    const response = await route.fetch(); responseFetched(); await held; await route.fulfill({ response });
  });
  await stale.getByRole('button', { name: /Check this draft/ }).click(); await fetched;
  await stale.getByRole('button', { name: /1 Explore/ }).click();
  await stale.getByRole('button', { name: /Write to Prof. Chen|Continue your email/ }).click();
  await draft(stale, V2);
  const delivered = stale.waitForResponse(r => r.url().endsWith('/prep/feedback'));
  releaseResponse();
  await delivered;
  ok('late result from an unmounted Prepare page cannot replace newer text', await stale.getByLabel('Email to Prof. Chen').inputValue() === V2);
  await stale.unroute('**/prep/feedback');
  await check(stale);
  ok('a newer full check stays attached to version 2', (await state(stale)).prep.versions.at(-1).text === V2);

  const full = await newGame(); await draft(full, V1); await check(full); await finish(full, 'full');
  ok('no browser page errors', pageErrors.length === 0);
  writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: true, environment: process.platform,
    node: process.version, origin, data_dir: info.data_dir, checks, pageErrors, mock_only: true }, null, 2));
  console.log(`${checks.length} browser checks passed. Evidence: ${reportDir}`);
} catch (error) {
  if (activePage) await activePage.screenshot({ path: path.join(reportDir, 'failure.png'), fullPage: true }).catch(() => {});
  writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: false, environment: process.platform,
    node: process.version, origin, data_dir: info.data_dir, checks, pageErrors, error: String(error.stack ?? error), mock_only: true }, null, 2));
  console.error(error); console.error(`Evidence: ${reportDir}`); process.exitCode = 1;
} finally { await browser.close(); }
