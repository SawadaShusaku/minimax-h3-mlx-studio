#!/usr/bin/env python3
"""MiniMax-H3 の生成フォームと履歴を1画面にまとめたWebアプリ。

  ./h3app.py            起動してブラウザを開く（既定 http://127.0.0.1:8765）

Python標準ライブラリのみで動く。画面はサーバがHTML断片を返し、HTMXが差し替える。
進捗だけは値の更新なので、ブラウザ標準の EventSource で受ける。

生成はGPUを占有するので同時に1件しか受け付けない。ワーカースレッドで走るため、
ブラウザを閉じても生成は続き、開き直せば進行中のジョブに再接続する。
"""

import argparse
import html
import json
import random
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import h3core
import h3lib
import h3view

WEB_DIR = h3lib.PROJECT / "web"
POLL = 0.4  # SSE が新しい進捗を拾いに行く間隔（秒）


class Job:
    """進行中の生成1件。進捗を貯めておき、SSEが追いかけて読む。"""

    def __init__(self, params):
        self.params = params
        self.events = []
        self.record = None
        self.done = False
        self.started = time.time()
        self.lock = threading.Lock()

    def push(self, ev):
        with self.lock:
            self.events.append(ev)

    def since(self, index):
        with self.lock:
            return self.events[index:]

    def finish(self, record):
        self.record = record
        self.done = True


CURRENT = None  # 実行中のジョブ（同時実行を防ぐ）
CURRENT_LOCK = threading.Lock()


def start_job(params, port):
    global CURRENT
    with CURRENT_LOCK:
        if CURRENT is not None and not CURRENT.done:
            return None
        job = Job(params)
        CURRENT = job

    def worker():
        record = h3core.run(params, port=port, on_progress=job.push)
        job.finish(record)

    threading.Thread(target=worker, daemon=True).start()
    return job


# ---------------------------------------------------------------- HTML 断片

def form_html(src=None):
    """生成フォーム。src を渡すとその設定で埋める（「この設定で作り直す」）。"""
    req = (src or {}).get("requested", {})
    esc = html.escape
    prompt = esc(req.get("prompt", ""))
    width = req.get("width", h3core.FALLBACK["width"])
    height = req.get("height", h3core.FALLBACK["height"])
    frames = req.get("frames", h3core.FALLBACK["frames"])
    steps = req.get("steps") or h3core.FALLBACK["steps"]
    seed = req.get("seed", random.randint(1, 99999))
    turbo = req.get("turbo", True)
    forked = src["id"] if src else ""

    res_options = "".join(
        f'<option value="{w}x{h}"{" selected" if (w, h) == (width, height) else ""}>'
        f"{esc(text)}</option>" for w, h, text in h3view.RESOLUTIONS)
    frame_options = "".join(
        f'<option value="{n}"{" selected" if n == frames else ""}>'
        f"{n}フレーム（{n / 24:.1f}秒）</option>" for n in h3view.FRAME_LADDER)

    banner = ""
    if src:
        banner = (f'<div class="notes">{esc(src["id"])} の設定を引き継いでいます。'
                  f'変えたい項目だけ書き換えてください。</div>')

    return f"""<form id="form" hx-post="/generate" hx-target="#progress"
      hx-swap="innerHTML" hx-disabled-elt="#go">
  {banner}
  <input type="hidden" name="forked_from" value="{esc(forked)}">
  <label>プロンプト
    <span class="hint">末尾の overall_soundscape: 以降が音声の指示になる</span></label>
  <textarea name="prompt" required
    placeholder="Hand-drawn 2D cel animation ... overall_soundscape: ...">{prompt}</textarea>

  <label>解像度 <span class="hint">短辺768がネイティブ</span></label>
  <select name="resolution">{res_options}</select>

  <label>長さ <span class="hint">124フレーム未満は学習範囲外</span></label>
  <select name="frames">{frame_options}</select>

  <div class="row">
    <div>
      <label>ステップ <span class="hint">turbo時6〜8</span></label>
      <input type="number" name="steps" value="{steps}" min="1" max="50">
    </div>
    <div>
      <label>シード <span class="hint">同じ値で再現</span></label>
      <input type="number" name="seed" value="{seed}" min="0">
    </div>
  </div>

  <label class="check"><input type="checkbox" name="turbo" value="1"
    {"checked" if turbo else ""}>turbo（4ステップ蒸留LoRA を使う）</label>

  <button id="go" class="primary" type="submit">生成する</button>
</form>"""


def progress_html(job_id):
    return f"""<div class="prog" id="prog" data-job="{job_id}">
  <div class="stage">生成を開始しました</div>
  <div class="line" id="prog-line">待機中…</div>
  <div class="bar"><div id="prog-bar"></div></div>
</div>"""


def history_html(records):
    if not records:
        return '<div class="sub">まだ履歴がありません。左のフォームから生成してください。</div>'
    return "\n".join(h3view.card_html(r, media_prefix="/media/", interactive=True)
                     for r in records)


APP_JS = """
%(click)s

// 進捗は「HTML断片の差し替え」ではなく「値の更新」なので EventSource で受ける。
function watch(jobId) {
  const es = new EventSource('/progress/' + jobId);
  es.onmessage = e => {
    const d = JSON.parse(e.data);
    const stage = document.querySelector('#prog .stage');
    const line = document.getElementById('prog-line');
    const bar = document.getElementById('prog-bar');
    if (d.type === 'done') {
      es.close();
      document.getElementById('progress').innerHTML = '';
      htmx.ajax('GET', '/history', {target: '#history', swap: 'innerHTML'});
      htmx.ajax('GET', '/form', {target: '#form', swap: 'outerHTML'});
      return;
    }
    if (stage) stage.textContent = d.stage || '生成中';
    if (d.step && d.total && bar) bar.style.width = (d.step / d.total * 100) + '%%';
    if (line) {
      let t = fmt(d.elapsed) + ' 経過';
      if (d.step && d.total) t += ' ・ ' + d.step + '/' + d.total + ' ステップ';
      if (d.sec_per_step) {
        t += ' ・ 1ステップ ' + fmt(d.sec_per_step);
        if (d.step && d.total) {
          t += ' ・ 残り約 ' + fmt((d.total - d.step) * d.sec_per_step);
        }
      }
      line.textContent = t;
    }
  };
}
function fmt(s) {
  if (s == null) return '-';
  s = Math.round(s);
  return s < 60 ? s + '秒' : Math.floor(s / 60) + '分' + String(s %% 60).padStart(2,'0') + '秒';
}
// フォーム送信で進捗欄が差し替わったら、そのジョブの監視を始める
document.body.addEventListener('htmx:afterSwap', e => {
  const p = document.querySelector('#prog[data-job]');
  if (p && !p.dataset.watching) { p.dataset.watching = '1'; watch(p.dataset.job); }
});
// 絞り込み
document.getElementById('q').addEventListener('input', function () {
  const n = this.value.toLowerCase();
  document.querySelectorAll('#history .card').forEach(c => {
    c.style.display = c.dataset.hay.includes(n) ? '' : 'none';
  });
});
""" % {"click": h3view.CLICK_TO_PLAY_JS}


def page_html(records, running_job):
    prog = progress_html(id(running_job)) if running_job else ""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiniMax-H3</title>
<script src="/static/htmx.min.js"></script>
<style>{h3view.CSS}</style></head><body>
<h1>MiniMax-H3</h1>
<div class="sub">生成と履歴。動画のサムネイルをクリックすると再生します。</div>
<div class="wrap">
  <div class="panel">
    <div class="card"><h2>新しく作る</h2>{form_html()}</div>
  </div>
  <div>
    <div id="progress">{prog}</div>
    <input id="q" type="search" placeholder="プロンプトやラベルで絞り込み">
    <div id="history">{history_html(records)}</div>
  </div>
</div>
<script>{APP_JS}</script></body></html>"""


# ---------------------------------------------------------------- サーバ

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "h3app"

    def log_message(self, fmt, *args):  # 既定のアクセスログは煩いので黙らせる
        pass

    # -- 返信ヘルパ -------------------------------------------------
    def send(self, body, ctype="text/html; charset=utf-8", status=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path):
        if not path.is_file():
            return self.send("見つかりません", status=404)
        types = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png",
                 ".js": "text/javascript; charset=utf-8"}
        self.send(path.read_bytes(), types.get(path.suffix, "application/octet-stream"))

    def safe_media(self, rel_path):
        """outputs/ と history/ の中のファイルだけを返す（パス抜けを防ぐ）。"""
        target = (h3lib.PROJECT / urllib.parse.unquote(rel_path)).resolve()
        for root in (h3lib.OUTPUT_DIR.resolve(), h3lib.HISTORY_DIR.resolve()):
            try:
                target.relative_to(root)
            except ValueError:
                continue
            return self.send_file(target)
        return self.send("見つかりません", status=404)

    def form_values(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    # -- ルーティング -----------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        path = url.path

        if path == "/":
            running = CURRENT if (CURRENT and not CURRENT.done) else None
            return self.send(page_html(list(reversed(h3lib.load())), running))
        if path == "/history":
            return self.send(history_html(list(reversed(h3lib.load()))))
        if path == "/form":
            src = None
            if query.get("from"):
                try:
                    src = h3lib.find(query["from"][0])
                except SystemExit:
                    src = None
            return self.send(form_html(src))
        if path == "/static/htmx.min.js":
            return self.send_file(WEB_DIR / "htmx.min.js")
        if path.startswith("/media/"):
            return self.safe_media(path[len("/media/"):])
        if path.startswith("/progress/"):
            return self.stream_progress()
        return self.send("見つかりません", status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/generate":
            return self.handle_generate()
        if path == "/rate":
            return self.handle_rate()
        return self.send("見つかりません", status=404)

    # -- 各処理 -----------------------------------------------------
    def handle_generate(self):
        v = self.form_values()
        try:
            width, height = (int(x) for x in v.get("resolution", "1024x768").split("x"))
            params = {
                "prompt": v.get("prompt", "").strip(),
                "width": width, "height": height,
                "frames": int(v.get("frames") or h3core.FALLBACK["frames"]),
                "steps": int(v.get("steps") or h3core.FALLBACK["steps"]),
                "seed": int(v.get("seed") or 0),
                "turbo": v.get("turbo") == "1",
                "forked_from": v.get("forked_from") or None,
            }
            h3core.normalize(params)
        except (ValueError, h3core.GenerationError) as err:
            return self.send(f'<div class="prog"><div class="err">{html.escape(str(err))}'
                             f"</div></div>")

        job = start_job(params, self.server.h3_port)
        if job is None:
            return self.send('<div class="prog"><div class="err">'
                             "すでに生成が走っています。終わってから実行してください。"
                             "</div></div>")
        return self.send(progress_html(id(job)))

    def handle_rate(self):
        v = self.form_values()
        try:
            target = h3lib.find(v["id"])
        except (SystemExit, KeyError):
            return self.send('<div class="card fail"><div class="err">'
                             "対象が見つかりません</div></div>")
        h3lib.append({"kind": "annotation", "target": target["id"],
                      "created_at": h3lib.now_iso(),
                      "rating": int(v["rating"]) if v.get("rating") else None,
                      "notes": v.get("notes")})
        updated = h3lib.find(target["id"])
        return self.send(h3view.card_html(updated, media_prefix="/media/",
                                          interactive=True))

    def stream_progress(self):
        """進行中のジョブの進捗を SSE で流す。完了で done を送って閉じる。"""
        job = CURRENT
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if job is None:
            self.write_event({"type": "done"})
            return
        sent = 0
        try:
            while True:
                for ev in job.since(sent):
                    ev = dict(ev, type="progress")
                    self.write_event(ev)
                    sent += 1
                if job.done:
                    self.write_event({"type": "done", "id": (job.record or {}).get("id")})
                    return
                time.sleep(POLL)
        except (BrokenPipeError, ConnectionResetError):
            pass  # ブラウザを閉じただけ。生成は続く

    def write_event(self, payload):
        self.wfile.write(
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765, help="このアプリのポート")
    p.add_argument("--mlx-port", type=int, default=h3core.DEFAULT_PORT,
                   help="mlx-serve のポート")
    p.add_argument("--no-open", action="store_true", help="ブラウザを開かない")
    a = p.parse_args()

    h3lib.ensure_dirs()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.daemon_threads = True
    server.h3_port = a.mlx_port
    url = f"http://127.0.0.1:{a.port}/"
    print(f"起動しました: {url}")
    print(f"  mlx-serve は {a.mlx_port} 番を見ています（未起動なら scripts/serve.sh）")
    print("  終了は Ctrl-C")
    if not a.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")


if __name__ == "__main__":
    main()
