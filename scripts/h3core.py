#!/usr/bin/env python3
"""生成の実処理。CLI（h3gen.py）とWebアプリ（h3app.py）が共用する。

mlx-serve の /v1/video/generations は base64 の rgb8 フレームと pcm_s16le 音声を
返すだけなので、mp4 化はこちら側で行う。進捗は SSE で流れてくるので、
呼び出し側が受け取れるよう on_progress コールバックで中継する。
"""

import base64
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import h3lib

DEFAULT_PORT = 11434
# 引き継げる設定。--from と「この設定で作り直す」はこれらだけを継承する。
INHERITED = ("prompt", "frames", "width", "height", "steps", "seed", "turbo")
FALLBACK = {"frames": 124, "width": 1024, "height": 768,
            "steps": 6, "seed": 7, "turbo": True}


class GenerationError(RuntimeError):
    pass


def slug(prompt, words=4):
    """プロンプト冒頭からファイル名用の短い名前を作る。"""
    tokens = re.findall(r"[A-Za-z0-9]+", prompt.lower())
    skip = {"a", "an", "the", "in", "of", "with", "and", "hand", "drawn", "2d"}
    picked = [t for t in tokens if t not in skip][:words]
    return "-".join(picked) or "untitled"


def snap_frames(n):
    """サーバ側と同じ 17k+5 の階段に丸める（送信前に実効値を知るため）。"""
    if n <= 5:
        return 5
    return 5 + 17 * ((n - 5 + 16) // 17)


def stream_generate(params, port=DEFAULT_PORT, timeout=7200, on_progress=None):
    """SSE を読みながら complete イベントと所要時間を返す。"""
    body = {
        "prompt": params["prompt"],
        "num_frames": params["frames"],
        "width": params["width"],
        "height": params["height"],
        "steps": params["steps"],
        "seed": params["seed"],
        "stream": True,
    }
    if params.get("turbo"):
        body["turbo"] = True

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/video/generations",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    started = time.time()
    complete, step_marks = None, []
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise GenerationError(f"サーバが {err.code} を返しました: {detail}")
    except urllib.error.URLError as err:
        raise GenerationError(
            f"サーバに接続できません（scripts/serve.sh で起動してください）: {err.reason}")

    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            kind = ev.get("type")
            if kind == "progress":
                elapsed = time.time() - started
                stage = ev.get("stage", "?")
                if stage.lower().startswith("generat"):
                    step_marks.append(elapsed)
                if on_progress:
                    on_progress({
                        "stage": stage, "step": ev.get("step"), "total": ev.get("total"),
                        "elapsed": round(elapsed, 1),
                        "sec_per_step": _per_step(step_marks),
                    })
            elif kind == "complete":
                complete = ev
            elif kind == "error":
                raise GenerationError(f"サーバがエラーを返しました: {ev}")

    if complete is None:
        raise GenerationError("complete イベントが来ませんでした")
    return complete, {"total_sec": round(time.time() - started, 1),
                      "sec_per_step": _per_step(step_marks)}


def _per_step(marks):
    if len(marks) < 2:
        return None
    return round((marks[-1] - marks[0]) / (len(marks) - 1), 1)


def mux(ev, out_path, metadata):
    """rgb8 フレーム + pcm_s16le を mp4 に束ね、設定を mp4 のコメントにも埋める。"""
    frames, w, h = ev["frames"], ev["width"], ev["height"]
    fps = ev.get("fps", 24)
    video_raw = base64.b64decode(ev["data"])
    expected = frames * w * h * 3
    if len(video_raw) != expected:
        raise GenerationError(f"フレーム長が不一致: {len(video_raw)} != {expected}")

    tmp = Path(str(out_path) + ".tmpdir")
    tmp.mkdir(exist_ok=True)
    try:
        vraw = tmp / "frames.rgb"
        vraw.write_bytes(video_raw)
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", str(vraw)]
        if ev.get("audio_data"):
            araw = tmp / "audio.pcm"
            araw.write_bytes(base64.b64decode(ev["audio_data"]))
            cmd += ["-f", "s16le", "-ar", str(ev.get("audio_sample_rate", 48000)),
                    "-ac", str(ev.get("audio_channels", 2)), "-i", str(araw),
                    "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-metadata", "comment=" + json.dumps(metadata, ensure_ascii=False),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_path)]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_strip(video_path, frames, out_path, tiles=4):
    """動きが一目で分かるように、等間隔の数コマを横に並べた画像を作る。"""
    picks = [int(frames * i / tiles) for i in range(tiles)]
    expr = "+".join(f"eq(n\\,{n})" for n in picks)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
           "-vf", f"select='{expr}',scale=320:-1,tile={tiles}x1",
           "-frames:v", "1", str(out_path)]
    try:
        subprocess.run(cmd, check=True)
        return out_path
    except subprocess.CalledProcessError:
        return None


def normalize(params):
    """欠けている項目を既定値で埋め、ラベルを決める。"""
    p = dict(params)
    for key, value in FALLBACK.items():
        if p.get(key) is None:
            p[key] = value
    if not p.get("prompt"):
        raise GenerationError("プロンプトが空です")
    if p["width"] % 32 or p["height"] % 32:
        raise GenerationError("幅と高さは32の倍数にしてください")
    if not p.get("label"):
        p["label"] = slug(p["prompt"])
    return p


def run(params, port=DEFAULT_PORT, timeout=7200, on_progress=None):
    """生成して mp4 と履歴を残す。成功・失敗どちらも記録して record を返す。"""
    h3lib.ensure_dirs()
    p = normalize(params)
    rec_id = h3lib.new_id()
    record = {
        "kind": "generation", "id": rec_id, "created_at": h3lib.now_iso(),
        "status": "ok", "label": p["label"], "prompt": p["prompt"],
        "requested": {k: p[k] for k in INHERITED},
        "forked_from": p.get("forked_from"), "rating": None, "notes": "",
    }

    try:
        ev, runtime = stream_generate(p, port=port, timeout=timeout,
                                      on_progress=on_progress)
    except (GenerationError, OSError) as err:
        record["status"] = "error"
        record["error"] = str(err)
        h3lib.append(record)
        return record

    record["effective"] = {
        "frames": ev["frames"], "width": ev["width"], "height": ev["height"],
        "fps": ev.get("fps"),
        "duration_sec": round(ev["frames"] / ev.get("fps", 24), 2),
        "audio_sample_rate": ev.get("audio_sample_rate"),
        "audio_channels": ev.get("audio_channels"),
    }
    record["runtime"] = runtime

    out_path = p.get("out") or h3lib.OUTPUT_DIR / f"{rec_id}_{p['label']}.mp4"
    try:
        meta = {"id": rec_id, "prompt": p["prompt"],
                "requested": record["requested"], "effective": record["effective"]}
        mux(ev, out_path, meta)
    except (GenerationError, subprocess.CalledProcessError) as err:
        record["status"] = "error"
        record["error"] = f"mp4化に失敗: {err}"
        h3lib.append(record)
        return record

    strip = h3lib.THUMBS_DIR / f"{rec_id}.jpg"
    record["strip"] = h3lib.rel(make_strip(out_path, ev["frames"], strip))
    record["output"] = h3lib.rel(out_path)
    h3lib.append(record)
    return record
