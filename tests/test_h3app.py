"""Webアプリの経路をHTTPで実際に叩くテスト。

参照つき生成をCLIでしか確認せず、Web経路を一度も通していなかったために、
「Webの参照生成が必ず失敗する」不具合を出した。同じ取りこぼしを防ぐため、
各モードのフォーム送信を本物のHTTPで通す。

mlx-serve は立てない。代わりに /v1/video/generations だけを模した小さな
サーバへ向ける。こうすると重みもGPUも要らず、mux・サムネイル・履歴書き込みまで
含めた実際の経路が数秒で通る。
"""

import base64
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

import h3app
import h3core
import h3lib
import h3view

FRAMES, SIZE = 5, 64


class StubMlxServe(BaseHTTPRequestHandler):
    """本物と同じ形の SSE を返すだけのスタブ。受け取った body は記録する。"""

    received = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubMlxServe.received.append(body)
        payload = {
            "type": "complete", "frames": FRAMES, "width": SIZE, "height": SIZE,
            "fps": 24, "format": "rgb8",
            "data": base64.b64encode(bytes(FRAMES * SIZE * SIZE * 3)).decode(),
            "audio_format": "pcm_s16le", "audio_channels": 2,
            "audio_sample_rate": 32000,
            "audio_data": base64.b64encode(bytes(4000)).decode(),
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ev in ({"type": "progress", "stage": "Generating", "step": 1, "total": 1},
                   payload):
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        self.wfile.flush()


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class WebPathTests(unittest.TestCase):
    """フォーム送信 → 生成 → 履歴 までを、実際のHTTPで通す。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # 本番の history/ outputs/ を汚さないよう、書き込み先を差し替える。
        cls.saved = {k: getattr(h3lib, k) for k in
                     ("MODELS_DIR", "OUTPUT_DIR", "INPUTS_DIR", "HISTORY_DIR",
                      "HISTORY_FILE", "THUMBS_DIR")}
        h3lib.MODELS_DIR = root / "models"
        h3lib.OUTPUT_DIR = root / "outputs"
        h3lib.INPUTS_DIR = root / "inputs"
        h3lib.HISTORY_DIR = root / "history"
        h3lib.HISTORY_FILE = root / "history" / "history.jsonl"
        h3lib.THUMBS_DIR = root / "history" / "thumbs"
        turbo_dir = h3lib.MODELS_DIR / h3lib.pack_for("t2va")
        turbo_dir.mkdir(parents=True)
        (turbo_dir / "turbo_lora.safetensors").write_bytes(b"test turbo v4")
        h3lib.ensure_dirs()

        cls.stub = serve(StubMlxServe)
        cls.app = serve(h3app.Handler)
        cls.app.h3_port = cls.stub.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.app.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.stub.shutdown()
        cls.app.shutdown()
        for key, value in cls.saved.items():
            setattr(h3lib, key, value)
        cls.tmp.cleanup()

    def setUp(self):
        StubMlxServe.received.clear()
        h3app.CURRENT = None          # 同時実行の制限を持ち越さない

    # -- ヘルパ ---------------------------------------------------
    def post(self, fields):
        data = urllib.parse.urlencode(fields).encode()
        with urllib.request.urlopen(self.base + "/generate", data) as r:
            return r.status, r.read().decode()

    def wait_for_record(self):
        job = h3app.CURRENT
        for _ in range(200):
            if job.done:
                return job.record
            threading.Event().wait(0.05)
        self.fail("生成が終わらない")

    def png_b64(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.png"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"color=c=red:s={SIZE}x{SIZE}", "-frames:v", "1",
                            str(p)], check=True)
            return base64.b64encode(p.read_bytes()).decode()

    # -- 各モードが実際に通ること ----------------------------------
    def test_every_mode_form_renders(self):
        for mode in h3lib.MODES:
            with urllib.request.urlopen(f"{self.base}/form?mode={mode}") as r:
                self.assertEqual(r.status, 200, mode)
                self.assertIn('id="composer"', r.read().decode(), mode)

    def test_text_mode_generates_and_records(self):
        status, body = self.post({
            "mode": "t2va", "prompt": "x. overall_soundscape: hum.",
            "resolution": f"{SIZE}x{SIZE}", "frames": FRAMES, "steps": 1, "seed": 1,
            "turbo": "1"})
        self.assertEqual(status, 200)
        self.assertIn('class="prog"', body)
        self.assertNotIn('class="err"', body)
        record = self.wait_for_record()
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["mode"], "t2va")
        self.assertEqual(record["turbo_lora"]["filename"],
                         "turbo_lora.safetensors")
        self.assertEqual(record["turbo_lora"]["sha256"],
                         hashlib.sha256(b"test turbo v4").hexdigest())
        self.assertIn(record["turbo_lora"]["sha256"],
                      h3lib.HISTORY_FILE.read_text("utf-8"))

    def test_reference_mode_reaches_the_server(self):
        """参照モードが 500 でも err でもなく、参照つきで実行されること。

        attach_keyframes のモード表に ref2va が無く KeyError になっていた回帰。
        """
        refs = [{"kind": "image", "name": "a.png", "b64": self.png_b64()}]
        status, body = self.post({
            "mode": "ref2va", "prompt": "x. overall_soundscape: hum.",
            "refs_json": json.dumps(refs), "ref_image_size": "max",
            "resolution": f"{SIZE}x{SIZE}", "frames": FRAMES, "steps": 1, "seed": 1})
        self.assertEqual(status, 200)
        self.assertNotIn('class="err"', body)
        record = self.wait_for_record()
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["mode"], "ref2va")
        self.assertEqual(len(record["refs"]), 1)
        # 参照つきは REF2VA パックを名指しし、参照が body に載っていること
        sent = StubMlxServe.received[-1]
        self.assertEqual(sent["model"], h3lib.pack_for("ref2va"))
        self.assertEqual(len(sent["ref_images"]), 1)
        self.assertEqual(sent["ref_image_size"], "max")
        self.assertNotIn("turbo", sent)

    def test_keyframe_mode_sends_the_image_and_picks_fl2va(self):
        status, body = self.post({
            "mode": "fl2va", "prompt": "x. overall_soundscape: hum.",
            "first_frame_b64": self.png_b64(),
            "resolution": f"{SIZE}x{SIZE}", "frames": FRAMES, "steps": 1, "seed": 1})
        self.assertEqual(status, 200)
        self.assertNotIn('class="err"', body)
        record = self.wait_for_record()
        self.assertEqual(record["mode"], "fl2va")
        self.assertIn("first_frame", record["inputs"])
        sent = StubMlxServe.received[-1]
        self.assertEqual(sent["model"], h3lib.pack_for("fl2va"))
        self.assertIn("first_frame_image", sent)

    def test_mode_without_its_input_is_refused(self):
        status, body = self.post({
            "mode": "fl2va", "prompt": "x",
            "resolution": f"{SIZE}x{SIZE}", "frames": FRAMES, "steps": 1, "seed": 1})
        self.assertEqual(status, 200)
        self.assertIn('class="err"', body)

    def test_worker_always_finishes_even_when_the_job_blows_up(self):
        """ジョブが永久に「実行中」で残ると、以降の生成が全部拒否される。"""
        broken = dict(h3core.FALLBACK, prompt="x",
                      refs=[{"kind": "image", "path": "存在しない.png"}])
        job = h3app.start_job(broken, self.app.h3_port)
        for _ in range(200):
            if job.done:
                break
            threading.Event().wait(0.05)
        self.assertTrue(job.done, "失敗してもジョブは終了扱いにする")
        self.assertEqual(job.record["status"], "error")


class ReferenceHandlingTests(unittest.TestCase):
    """レビューで見つかった参照まわりの回帰。"""

    def test_short_reference_video_uses_all_its_frames(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc=size=64x64:rate=24", "-frames:v", "5",
                            "-pix_fmt", "yuv420p", str(path)], check=True)
            # 17で割ったままだと2枚しか取れず、下限5割れで弾かれていた
            self.assertEqual(len(h3core.ref_video_frames_b64(path)), 5)

    def test_unreadable_reference_audio_becomes_a_generation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.wav"
            path.write_bytes(b"not audio at all")
            with self.assertRaises(h3core.GenerationError):
                h3core.ref_audio_wav_b64(path)

    def test_reference_caps_count_the_total_not_just_each_kind(self):
        # 種類ごと（9/3/3）は満たすが合計15。本当の上限は12。
        too_many = ([{"kind": "image", "path": f"{i}.png"} for i in range(9)]
                    + [{"kind": "video", "path": f"{i}.mp4"} for i in range(3)]
                    + [{"kind": "audio", "path": f"{i}.wav"} for i in range(3)])
        with self.assertRaises(h3core.GenerationError):
            h3core.validate_refs(too_many)
        self.assertTrue(h3core.validate_refs(too_many[:12]))

    def test_rebuild_keeps_ref_image_size(self):
        src = {"id": "x", "mode": "ref2va", "ref_image_size": "max", "refs": [],
               "requested": dict(h3core.FALLBACK, prompt="p")}
        self.assertIn('value="max" selected', h3view.form_html(src, "ref2va"))

    def test_reference_defaults_use_base_not_fl2va_turbo(self):
        p = h3core.normalize({
            "prompt": "p",
            "refs": [{"kind": "image", "path": "a.png"}],
        })
        self.assertEqual(p["mode"], "ref2va")
        self.assertEqual(p["steps"], 30)
        self.assertFalse(p["turbo"])

        form = h3view.form_html(None, "ref2va")
        self.assertIn('name="steps" value="30"', form)
        self.assertNotIn('name="turbo" value="1"\n    checked', form)

    def test_fl2va_defaults_keep_the_existing_turbo_recipe(self):
        p = h3core.normalize({"prompt": "p"})
        self.assertEqual(p["mode"], "t2va")
        self.assertEqual(p["steps"], 6)
        self.assertTrue(p["turbo"])

    def test_cli_keyframe_path_selects_fl2va_before_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"read_image_b64 only records the supplied bytes")
            p = h3core.normalize({"prompt": "p", "first_frame_path": str(path)})
        self.assertEqual(p["mode"], "fl2va")
        self.assertTrue(p["turbo"])

    def test_reference_mode_refuses_chaining_and_keyframes(self):
        base = dict(h3core.FALLBACK, prompt="p",
                    refs=[{"kind": "image", "path": "a.png"}])
        for extra in ({"chain_windows": 3}, {"first_frame_b64": "AA"}):
            with self.assertRaises(h3core.GenerationError):
                h3core.normalize(dict(base, **extra))


if __name__ == "__main__":
    unittest.main(verbosity=2)
