"""
image2pdf_web/app.py  -  画像→PDF 変換 Webアプリ
起動: python app.py  →  http://localhost:5000
"""

import re, io, uuid, threading, os
from pathlib import Path
from flask import Flask, render_template_string, request, send_file, jsonify

from PIL import Image, ImageOps
import pillow_heif
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader

pillow_heif.register_heif_opener()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300MB

EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".jpg_"}

# --- PDF レイアウト ---
PAGE_W, PAGE_H = landscape(A4)
MM     = 72 / 25.4
MARGIN = 7.5 * MM
IMG_H  = PAGE_H - 2 * MARGIN
IMG_W  = IMG_H * 4 / 3
IMG_X  = (PAGE_W - IMG_W) / 2
IMG_Y  = MARGIN

# ジョブ管理 {job_id: {"status", "progress", "total", "pdf_bytes"}}
jobs: dict[str, dict] = {}


def sort_key(name: str):
    nums = [int(n) for n in re.findall(r"\d+", Path(name).stem)]
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0] if nums else 999, 0)


def crop_43(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w / h > 4 / 3:
        nw = int(h * 4 / 3); l = (w - nw) // 2
        return img.crop((l, 0, l + nw, h))
    else:
        nh = int(w * 3 / 4); t = (h - nh) // 2
        return img.crop((0, t, w, t + nh))


def run_job(job_id: str, file_list: list[tuple[str, bytes]]):
    """バックグラウンドでPDF生成"""
    job = jobs[job_id]
    try:
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=landscape(A4))
        total = len(file_list)
        job["total"] = total

        for i, (name, data) in enumerate(file_list):
            job["progress"] = i
            job["current"]  = name
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img = crop_43(img)
            ibuf = io.BytesIO()
            img.save(ibuf, format="JPEG", quality=92)
            ibuf.seek(0)
            c.drawImage(ImageReader(ibuf), IMG_X, IMG_Y,
                        width=IMG_W, height=IMG_H, preserveAspectRatio=False)
            c.showPage()

        c.save()
        buf.seek(0)
        job["pdf_bytes"] = buf.read()
        job["progress"]  = total
        job["status"]    = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)


# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>画像 → PDF</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Yu Gothic UI',Helvetica,sans-serif;background:#fff;color:#111;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:60px 16px}
h1{font-size:1.3rem;font-weight:600;letter-spacing:.04em;margin-bottom:4px}
.sub{color:#999;font-size:.78rem;margin-bottom:40px;letter-spacing:.03em}
.wrap{width:100%;max-width:520px}
#drop{border:1.5px solid #ccc;padding:48px 20px;text-align:center;cursor:pointer;transition:.15s;color:#aaa;font-size:.9rem;line-height:1.8}
#drop:hover,#drop.over{border-color:#111;color:#111}
#drop svg{display:block;margin:0 auto 14px;color:#bbb}
#drop.over svg{color:#111}
#file-list{margin-top:16px;max-height:180px;overflow-y:auto;border-top:1px solid #eee}
.file-item{display:flex;align-items:center;gap:10px;padding:6px 4px;font-size:.8rem;color:#555;border-bottom:1px solid #f0f0f0}
.file-item .num{color:#999;min-width:24px;text-align:right;font-variant-numeric:tabular-nums}
#file-count{font-size:.78rem;color:#999;margin-top:8px}
.btn-row{display:flex;gap:8px;margin-top:20px}
button{border:1.5px solid #111;background:#fff;color:#111;padding:8px 20px;font-size:.85rem;cursor:pointer;font-family:inherit;transition:.15s;letter-spacing:.03em}
button:hover{background:#111;color:#fff}
button:disabled{border-color:#ccc;color:#ccc;cursor:not-allowed}
button:disabled:hover{background:#fff;color:#ccc}
.btn-primary{background:#111;color:#fff}
.btn-primary:hover{background:#333}
.btn-primary:disabled{background:#ccc;border-color:#ccc;color:#fff}
.btn-primary:disabled:hover{background:#ccc;color:#fff}
#progress-wrap{margin-top:24px;display:none}
.prog-bar-bg{background:#eee;height:2px;margin:10px 0}
.prog-bar{background:#111;height:100%;width:0%;transition:width .3s}
#prog-text{font-size:.78rem;color:#999}
#done-wrap{display:none;margin-top:24px;padding-top:24px;border-top:1px solid #eee;text-align:center}
#done-wrap p{font-size:.9rem;margin-bottom:16px;color:#111}
.btn-dl{background:#111;color:#fff;border:none;padding:10px 32px;font-size:.9rem;cursor:pointer;letter-spacing:.04em}
.btn-dl:hover{background:#333}
input[type=file]{display:none}
</style>
</head>
<body>
<h1>画像 → PDF 変換</h1>
<p class="sub">A4横 &nbsp;/&nbsp; 4:3クロップ &nbsp;/&nbsp; 上下マージン 7.5mm</p>

<div class="wrap">
  <div id="drop" onclick="document.getElementById('file-input').click()">
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
    </svg>
    ここに画像をドロップ<br>またはクリックして選択
  </div>
  <input type="file" id="file-input" accept=".jpg,.jpeg,.png,.heic,.heif,.JPG,.JPEG,.PNG,.HEIC" multiple>

  <div id="file-list"></div>
  <div id="file-count"></div>

  <div class="btn-row">
    <button class="btn-primary" id="convert-btn" onclick="startConvert()" disabled>PDF を作成</button>
    <button onclick="clearFiles()">クリア</button>
  </div>

  <div id="progress-wrap">
    <div class="prog-bar-bg"><div class="prog-bar" id="prog-bar"></div></div>
    <div id="prog-text">準備中...</div>
  </div>

  <div id="done-wrap">
    <p>変換完了</p>
    <a id="dl-link" href="#"><button class="btn-dl">PDF をダウンロード</button></a>
    <br><br>
    <button onclick="resetAll()" style="margin-top:8px">もう一度</button>
  </div>
</div>

<script>
let allFiles = [];
let pollTimer = null;

// ---- ドロップ ----
const drop = document.getElementById('drop');
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('over');
  addFiles([...e.dataTransfer.files]);
});
document.getElementById('file-input').addEventListener('change', e => {
  addFiles([...e.target.files]);
  e.target.value = '';
});

const EXTS = new Set(['.jpg','.jpeg','.png','.heic','.heif','.jpg_']);

function addFiles(files) {
  const existing = new Set(allFiles.map(f => f.name + f.size));
  for (const f of files) {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (EXTS.has(ext) && !existing.has(f.name + f.size)) {
      allFiles.push(f);
      existing.add(f.name + f.size);
    }
  }
  allFiles.sort((a, b) => sortKey(a.name) < sortKey(b.name) ? -1 : 1);
  renderList();
}

function sortKey(name) {
  const stem = name.replace(/\.[^.]+$/, '');
  const nums = [...stem.matchAll(/\d+/g)].map(m => parseInt(m[0]));
  return nums.length >= 2 ? [nums[0], nums[1]] : [nums[0] ?? 999, 0];
}

function renderList() {
  const list = document.getElementById('file-list');
  const count = document.getElementById('file-count');
  list.innerHTML = allFiles.map((f, i) =>
    `<div class="file-item"><span class="num">${i+1}</span><span>${f.name}</span></div>`
  ).join('');
  count.textContent = allFiles.length ? `${allFiles.length} ファイル` : '';
  document.getElementById('convert-btn').disabled = allFiles.length === 0;
}

function clearFiles() {
  allFiles = [];
  renderList();
  resetProgress();
}

// ---- 変換 ----
async function startConvert() {
  if (!allFiles.length) return;
  document.getElementById('convert-btn').disabled = true;
  document.getElementById('progress-wrap').style.display = 'block';
  document.getElementById('done-wrap').style.display = 'none';
  setProgress(0, allFiles.length, 'アップロード中...');

  const form = new FormData();
  allFiles.forEach(f => form.append('files', f, f.name));

  let jobId;
  try {
    const res = await fetch('/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    jobId = data.job_id;
  } catch(e) {
    alert('アップロードエラー: ' + e.message);
    document.getElementById('convert-btn').disabled = false;
    return;
  }

  // ポーリング
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch('/status/' + jobId);
      const d = await res.json();
      if (d.status === 'done') {
        clearInterval(pollTimer);
        setProgress(d.total, d.total, '完了！');
        showDone(jobId);
      } else if (d.status === 'error') {
        clearInterval(pollTimer);
        alert('エラー: ' + d.error);
        document.getElementById('convert-btn').disabled = false;
      } else {
        setProgress(d.progress, d.total, `[${d.progress}/${d.total}]  ${d.current ?? ''}`);
      }
    } catch(e) {}
  }, 600);
}

function setProgress(val, max, text) {
  const pct = max ? Math.round(val / max * 100) : 0;
  document.getElementById('prog-bar').style.width = pct + '%';
  document.getElementById('prog-text').textContent = text;
}

function showDone(jobId) {
  document.getElementById('done-wrap').style.display = 'block';
  document.getElementById('dl-link').href = '/download/' + jobId;
  document.getElementById('dl-link').download = 'output.pdf';
}

function resetProgress() {
  document.getElementById('progress-wrap').style.display = 'none';
  document.getElementById('done-wrap').style.display = 'none';
  document.getElementById('convert-btn').disabled = false;
  setProgress(0, 1, '');
}

function resetAll() {
  clearFiles();
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify(error="ファイルがありません"), 400

    file_list = sorted(
        [(f.filename, f.read()) for f in files if Path(f.filename).suffix.lower() in EXTS],
        key=lambda x: sort_key(x[0])
    )
    if not file_list:
        return jsonify(error="対応画像が見つかりません"), 400

    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "running", "progress": 0, "total": len(file_list),
                    "current": "", "pdf_bytes": None}

    threading.Thread(target=run_job, args=(job_id, file_list), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="not found"), 404
    return jsonify(
        status=job["status"],
        progress=job["progress"],
        total=job["total"],
        current=job.get("current", ""),
        error=job.get("error", "")
    )


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return "Not ready", 404
    return send_file(
        io.BytesIO(job["pdf_bytes"]),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="output.pdf"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
