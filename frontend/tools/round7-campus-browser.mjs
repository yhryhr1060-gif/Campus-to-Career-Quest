// Run against backend/tools/round6_acceptance_server.py, never the ordinary server.
// Round 12: selectors updated for the scene layout that all four places now use (same journey, same checks).
// node frontend/tools/round7-campus-browser.mjs [http://127.0.0.1:18746]
// Optional: PLAYWRIGHT_MODULE_PATH points to an already installed playwright package.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const origin = process.argv[2] ?? 'http://127.0.0.1:18746';
assert.equal(new URL(origin).hostname, '127.0.0.1', 'The browser checks only support the isolated loopback server');
const reportDir = path.join(root, 'output/playwright', `round7-campus-${new Date().toISOString().replace(/[:.]/g, '-')}`);
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
const checks = [], pageErrors = [];
function ok(name, condition = true) { assert.ok(condition, name); checks.push(name); console.log(`PASS ${name}`); }
const context = await browser.newContext({ viewport: { width: 1365, height: 1000 }, baseURL: origin });
await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
const page = await context.newPage();
page.on('pageerror', e => pageErrors.push(e.message));
page.setDefaultTimeout(15000);
const sid = () => page.evaluate(() => localStorage.getItem('ccq_session_id'));
const state = async () => page.request.get(`/api/sessions/${await sid()}`).then(r => r.json());
const shot = (name) => page.screenshot({ path: path.join(reportDir, `${name}.png`), fullPage: true });
const side = (cls) => page.locator(`aside.sidebar .${cls}`);
// Simulated network failure: the request never reaches the server, as when the connection drops.
const fail = (pattern) => page.route(pattern, route => route.abort('connectionfailed'));
const restore = (pattern) => page.unroute(pattern);
const scene = page.locator('.scene-now');
const place = (name) => page.locator('nav.map').getByRole('button', { name });
const EMAIL = "Dear Prof. Chen,\n\nUnfortunately, I have been sick for three days, so I haven't been able to finish the project report by Friday. Would it be possible to hand it in next Wednesday?\n\nThanks,\nMika";

try {
  // ---- nickname
  await page.goto(origin);
  await page.getByRole('button', { name: /Enter the campus/ }).waitFor();
  const nameBox = page.getByLabel(/Your game nickname/);
  ok('new game opens on the nickname screen with the default nickname', await nameBox.inputValue() === 'Alex');
  ok('nickname screen says where it is saved and asks for no real name', /not your real name/.test(await page.locator('#name-help').innerText()) && /save file on the computer/.test(await page.locator('#name-help').innerText()));
  await nameBox.fill('Mika!!');
  ok('an unusable nickname is explained and cannot be submitted', await page.getByRole('button', { name: /Enter the campus/ }).isDisabled());
  await nameBox.fill('Mika');
  await fail('**/player');
  await page.getByRole('button', { name: /Enter the campus/ }).click();
  await page.getByText(/Not saved:/).waitFor();
  ok('a failed nickname save keeps the learner on the start screen and says so', (await state()).needs_name === true && await page.getByRole('button', { name: /Enter the campus/ }).isVisible());
  await restore('**/player');
  await shot('01-nickname');
  await page.getByRole('button', { name: /Enter the campus/ }).click();

  // ---- start at the desk
  await page.locator('.campus').waitFor();
  ok('the game starts at the library desk with the story opening', await place(/Library/).getAttribute('aria-current') === 'location' && await page.locator('.caption-intro').isVisible());
  ok('the nickname is shown and four places can be chosen', /Mika/.test(await side('settings').innerText()) && await page.locator('nav.map button').count() === 4);
  ok('nothing is in the notes or the expression book before exploring', /noted here once it is saved/.test(await side('notes').innerText()) && /0 of 4 met/.test(await side('phrasebook').innerText()));
  ok('unmet expressions are not listed in the expression book', !/Would it be possible|I could|make sure|Unfortunately/.test(await side('phrasebook').innerText()));
  await shot('02-desk-start');

  // ---- writing is open at once, and back again
  await page.getByRole('button', { name: /Write to Prof. Chen/ }).click();
  await page.getByLabel('Email to Prof. Chen').waitFor();
  ok('writing is open before anything has been explored', (await state()).notes.length === 0);
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.locator('.campus').waitFor();

  // ---- looking at things; notes only after a successful save
  await scene.getByRole('button', { name: /Your phone/ }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  ok('an opened object is noted after the save succeeds', /Ivy offered her lecture notes/.test(await side('notes').innerText()));
  ok('an expression met in context appears in the expression book with its line', /I could … by …/.test(await side('phrasebook').innerText()) && /I could send you my lecture notes by tonight/.test(await side('phrasebook').innerText()));
  await fail('**/hotspots/**');
  await scene.getByRole('button', { name: /CS101 syllabus/ }).click();
  await scene.getByText('Not saved.').waitFor();
  ok('a failed save says "Not saved" and never "Added to your notes"', await scene.locator('.dialogue').getByText('✓ Added to your notes.').count() === 0 && /not saved/.test(await scene.getByRole('button', { name: /CS101 syllabus/ }).innerText()));
  ok('the unsaved clue is not in the notes and has no tick', !/prior approval/.test(await side('notes').innerText()) && await scene.getByRole('button', { name: /CS101 syllabus/ }).locator('.tick').count() === 0 && !(await state()).notes.includes('syllabus'));
  await shot('03-save-failed');
  await restore('**/hotspots/**');
  await scene.getByRole('button', { name: 'Retry saving' }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  ok('retrying the save adds the note', /prior approval/.test(await side('notes').innerText()) && (await state()).notes.includes('syllabus'));

  // ---- a person with pre-written questions
  await place(/Coffee corner/).click();
  await scene.getByRole('heading', { name: 'Coffee corner' }).waitFor();
  ok('the questions for a person are closed until the first chat is saved', await scene.getByRole('button', { name: /Have you ever asked him/ }).count() === 0);
  await scene.getByRole('button', { name: /Ivy/ }).click();
  await scene.getByRole('button', { name: /Have you ever asked him/ }).waitFor();
  ok('the follow-up question waits for the first one', await scene.getByRole('button', { name: /What did you ask for/ }).isDisabled());
  await scene.getByRole('button', { name: /Have you ever asked him/ }).click();
  await scene.getByText(/I emailed him before I went to see him/).waitFor();
  await scene.getByRole('button', { name: /What did you ask for/ }).click();
  await scene.getByText(/Would it be possible to hand it in on Monday instead/).waitFor();
  await page.waitForFunction(() => /3 of 4 met/.test(document.querySelector('.phrasebook')?.textContent ?? ''));
  ok('scripted answers are recorded as notes and expressions met', (await state()).notes.filter(n => n.startsWith('ivy')).length === 3);
  await shot('04-ivy-dialogue');

  // ---- a place change that could not be saved
  await fail('**/location');
  await place(/CS corridor/).click();
  await page.getByText('Your place on campus was not saved').waitFor();
  ok('a failed place save is reported and the server still has the old place', (await state()).location === 'cafe');
  await restore('**/location');
  await page.locator('.save-line-error').getByRole('button', { name: 'Retry' }).click();
  await page.waitForFunction(() => !document.querySelector('.save-line-error'));
  ok('retrying saves the new place', (await state()).location === 'corridor');
  await scene.getByRole('button', { name: /Notice board/ }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  await place(/Outside Prof. Chen/).click();
  await scene.getByRole('button', { name: /Listen at the door/ }).click();
  await scene.getByText('✓ Added to your notes.').waitFor();
  await page.waitForFunction(() => /4 of 4 met/.test(document.querySelector('.phrasebook')?.textContent ?? ''));
  ok('all four expressions can be met in context, and are labelled as optional exposure', /does not mean you have learned it/.test(await side('phrasebook').innerText()));
  await shot('05-office-door-all-met');

  // ---- refresh restores place, notes, expressions and nickname
  await page.reload();
  await page.locator('.campus').waitFor();
  ok('refresh restores the place', await place(/Outside Prof. Chen/).getAttribute('aria-current') === 'location');
  ok('refresh restores notes, expressions met and nickname', /Office hours/.test(await side('notes').innerText()) && /4 of 4 met/.test(await side('phrasebook').innerText()) && /Mika/.test(await side('settings').innerText()));
  ok('unexplored things are reported as a count, not as content', /Not explored yet · 尚未探索: 1 thing/.test(await side('task').innerText()) && !/introduction and a list of headings/.test(await page.locator('aside.sidebar').innerText()));

  // ---- campus <-> writing with a draft
  await scene.getByRole('button', { name: /Write to Prof. Chen/ }).click();
  const editor = page.getByLabel('Email to Prof. Chen');
  await editor.fill('Dear Prof. Chen, I am writing about the report.');
  await page.getByRole('button', { name: /Back to campus/ }).click();          // at once, before the timed autosave
  await page.locator('.campus').waitFor();
  ok('leaving the editor writes the newest draft to the server first', (await state()).prep.draft === 'Dear Prof. Chen, I am writing about the report.');
  ok('notes and expressions are still there when writing', /Continue your email/.test(await page.locator('.stage-write').innerText()));
  await page.getByRole('button', { name: /Continue your email/ }).click();
  ok('the draft is back in the editor', await editor.inputValue() === 'Dear Prof. Chen, I am writing about the report.');
  ok('notes and the expression book can be read while writing', /Office hours/.test(await side('notes').innerText()) && /Would it be possible to …\?/.test(await side('phrasebook').innerText()));
  await shot('06-writing-with-notes');
  await fail('**/prep/draft');
  await editor.fill(EMAIL);
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.getByText(/saving did not finish/).waitFor();
  ok('when the draft cannot be saved the learner stays in the editor with the text and a clear message', await editor.inputValue() === EMAIL && (await state()).phase === 'prepare' && (await state()).prep.draft !== EMAIL);
  ok('the save status does not claim success', !/Saved on server/.test(await page.locator('.save-status').innerText()));
  await shot('07-draft-save-failed');
  await restore('**/prep/draft');
  await page.reload();
  await editor.waitFor();
  ok('after a refresh the unsaved text is recovered from this browser and saved', await editor.inputValue() === EMAIL);
  await page.waitForFunction(() => /Saved on server/.test(document.querySelector('.save-status')?.textContent ?? ''));
  ok('the recovered draft reaches the server', (await state()).prep.draft === EMAIL);

  // ---- send, summary, still free to explore, then the office
  await page.getByRole('button', { name: 'Send email', exact: true }).click();
  if (await page.getByRole('button', { name: /Send anyway/ }).count()) await page.getByRole('button', { name: /Send anyway/ }).click();
  await page.getByText('Prof. Chen will read these points.', { exact: true }).waitFor();
  ok('the existing email summary step follows sending', await page.getByRole('button', { name: 'Confirm', exact: true }).isVisible());
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.getByRole('button', { name: /Go to Prof. Chen's office/ }).waitFor();
  await page.getByRole('button', { name: /Back to campus/ }).click();
  await page.locator('.campus').waitFor();
  ok('the campus is still open after the summary is confirmed', /Get ready for the office/.test(await page.locator('.stage-write').innerText()));
  await page.getByRole('button', { name: /Get ready for the office/ }).click();
  await page.getByRole('button', { name: /Go to Prof. Chen's office/ }).click();
  const reply = page.getByLabel('Your reply', { exact: true });
  await reply.waitFor();
  await page.getByText(/Ah, Mika, come in/).waitFor();
  ok('Prof. Chen greets the learner by nickname');
  ok('campus and writing are closed once the negotiation has started', await page.getByRole('button', { name: /1 Explore/ }).isDisabled() && await page.getByRole('button', { name: /2 Prepare/ }).isDisabled());
  const refused = await page.request.post(`/api/sessions/${await sid()}/location`, { data: { area: 'cafe' } });
  ok('the server also refuses campus moves during the negotiation', refused.status() === 409);
  await shot('08-negotiation');
  const voice = page.getByRole('checkbox', { name: 'voice', exact: true });
  if (await voice.count()) await voice.uncheck();
  const say = async (text) => {
    await reply.fill(text);
    const response = page.waitForResponse(r => r.url().endsWith('/negotiate/turn'));
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    assert.ok((await response).ok());
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Your reply"]')?.disabled);
  };
  await say("Yes. Unfortunately, I've been sick since Monday, so I haven't been able to work on it.");
  await say('I could send you the outline by Friday and the full report by Sunday evening.');
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.getByRole('button', { name: /Leave the office/ }).click();
  await page.getByRole('heading', { name: /Consequences/ }).waitFor();
  const outcome = await page.request.get(`/api/sessions/${await sid()}/outcome`).then(r => r.json());
  ok('the journey from the campus reaches an agreed ending', outcome.ending.id === 'staged');
  await shot('09-ending');
  ok('no browser page errors', pageErrors.length === 0);
  writeFileSync(path.join(reportDir, 'result.json'), JSON.stringify({ passed: checks.length, checks, pageErrors, origin, mock_only: true }, null, 2));
  console.log(`${checks.length} campus browser checks passed. Evidence: ${reportDir}`);
} catch (error) {
  await shot('failure').catch(() => {});
  console.error(`FAILED after ${checks.length} checks: ${error.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
