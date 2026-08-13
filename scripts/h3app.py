#!/usr/bin/env python3
"""MiniMax-H3 の生成フォームと履歴を1画面にまとめたWebアプリ。

  ./h3app.py            起動してブラウザを開く（既定 http://127.0.0.1:8765）

Python標準ライブラリのみで動く。画面はサーバがHTML断片を返し、HTMXが差し替える。
進捗だけは値の更新なので、ブラウザ標準の EventSource で受ける。

生成はGPUを占有するので同時に1件しか受け付けない。ワーカースレッドで走るため、
ブラウザを閉じても生成は続き、開き直せば進行中のジョブに再接続する。
"""

import argparse
import base64
import html
import json
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
        # 何が飛んできても必ず finish する。ここを抜けるとジョブが永久に
        # 「実行中」のまま残り、以降の生成が全部拒否される。
        try:
            record = h3core.run(params, port=port, on_progress=job.push)
        except BaseException as err:                     # noqa: BLE001
            record = {"kind": "generation", "id": h3lib.new_id(),
                      "created_at": h3lib.now_iso(), "status": "error",
                      "mode": params.get("mode", "t2va"), "label": "failed",
                      "prompt": params.get("prompt", ""), "requested": {},
                      "error": f"想定外の失敗: {err}"}
            h3lib.append(record)
        job.finish(record)

    threading.Thread(target=worker, daemon=True).start()
    return job


# ---------------------------------------------------------------- HTML 断片

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

// 画像はブラウザ側で base64 にして hidden へ入れる。こうするとフォームは
// 素の urlencoded のままで済み、サーバに multipart の解析が要らない。
document.body.addEventListener('change', e => {
  const input = e.target;
  if (input.type !== 'file' || !input.dataset.target) return;
  const name = input.dataset.target;
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = reader.result.split(',')[1];
    document.querySelector(`input[name="${name}_b64"]`).value = b64;
    const prev = document.getElementById(name + '-prev');
    prev.src = reader.result;
    prev.style.display = 'block';
  };
  reader.readAsDataURL(file);
});

// 参照は複数ファイル・複数種類なので、hidden の JSON 配列にまとめて積む。
document.body.addEventListener('change', e => {
  const input = e.target;
  if (input.type !== 'file' || !input.dataset.ref) return;
  const hidden = document.querySelector('input[name="refs_json"]');
  if (!hidden) return;
  let refs = [];
  try { refs = JSON.parse(hidden.value === 'keep' ? '[]' : hidden.value); } catch (_) {}
  refs = refs.filter(r => r.kind !== input.dataset.ref);   // 同じ種類は選び直し
  let pending = input.files.length;
  [...input.files].forEach(file => {
    const reader = new FileReader();
    reader.onload = () => {
      refs.push({kind: input.dataset.ref, name: file.name,
                 b64: reader.result.split(',')[1]});
      if (--pending === 0) {
        hidden.value = JSON.stringify(refs);
        document.getElementById('ref-list').innerHTML = refs.map(r =>
          '<span class="chip"><b>' + r.kind + '</b>' + r.name + '</span>').join('');
      }
    };
    reader.readAsDataURL(file);
  });
});

// 窓の数と1窓の長さから合計秒数を出す／品質優先の警告を出し入れする
function syncForm() {
  const f = document.getElementById('frames-sel'), c = document.getElementById('chain-sel');
  if (f && c) {
    [...c.options].forEach(o => {
      const n = +o.value, total = n * (+f.value) / 24;
      o.textContent = n === 1 ? '1窓' : n + '窓（約' + Math.round(total) + '秒）';
    });
  }
  const slow = document.querySelector('input[name=slow]');
  const warn = document.getElementById('slow-warn');
  if (slow && warn) warn.style.display = slow.checked ? 'block' : 'none';
}
document.body.addEventListener('change', syncForm);
document.body.addEventListener('htmx:afterSwap', syncForm);
syncForm();

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
      htmx.ajax('GET', '/form', {target: '#composer', swap: 'outerHTML'});
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
    <div class="card"><h2>新しく作る</h2>
      {h3view.composer_html()}</div>
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
        types = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png",
                 ".js": "text/javascript; charset=utf-8"}
        self.send(path.read_bytes(), types.get(path.suffix, "application/octet-stream"))

    def safe_media(self, rel_path):
        """outputs/ inputs/ history/ の中のファイルだけを返す（パス抜けを防ぐ）。"""
        target = (h3lib.PROJECT / urllib.parse.unquote(rel_path)).resolve()
        for root in (h3lib.OUTPUT_DIR.resolve(), h3lib.INPUTS_DIR.resolve(),
                     h3lib.HISTORY_DIR.resolve()):
            try:
                target.relative_to(root)
            except ValueError:
                continue
            return self.send_file(target)
        return self.send("見つかりません", status=404)

    def form_values(self, multi=()):
        """フォームの値。multi に挙げた名前だけはリストで返す（LoRAの複数行）。"""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        out = {k: v[0] for k, v in parsed.items()}
        for key in multi:
            out[key] = parsed.get(key, [])
        return out

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
            mode = query.get("mode", [None])[0]
            return self.send(h3view.composer_html(src, mode))
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
        v = self.form_values(multi=("lora_path", "lora_scale"))
        try:
            width, height = (int(x) for x in v.get("resolution", "1024x768").split("x"))
            loras = [{"path": p.strip(), "scale": float(s or 1.0)}
                     for p, s in zip(v["lora_path"], v["lora_scale"]) if p.strip()]
            params = {
                "prompt": v.get("prompt", "").strip(),
                "width": width, "height": height,
                "frames": int(v.get("frames") or h3core.FALLBACK["frames"]),
                "steps": int(v.get("steps") or h3core.FALLBACK["steps"]),
                "seed": int(v.get("seed") or 0),
                "turbo": v.get("turbo") == "1",
                # チェックが入ったときだけ fast を切る（既定は高速側）。
                "fast": v.get("slow") != "1",
                "chain_windows": int(v.get("chain_windows") or 1),
                "ref_image_size": v.get("ref_image_size") or "match",
                "loras": loras,
                "forked_from": v.get("forked_from") or None,
            }
            self.attach_keyframes(params, v)
            self.attach_refs(params, v)
            # normalize は新しい辞書を返すので、受け取らないと mode も既定値も
            # 反映されない（この取りこぼしで mode 参照が KeyError になっていた）。
            params = h3core.normalize(params)
        except (ValueError, KeyError, h3core.GenerationError) as err:
            return self.send(f'<div class="prog"><div class="err">{html.escape(str(err))}'
                             f"</div></div>")

        if params["mode"] != v.get("mode", "t2va"):
            return self.send('<div class="prog"><div class="err">'
                             "このモードに必要な画像が指定されていません</div></div>")

        job = start_job(params, self.server.h3_port)
        if job is None:
            return self.send('<div class="prog"><div class="err">'
                             "すでに生成が走っています。終わってから実行してください。"
                             "</div></div>")
        return self.send(progress_html(id(job)))

    def attach_keyframes(self, params, v):
        """フォームの画像を params に載せる。

        値は3通り: 空（なし）／実際のbase64（新規に選んだ）／"keep"（派生元の
        画像をそのまま使う）。モードで要らない画像は捨てる。
        """
        # 参照つきはキーフレームを取らない。ここに載せ忘れると Web の
        # REF2VA が KeyError で必ず失敗するので、モードは全部書く。
        wanted = {"t2va": (), "ref2va": (), "fl2va": ("first_frame",),
                  "interp": ("first_frame", "last_frame")}.get(v.get("mode", "t2va"))
        if wanted is None:
            raise h3core.GenerationError(f"モードが不正です: {v.get('mode')}")
        for key in ("first_frame", "last_frame"):
            data = (v.get(key + "_b64") or "").strip()
            if key not in wanted or not data:
                continue
            if data == "keep":
                src = h3lib.find(v["forked_from"]) if v.get("forked_from") else None
                path = (src or {}).get("inputs", {}).get(key)
                if not path:
                    continue
                params[key + "_b64"], params[key + "_ext"] = \
                    h3core.read_image_b64(h3lib.PROJECT / path)
            else:
                params[key + "_b64"] = data
                params[key + "_ext"] = ".png"

    def attach_refs(self, params, v):
        """参照ファイルを inputs/ に保存し、params に載せる。

        値は空／JSON配列（新規に選んだ）／"keep"（派生元のものを使う）の3通り。
        """
        raw = (v.get("refs_json") or "").strip()
        if v.get("mode") != "ref2va" or not raw or raw == "[]":
            return
        if raw == "keep":
            src = h3lib.find(v["forked_from"]) if v.get("forked_from") else None
            params["refs"] = (src or {}).get("refs") or []
            return
        try:
            incoming = json.loads(raw)
        except json.JSONDecodeError:
            raise h3core.GenerationError("参照の受け取りに失敗しました")

        h3lib.ensure_dirs()
        stamp = h3lib.new_id()
        saved = []
        for i, ref in enumerate(incoming, 1):
            kind = ref.get("kind")
            if kind not in h3core.MAX_REF:
                raise h3core.GenerationError(f"参照の種類が不正です: {kind}")
            suffix = Path(ref.get("name") or "").suffix.lower() or {
                "image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
            path = h3lib.INPUTS_DIR / f"{stamp}_ref{i:02d}{suffix}"
            try:
                path.write_bytes(base64.b64decode(ref["b64"]))
            except (KeyError, ValueError):
                raise h3core.GenerationError(f"参照 {i} を読み取れませんでした")
            saved.append({"kind": kind, "path": h3lib.rel(path),
                          "name": ref.get("name") or path.name})
        params["refs"] = saved

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
