"""나만 쓰는 영상 다운로더 사이트 (Flask)
환경변수: APP_PASSWORD(필수), SECRET_KEY(권장), cookies.txt(선택, 유튜브 봇 차단 시)
"""
import os, shutil, tempfile, threading, time, uuid
from pathlib import Path
from flask import Flask, request, session, jsonify, send_file, render_template_string, redirect
import yt_dlp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")
PASSWORD = os.environ.get("APP_PASSWORD", "")
BASE = Path(tempfile.gettempdir()) / "ytdl"
BASE.mkdir(exist_ok=True)
JOBS, LOCK = {}, threading.Lock()
SLOTS = threading.Semaphore(2)  # 동시 다운로드 2개까지
KEEP_SECONDS = 30 * 60          # 완료 후 30분 뒤 자동 삭제

FORMATS = {
    "best": "bestvideo+bestaudio/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "mp3": "bestaudio/best",
}

CSS = """
:root{--bg:#eef2f6;--card:#fff;--ink:#14202e;--sub:#5b6b7d;--line:#d3dce6;--accent:#0b5fff;--ok:#12805c;--err:#c22f2f}
@media(prefers-color-scheme:dark){:root{--bg:#0e141b;--card:#161f29;--ink:#e6edf5;--sub:#8fa1b4;--line:#2a3846;--accent:#5b93ff;--ok:#4cc9a0;--err:#ff7b7b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 "Pretendard","Noto Sans KR",system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:32px 16px}
h1{font-size:1.5rem;margin:0 0 4px}
p.sub{margin:0 0 20px;color:var(--sub);font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
input,select,button,a.btn{font:inherit;color:inherit}
input[type=text],input[type=password]{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
.row{display:flex;gap:8px;margin-top:10px}
select{flex:1;padding:11px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
button,a.btn{display:inline-block;padding:11px 18px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;text-decoration:none}
button:disabled{opacity:.5;cursor:default}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.job{margin-top:12px}.job .t{font-size:.95rem;word-break:break-all}
.bar{height:6px;background:var(--line);border-radius:3px;margin:6px 0 2px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}
.st{font-size:.82rem;color:var(--sub)}.st.err{color:var(--err)}
a.btn.save{margin-top:8px;background:var(--ok)}
.msg{color:var(--err);font-size:.9rem;margin:8px 0 0}
"""

LOGIN = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>로그인</title>
<style>{{css|safe}}</style></head><body><main>
<h1>영상 다운로더</h1><p class="sub">비밀번호를 입력하세요.</p>
<form method="post" class="card">
<input type="password" name="pw" placeholder="비밀번호" aria-label="비밀번호" autofocus>
<div class="row"><button>로그인</button></div>
{% if err %}<p class="msg">비밀번호가 맞지 않아요.</p>{% endif %}
</form></main></body></html>"""

APP = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>영상 다운로더</title>
<style>{{css|safe}}</style></head><body><main>
<h1>영상 다운로더</h1>
<p class="sub">링크를 붙여넣으면 서버가 받아서, 완료 후 저장 버튼으로 내려받을 수 있어요.</p>
<div class="card">
  <input id="url" type="text" placeholder="https://www.youtube.com/watch?v=..." aria-label="영상 주소">
  <div class="row">
    <select id="mode" aria-label="화질">
      <option value="best">최고 화질 (mp4)</option>
      <option value="1080">1080p 이하</option>
      <option value="720">720p 이하</option>
      <option value="mp3">음원만 (mp3)</option>
    </select>
    <button id="go">다운로드</button>
  </div>
</div>
<div id="jobs"></div>
</main>
<script>
const $=id=>document.getElementById(id);
$("go").onclick=start;
$("url").addEventListener("keydown",e=>{if(e.key==="Enter")start()});
async function start(){
  const url=$("url").value.trim(); if(!url) return;
  $("go").disabled=true;
  const r=await fetch("/api/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,mode:$("mode").value})});
  if(r.status===401){location.reload();return}
  const {id}=await r.json(); addJob(id,url); $("url").value=""; $("go").disabled=false;
}
function addJob(id,url){
  const el=document.createElement("div"); el.className="card job";
  el.innerHTML='<div class="t"></div><div class="bar"><i></i></div><div class="st">준비 중…</div>';
  el.querySelector(".t").textContent=url; $("jobs").prepend(el);
  const t=el.querySelector(".t"),bar=el.querySelector("i"),st=el.querySelector(".st");
  const timer=setInterval(async()=>{
    const j=await (await fetch("/api/progress/"+id)).json();
    if(j.title) t.textContent=j.title;
    bar.style.width=(j.percent||0)+"%";
    if(j.status==="downloading") st.textContent=j.percent.toFixed(0)+"% "+(j.speed||"");
    if(j.status==="processing") st.textContent="변환 중…";
    if(j.status==="done"){clearInterval(timer);bar.style.width="100%";st.textContent="완료 (30분 뒤 서버에서 삭제돼요)";
      const a=document.createElement("a");a.className="btn save";a.href="/api/file/"+id;a.textContent="내 기기에 저장";el.appendChild(a)}
    if(j.status==="error"){clearInterval(timer);st.textContent="실패: "+j.error;st.className="st err"}
  },800);
}
</script></body></html>"""


def authed():
    return session.get("ok") is True


def run_job(job_id, url, mode):
    job, workdir = JOBS[job_id], BASE / job_id
    workdir.mkdir(exist_ok=True)

    def hook(d):
        job["title"] = d.get("info_dict", {}).get("title", job.get("title", ""))
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job["percent"] = d["downloaded_bytes"] / total * 100
            job["speed"] = (d.get("_speed_str") or "").strip()
            job["status"] = "downloading"
        elif d["status"] == "finished":
            job["status"] = "processing"

    opts = {
        "format": FORMATS.get(mode, FORMATS["best"]),
        "outtmpl": str(workdir / "%(title)s.%(ext)s"),
        "progress_hooks": [hook],
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
    }
    if Path("cookies.txt").exists():
        opts["cookiefile"] = "cookies.txt"
    if mode == "mp3":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        opts["merge_output_format"] = "mp4"

    with SLOTS:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = [p for p in workdir.iterdir() if p.suffix not in (".part", ".ytdl")]
            job["file"] = str(max(files, key=lambda p: p.stat().st_size))
            job["status"], job["finished"] = "done", time.time()
        except Exception as e:
            job["status"], job["error"] = "error", str(e).replace("ERROR: ", "")[:200]
            job["finished"] = time.time()


def cleaner():
    while True:
        time.sleep(60)
        now = time.time()
        with LOCK:
            for jid in [k for k, v in JOBS.items() if v.get("finished") and now - v["finished"] > KEEP_SECONDS]:
                shutil.rmtree(BASE / jid, ignore_errors=True)
                JOBS.pop(jid, None)


threading.Thread(target=cleaner, daemon=True).start()


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if PASSWORD and request.form.get("pw") == PASSWORD:
            session["ok"] = True
            return redirect("/")
        time.sleep(1.5)  # 무차별 대입 완화
        return render_template_string(LOGIN, css=CSS, err=True)
    if not authed():
        return render_template_string(LOGIN, css=CSS, err=False)
    return render_template_string(APP, css=CSS)


@app.post("/api/download")
def download():
    if not authed():
        return jsonify(error="login"), 401
    body = request.get_json(force=True)
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "downloading", "percent": 0}
    threading.Thread(target=run_job, args=(job_id, body.get("url", ""), body.get("mode", "best")), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/api/progress/<job_id>")
def progress(job_id):
    if not authed():
        return jsonify(error="login"), 401
    j = JOBS.get(job_id, {"status": "error", "error": "작업이 만료됐어요"})
    return jsonify({k: v for k, v in j.items() if k != "file"})


@app.get("/api/file/<job_id>")
def get_file(job_id):
    if not authed():
        return "login", 401
    j = JOBS.get(job_id)
    if not j or j.get("status") != "done" or not Path(j["file"]).exists():
        return "만료됐어요", 404
    return send_file(j["file"], as_attachment=True)
