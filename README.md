# Campus to Career Quest

An AI-assisted English writing and speaking practice game. The current prototype contains Chapter 2:
explore a small campus, write an email to a professor, negotiate an extension, and review the outcome.

## 公开版本说明

本仓库包含可运行的应用源码、合成测试、提示模板和安装脚本，并保留原来的 Git 提交历史及 `docs/` 文档。
这次公开版本使用代码绘制的 SVG 占位素材，**不包含原项目的五张图片**。原本地项目及其图片、配置和存档没有被修改。

不会上传 `.env`、API 密钥、玩家存档、数据库、真实录音、模型、依赖目录、截图、运行输出或历史验收报告。
`.env.example` 只含空密钥和示例配置，默认启用无需 API 的 mock 模式。

## Requirements

- Windows: Python 3.11+ and Node.js 22.18+ (or a current supported Node release).
- The backend uses FastAPI and SQLite; the frontend uses React, TypeScript and Vite.
- Optional local speech recognition needs additional packages and a separately downloaded model.
- No API key, model download or microphone is needed for typed play in mock mode.

## Quick start on Windows

1. Clone this repository and open its folder.
2. Run `setup.cmd` once. It creates `backend/.venv`, installs Python and frontend packages, and copies
   the empty `.env.example` template to `.env` only if `.env` does not already exist.
3. Run `run.cmd`. It opens the backend and frontend, then opens http://127.0.0.1:5173.
4. Keep both server windows open while playing. Close them to stop the game.

In PowerShell, use `.\setup.cmd` and `.\run.cmd`. The scripts use `npm.cmd` and the virtual environment's
Python directly, so changing the system's PowerShell execution policy is not necessary.

Progress is saved locally under `data/` and in browser storage. Do not share that folder or commit it.
The development servers bind to loopback. This prototype is **not configured for public production hosting**.

## Tests and build

After setup, run from the repository root in PowerShell:

```powershell
# Isolated backend tests: no credential file is opened.
$env:CCQ_SKIP_DOTENV = "1"
$env:LLM_MOCK = "1"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q

# Frontend unit tests, type checking and production build.
cd frontend
npm.cmd test
npm.cmd run build
```

The pytest fixtures use temporary storage and mock responses. Frontend tests use Node's native test
runner and TypeScript stripping; the recording tests simulate browser APIs, not a real microphone.
`test.cmd` is a convenience entry point for the backend tests only.

Browser acceptance tools are under `frontend/tools/`. Playwright is a declared development dependency;
install a browser separately with `npx.cmd playwright install chromium` if needed. Build the frontend,
then use `backend/tools/round6_acceptance_server.py` for an isolated, loopback-only mock server. The
current scene check is `node frontend/tools/round12-scenes-browser.mjs`; `round8-ui-browser.mjs` is
retired and intentionally exits by default. Generated reports remain ignored.

## Optional speech and live models

- `setup-speech.cmd` installs optional speech packages and downloads the selected local ASR model
  into `models/`. Review the upstream model's terms before downloading or using it. Model files and
  recordings are not part of this repository. Typed play still works without speech setup.
- `backend/tools/round4_acceptance.py` is an optional real-ASR smoke test. Its three synthetic WAV
  inputs are deliberately not included. Use `--help` for the required filenames and `--fixtures-dir`
  option, and supply your own local synthetic test audio. Missing fixtures produce an explanatory
  error before the app or model is loaded. This check is not required by the ordinary unit tests.
- For a live OpenAI-compatible provider, set `LLM_MOCK=0`, `LLM_BASE_URL`, `LLM_MODEL` and your own
  `LLM_API_KEY` in the ignored `.env`. Provider availability, privacy terms and charges apply.
  Learner text is sent to that provider in live mode; use mock mode for offline synthetic testing.
- `scripts/llm_check.cmd`, `backend/tools/prompt_regression.py`, `feedback_gold_check.py`, and explicitly
  requested live acceptance runs may call a paid API. They are not part of the offline test command.
  The legacy prompt-regression writing branch still targets an older feedback schema; do not treat
  its result as the current writing-feedback acceptance result.

## Layout

- `backend/app/`: API, session state, rules, feedback, local speech, and export services.
- `backend/app/llm/prompts/`: all seven application prompt templates.
- `backend/tests/`, `frontend/tests/`: unit and regression tests using synthetic examples.
- `content/chapter2.yaml`: fictional chapter content and rules.
- `frontend/src/`: interface, audio handling, state, and public SVG placeholders.
- `backend/tools/`, `frontend/tools/`, `scripts/`: local development and acceptance helpers.
- `docs/`: preserved project overview, requirements and architecture documents from the original
  repository. They contain early planning material and may lag behind the current implementation.

## Before publishing further changes

Keep the ignore rules in place and inspect `git diff --cached` before committing. Only stage reviewed
source files. Never force-add secrets, saves, model weights, recordings, reports, or the excluded PNGs.
Public Git history cannot reliably make a leaked secret private again; revoke a leaked credential
with its provider rather than merely deleting the file.

Inter and Source Serif 4 are installed through their `@fontsource` npm packages; retain the upstream
license notices when distributing built assets. No blanket open-source license or rights to the
omitted illustrations are asserted by this publication.
