"""Isolated, loopback-only mock acceptance server. Never loads .env or real saved games.

Run from the project root:
    backend\\.venv\\Scripts\\python.exe backend\\tools\\round6_acceptance_server.py
Open http://127.0.0.1:18746 and /api/__test in a second tab for fault controls.
This harness is not imported by the normal application and adds no production routes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextvars import ContextVar
from datetime import datetime
import os
from pathlib import Path
import re
import sys
from threading import Lock
import time

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=18746)
parser.add_argument("--data-dir", type=Path)
args = parser.parse_args()
runtime_root = (ROOT / "reports" / "round6-runtime").resolve()
data_dir = (args.data_dir or runtime_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")).resolve()
if not data_dir.is_relative_to(runtime_root) or data_dir == runtime_root:
    parser.error("--data-dir must be a child of this project's reports/round6-runtime directory")
data_dir.mkdir(parents=True, exist_ok=True)
os.environ.update(CCQ_SKIP_DOTENV="1", LLM_MOCK="1", LLM_API_KEY="",
                  LLM_BASE_URL="http://127.0.0.1:1", DATA_DIR=str(data_dir), ASR_BACKEND="disabled")
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from app.llm import client  # noqa: E402
from app.llm.schemas import DraftFeedbackOut, FeedbackReviewOut, RecheckOut  # noqa: E402
from app.main import app  # noqa: E402
from app.services import writing_feedback  # noqa: E402


def no_live_transport(*_a, **_k):
    raise AssertionError("Live model transport is disabled by the round-6 acceptance harness")


client.http_transport = no_live_transport
_real_call = writing_feedback.call_json
_sid = ContextVar("round6_acceptance_sid", default="")
_lock = Lock()
_scenarios: dict[str, dict] = {}
_counts: dict[str, Counter] = {}
SCENARIOS = ("normal", "second_review_fail_once", "missing_review_once", "recheck_missing_once",
             "invalid_quote_once", "unsure", "cap", "context")
CAP_PAIRS = list(zip(
    "accomodate recieve seperate definately occured adress begining beleive enviroment goverment necesary untill wich writting".split(),
    "accommodate receive separate definitely occurred address beginning believe environment government necessary until which writing".split()))
CAP_DRAFT = "Dear Prof. Chen, I wrote these words in my report: " + ", ".join(a for a, _ in CAP_PAIRS) + ". Thank you, Alex."
CONTEXT_DRAFT = "Dear Prof. Chen, the report and the outline are due today. It is late. Could I have more time? Thanks, Alex."
CONTEXT_REVISED = "Dear Prof. Chen, the report is due today. It is late. Could I have more time? Thanks, Alex."


@app.middleware("http")
async def acceptance_scope(request: Request, call_next):
    if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "This acceptance server only accepts local requests")
    match = re.search(r"/sessions/([^/]+)", request.url.path)
    token = _sid.set(match.group(1) if match else "")
    try:
        return await call_next(request)
    finally:
        _sid.reset(token)


def _controlled_call(schema, messages, **kwargs):
    sid, name = _sid.get(), schema.__name__
    with _lock:
        controls = _scenarios.get(sid, {"scenario": "normal", "delay_ms": 0})
        scenario = controls["scenario"]
        counts = _counts.setdefault(sid, Counter())
        counts[name] += 1
        counts[f"{scenario}:{name}"] += 1
        attempt = counts[f"{scenario}:{name}"]
    if controls["delay_ms"]:
        time.sleep(controls["delay_ms"] / 1000)
    original_mock = kwargs["mock"]
    if schema is FeedbackReviewOut and scenario == "second_review_fail_once" and attempt == 1:
        return client.LLMResult(data=None, model="mock", prompt_version=kwargs["prompt_version"],
                                errors=["Synthetic second-reading failure"], execution_mode="mock")
    if schema is FeedbackReviewOut and scenario == "missing_review_once" and attempt == 1:
        kwargs["mock"] = lambda: {"reviews": original_mock()["reviews"][:-1]}
    if schema is DraftFeedbackOut and scenario == "cap":
        kwargs["mock"] = lambda: {"items": [dict(type="spelling", judgement="clear_error", serious=False,
            quote=a, suggestion=b, reason=f"The conventional spelling of this sample word is {b}.") for a, b in CAP_PAIRS]}
    if schema is DraftFeedbackOut and scenario == "context":
        kwargs["mock"] = lambda: {"items": [dict(type="accuracy", judgement="expression_issue", serious=False,
            quote="It is late.", suggestion="The report is late.", reason="Two preceding nouns make the referent of It ambiguous.")]}
    if schema is RecheckOut:
        def recheck_output():
            output = original_mock()
            if scenario == "recheck_missing_once" and attempt == 1:
                return {"results": []}
            if scenario == "invalid_quote_once" and attempt == 1:
                for row in output["results"]:
                    row.update(status="resolved", current_quote="THESE WORDS ARE NOT IN THE DRAFT")
            if scenario == "unsure":
                for row in output["results"]:
                    row.update(status="unclear", current_quote="", note="This synthetic model cannot determine the intended referent.")
            if scenario == "context":
                for row in output["results"]:
                    row.update(status="resolved", current_quote="the report is due today. It is late.",
                               note="The revised context identifies one report; the pronoun sentence itself is unchanged.")
            return output
        kwargs["mock"] = recheck_output
    return _real_call(schema, messages, **kwargs)


writing_feedback.call_json = _controlled_call


class Controls(BaseModel):
    scenario: str
    delay_ms: int = Field(default=0, ge=0, le=5000)


@app.post("/api/__test/sessions/{sid}/scenario")
def set_scenario(sid: str, body: Controls):
    if body.scenario not in SCENARIOS:
        raise HTTPException(422, "Unknown mock scenario")
    with _lock:
        _scenarios[sid] = body.model_dump()
        counts = _counts.setdefault(sid, Counter())
        for key in list(counts):
            if key.startswith(body.scenario + ":"):
                del counts[key]
    return {"mock_only": True, "scenario": body.scenario}


@app.get("/api/__test/sessions/{sid}/counts")
def counts(sid: str):
    with _lock:
        return {"mock_only": True, "operations": dict(_counts.get(sid, {})),
                "scenario": _scenarios.get(sid, {"scenario": "normal"})}


@app.get("/api/__test/info")
def info():
    return {"mock_only": True, "dotenv_disabled": True, "live_transport_disabled": True,
            "data_dir": str(data_dir), "scenarios": SCENARIOS,
            "samples": {"cap": CAP_DRAFT, "context": CONTEXT_DRAFT, "context_revised": CONTEXT_REVISED}}


@app.get("/api/__test", response_class=HTMLResponse)
def controls_page():
    return """<!doctype html><meta charset="utf-8"><title>Round 6 mock controls</title>
<style>body{font:17px system-ui;max-width:850px;margin:40px auto;padding:20px}button,select,input{font:inherit;margin:6px;padding:8px}pre{white-space:pre-wrap}</style>
<h1>Round 6 · 隔离 mock 验收</h1><p>此页面只控制这个独立验收服务。不会读取 .env、真实存档或调用真实模型。</p>
<p>先在<a href="/" target="_blank">游戏页</a>开始新游戏，再回到这里。选好场景后点“应用”；不要反复应用，否则一次失败会重新计数。</p>
<label>场景 <select id="scenario"></select></label><label>模拟延迟（毫秒）<input id="delay" type="number" min="0" max="5000" value="0"></label>
<button id="apply">应用到当前试玩</button><button id="counts">查看调用计数</button><pre id="status"></pre>
<p>second_review_fail_once：首次二次检查失败；missing_review_once：首次漏掉一条二次意见；recheck_missing_once：修改后首次复核漏答；invalid_quote_once：首次复核引用不存在；unsure：有效的“无法判断”；cap：14 条模拟拼写问题；context：上下文变化消除指代歧义。</p>
<p>失败后直接回游戏点 Retry，不要重新应用场景。DraftFeedbackOut 计数应保持 1，FeedbackReviewOut 增加到 2。</p><h2>固定测试样例（仅验证流程）</h2><pre id="samples"></pre>
<script>
const sid=()=>localStorage.getItem('ccq_session_id');
const status=document.querySelector('#status');
fetch('/api/__test/info').then(r=>r.json()).then(x=>{for(const s of x.scenarios){const o=document.createElement('option');o.value=s;o.textContent=s;scenario.append(o)}samples.textContent=JSON.stringify(x.samples,null,2)});
document.querySelector('#apply').onclick=async()=>{if(!sid()){status.textContent='请先打开游戏页。';return}const r=await fetch('/api/__test/sessions/'+sid()+'/scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:scenario.value,delay_ms:Number(delay.value)})});status.textContent=JSON.stringify(await r.json(),null,2)};
document.querySelector('#counts').onclick=async()=>{const r=await fetch('/api/__test/sessions/'+sid()+'/counts');status.textContent=JSON.stringify(await r.json(),null,2)};
</script>"""


dist = ROOT / "frontend" / "dist"
if not (dist / "index.html").is_file():
    parser.error("Build the frontend first: cd frontend then npm.cmd run build")
app.mount("/", StaticFiles(directory=str(dist), html=True), name="acceptance_frontend")

if __name__ == "__main__":
    import uvicorn
    print(f"Mock-only game: http://127.0.0.1:{args.port}", flush=True)
    print(f"Fault controls: http://127.0.0.1:{args.port}/api/__test", flush=True)
    print(f"Isolated synthetic saves: {data_dir}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
