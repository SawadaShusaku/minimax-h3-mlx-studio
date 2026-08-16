#!/usr/bin/env python3
"""生成の実処理。CLI（h3gen.py）とWebアプリ（h3app.py）が共用する。

mlx-serve の /v1/video/generations は base64 の rgb8 フレームと pcm_s16le 音声を
返すだけなので、mp4 化はこちら側で行う。進捗は SSE で流れてくるので、
呼び出し側が受け取れるよう on_progress コールバックで中継する。
"""

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import h3lib

DEFAULT_PORT = 11434
# 引き継げる設定。--from と「この設定で作り直す」はこれらだけを継承する。
INHERITED = ("prompt", "frames", "width", "height", "steps", "seed", "turbo",
             "fast", "chain_windows")
FALLBACK = {"frames": 124, "width": 1024, "height": 768, "steps": 6,
            "seed": 7, "turbo": True, "fast": True, "chain_windows": 1}
# REF2VA は別チェックポイントで、FL2VA パック同梱の Turbo LoRA を流用しない。
# 対応 LoRA を明示的に導入するまでは、mlx-serve の Base 既定で始める。
MODE_FALLBACK = {
    "ref2va": {"steps": 30, "turbo": False},
}
MAX_CHAIN = 6      # サーバ側の上限
MAX_LORAS = 7      # 実際は8枠だが turbo が1枠を使う

# 参照（REF2VA）の上限。種類ごとの上限を全部満たしても合計12を超えると
# モデルが与えられたことのない組み合わせになるので、合計も必ず見る。
MAX_REF = {"image": 9, "video": 3, "audio": 3}
MAX_REF_TOTAL = 12
MIN_REF_VIDEO_FRAMES = 5   # これ未満だと条件付けに使う潜在フレームが1枚も取れない
REF_VIDEO_FRAMES = 17      # 参照動画から抜くコマ数（VAEの1クリップ分）


class GenerationError(RuntimeError):
    pass


def defaults_for(mode):
    """共通値に、そのモード固有の安全な既定値を重ねて返す。"""
    defaults = dict(FALLBACK)
    defaults.update(MODE_FALLBACK.get(mode, {}))
    return defaults


def _sha256(path, chunk_size=1024 * 1024):
    """大きなLoRAをメモリへ載せずに識別する。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def turbo_lora_info(mode):
    """リクエスト時点で選択されるTurbo LoRAの識別情報を返す。

    mlx-serve は各モデルパック直下の `turbo_lora.safetensors` を読む。
    シンボリックリンクで版を切り替えても追跡できるよう、入口と解決先を
    両方記録する。サーバの応答を待つ前に呼ぶため、失敗した生成にも残せる。
    """
    pack = h3lib.pack_for(mode)
    configured = h3lib.MODELS_DIR / pack / "turbo_lora.safetensors"
    info = {
        "model_pack": pack,
        "configured_path": h3lib.rel(configured),
    }
    if not configured.is_file():
        info["status"] = "missing"
        return info

    try:
        resolved = configured.resolve()
        stat = resolved.stat()
        info.update({
            "status": "found",
            "filename": resolved.name,
            "resolved_path": h3lib.rel(resolved),
            "sha256": _sha256(resolved),
            "size_bytes": stat.st_size,
        })
    except OSError as err:
        # 識別情報を読めない場合も生成履歴自体は失わない。
        info.update({"status": "unreadable", "error": str(err)})
    return info


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


def ref_video_frames_b64(path, count=REF_VIDEO_FRAMES):
    """参照動画から等間隔でコマを抜き、base64 PNG の配列にする。

    APIが受け取るのはコマの配列なので、動画ファイルはこちらで展開する。
    尺の長い素材でも「動きと画風の見本」として使える枚数に間引く。
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_packets", "-show_entries", "stream=nb_read_packets",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    total = int((probe.stdout or "0").strip() or 0)
    if total < MIN_REF_VIDEO_FRAMES:
        raise GenerationError(
            f"参照動画が短すぎます（{total}コマ）。{MIN_REF_VIDEO_FRAMES}コマ以上必要です")
    # 指摘3: 抜く枚数が総コマ数より多いときは、総コマ数を除数にする。
    # 17で割ったままだと5コマの動画が2枚しか取れず、下限割れで弾かれる。
    n = min(count, total)
    picks = sorted({int(total * i / n) for i in range(n)})
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        expr = "+".join(f"eq(n\\,{k})" for k in picks)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                 "-vf", f"select='{expr}'", "-vsync", "0",
                 str(Path(tmp) / "f%03d.png")], check=True)
        except subprocess.CalledProcessError as err:
            raise GenerationError(f"参照動画を読めませんでした: {path.name}") from err
        for f in sorted(Path(tmp).glob("f*.png")):
            out.append(base64.b64encode(f.read_bytes()).decode())
    if len(out) < MIN_REF_VIDEO_FRAMES:
        raise GenerationError("参照動画からコマを取り出せませんでした")
    return out


def ref_audio_wav_b64(path):
    """参照音声を、APIが受け取れる PCM16 の WAV にして base64 にする。"""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "ref.wav"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                            "-c:a", "pcm_s16le", str(wav)], check=True)
        except subprocess.CalledProcessError as err:
            raise GenerationError(f"参照音声を読めませんでした: {Path(path).name}") from err
        return base64.b64encode(wav.read_bytes()).decode()


def validate_refs(refs):
    """種類ごとの上限と、合計12という本当の上限の両方を見る。"""
    counts = {k: 0 for k in MAX_REF}
    for ref in refs:
        kind = ref.get("kind")
        if kind not in MAX_REF:
            raise GenerationError(f"参照の種類が不正です: {kind}")
        counts[kind] += 1
    for kind, n in counts.items():
        if n > MAX_REF[kind]:
            raise GenerationError(f"参照の{kind}は最大{MAX_REF[kind]}件です（{n}件）")
    if sum(counts.values()) > MAX_REF_TOTAL:
        raise GenerationError(
            f"参照は全種類あわせて最大{MAX_REF_TOTAL}件です（{sum(counts.values())}件）")
    return counts


def refs_to_body(refs):
    """保存済みの参照ファイルを、API が受け取る形に変換する。"""
    images, videos, audios = [], [], []
    for ref in refs:
        path = h3lib.PROJECT / ref["path"]
        if not path.is_file():
            raise GenerationError(f"参照ファイルが見つかりません: {ref['path']}")
        if ref["kind"] == "image":
            images.append(base64.b64encode(path.read_bytes()).decode())
        elif ref["kind"] == "video":
            videos.append({"frames": ref_video_frames_b64(path)})
        else:
            audios.append(ref_audio_wav_b64(path))
    body = {}
    if images:
        body["ref_images"] = images
    if videos:
        body["ref_videos"] = videos
    if audios:
        body["ref_audios"] = audios
    return body


def build_body(params):
    """/v1/video/generations に送るJSONを組み立てる。

    num_frames は「1窓あたり」の値で、chain_windows 個の窓が前の窓の最終フレームを
    引き継いで連結される。応答が返す frames は連結後の総数になる。
    """
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
    if params.get("chain_windows", 1) > 1:
        body["chain_windows"] = params["chain_windows"]
    # fast は既定 true。品質優先のときだけ明示的に false を送る
    # （このトグルは速度を捨てる代わりに品質とメモリの両方が良くなる）。
    if params.get("fast") is False:
        body["fast"] = False

    for field, key in (("first_frame_image", "first_frame"),
                       ("last_frame_image", "last_frame")):
        data = params.get(key + "_b64")
        if data:
            body[field] = data

    loras = params.get("loras") or []
    if loras:
        body["lora_paths"] = [str(Path(l["path"]).resolve()) for l in loras]
        body["lora_scales"] = [float(l.get("scale", 1.0)) for l in loras]

    # モードによって処理できるパックが違うので、リクエストで名指しする。
    body["model"] = h3lib.pack_for(params.get("mode", "t2va"))

    refs = params.get("refs") or []
    if refs:
        body.update(refs_to_body(refs))
        body["ref_image_size"] = params.get("ref_image_size", "match")
    return body


def stream_generate(params, port=DEFAULT_PORT, timeout=7200, on_progress=None):
    """SSE を読みながら complete イベントと所要時間を返す。"""
    body = build_body(params)

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


def save_raw_samples(vraw, frames, width, height, rec_id):
    """MP4圧縮前の開始・中間・終了フレームを可逆PNGで保存する。"""
    picks = sorted({0, frames // 2, frames - 1})
    expr = "+".join(f"eq(n\\,{n})" for n in picks)
    pattern = h3lib.RAW_FRAMES_DIR / f"{rec_id}_%02d.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{width}x{height}", "-i", str(vraw),
         "-vf", f"select='{expr}'", "-vsync", "0", str(pattern)],
        check=True,
    )
    paths = sorted(h3lib.RAW_FRAMES_DIR.glob(f"{rec_id}_*.png"))
    if len(paths) != len(picks):
        raise GenerationError(
            f"圧縮前フレームの保存数が不一致: {len(paths)} != {len(picks)}")
    return [{"frame": frame, "path": h3lib.rel(path)}
            for frame, path in zip(picks, paths)]


def mux(ev, out_path, metadata, rec_id=None):
    """rgb8フレームをMP4に束ね、圧縮前サンプルと設定も残す。"""
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
        raw_samples = save_raw_samples(vraw, frames, w, h, rec_id) if rec_id else []
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
        return raw_samples
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_strip(video_path, frames, out_path, tiles=4, width=200, quality=6):
    """動きが一目で分かるように、等間隔の数コマを横に並べた画像を作る。

    これは git で追跡する唯一の視覚的記録（動画は追跡しない）なので、
    一覧で内容を判断できる下限まで小さくしてある。1枚およそ20KB。
    """
    picks = [int(frames * i / tiles) for i in range(tiles)]
    expr = "+".join(f"eq(n\\,{n})" for n in picks)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
           "-vf", f"select='{expr}',scale={width}:-1,tile={tiles}x1",
           "-q:v", str(quality), "-frames:v", "1", str(out_path)]
    try:
        subprocess.run(cmd, check=True)
        return out_path
    except subprocess.CalledProcessError:
        return None


def mode_of(params):
    """入力の有無から生成モードを決める。"""
    if params.get("refs"):
        return "ref2va"
    # CLIでは画像を読む前に既定値を選ぶため、パス指定も入力として数える。
    has_first = bool(params.get("first_frame_b64") or params.get("first_frame_path"))
    has_last = bool(params.get("last_frame_b64") or params.get("last_frame_path"))
    if has_first and has_last:
        return "interp"
    if has_first or has_last:
        return "fl2va"
    return "t2va"


def read_image_b64(path):
    """画像ファイルを base64 にする。拡張子も返す（保存時に使う）。"""
    p = Path(path)
    if not p.is_file():
        raise GenerationError(f"画像が見つかりません: {path}")
    if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise GenerationError(f"画像は PNG か JPEG にしてください: {p.name}")
    return base64.b64encode(p.read_bytes()).decode(), p.suffix.lower()


def normalize(params):
    """欠けている項目を既定値で埋め、値の妥当性を確かめる。"""
    p = dict(params)
    # 入力から先にパーティションを決めないと、REF2VA に FL2VA/Turbo の
    # 共通既定値を入れてしまう。モード固有値は欠けている項目だけに適用する。
    p["mode"] = mode_of(p)
    for key, value in defaults_for(p["mode"]).items():
        if p.get(key) is None:
            p[key] = value
    if not p.get("prompt"):
        raise GenerationError("プロンプトが空です")
    if p["width"] % 32 or p["height"] % 32:
        raise GenerationError("幅と高さは32の倍数にしてください")
    if not 1 <= p["chain_windows"] <= MAX_CHAIN:
        raise GenerationError(f"連結する窓の数は 1〜{MAX_CHAIN} にしてください")
    if len(p.get("loras") or []) > MAX_LORAS:
        raise GenerationError(f"LoRAは最大{MAX_LORAS}枚です（turboが1枠を使うため）")
    for lora in p.get("loras") or []:
        if not Path(lora["path"]).is_file():
            raise GenerationError(f"LoRAが見つかりません: {lora['path']}")

    # ファイルパスで渡された画像はここで base64 にしておく（CLI 経路）。
    for key in ("first_frame", "last_frame"):
        if p.get(key + "_path") and not p.get(key + "_b64"):
            p[key + "_b64"], p[key + "_ext"] = read_image_b64(p[key + "_path"])
    # REF2VA と FL2VA は別のDiTなので、片方の機能はもう片方では使えない。
    # サーバも400で拒否するが、待たされる前にこちらで止める。
    if p["mode"] == "ref2va":
        validate_refs(p["refs"])
        if p["chain_windows"] > 1:
            raise GenerationError(
                "窓の連結はFL2VAのキーフレーム条件付けに乗る仕組みなので、"
                "参照つき（REF2VA）とは併用できません")
        if p.get("first_frame_b64") or p.get("last_frame_b64"):
            raise GenerationError("参照つき（REF2VA）ではキーフレーム画像を使えません")
        if p.get("ref_image_size") not in (None, "match", "max"):
            raise GenerationError("ref_image_size は match か max にしてください")
    if not p.get("label"):
        p["label"] = slug(p["prompt"])
    return p


def save_inputs(params, rec_id):
    """渡したキーフレームを inputs/ に保存し、履歴から参照できるようにする。

    何から生成したかが分からないと記録の価値が落ちるので、入力も残す。
    """
    saved = {}
    for key in ("first_frame", "last_frame"):
        data = params.get(key + "_b64")
        if not data:
            continue
        ext = params.get(key + "_ext") or ".png"
        path = h3lib.INPUTS_DIR / f"{rec_id}_{key}{ext}"
        path.write_bytes(base64.b64decode(data))
        saved[key] = h3lib.rel(path)
    return saved


def run(params, port=DEFAULT_PORT, timeout=7200, on_progress=None):
    """生成して mp4 と履歴を残す。成功・失敗どちらも記録して record を返す。"""
    h3lib.ensure_dirs()
    p = normalize(params)
    rec_id = h3lib.new_id()
    record = {
        "kind": "generation", "id": rec_id, "created_at": h3lib.now_iso(),
        "status": "ok", "mode": p["mode"], "label": p["label"],
        "prompt": p["prompt"],
        "requested": {k: p[k] for k in INHERITED},
        "turbo_lora": turbo_lora_info(p["mode"]) if p.get("turbo") else None,
        "loras": p.get("loras") or [],
        "refs": p.get("refs") or [],
        "ref_image_size": p.get("ref_image_size") if p.get("refs") else None,
        "inputs": save_inputs(p, rec_id),
        "forked_from": p.get("forked_from"), "rating": None, "notes": "",
    }

    try:
        ev, runtime = stream_generate(p, port=port, timeout=timeout,
                                      on_progress=on_progress)
    except (GenerationError, OSError, subprocess.CalledProcessError) as err:
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
                "requested": record["requested"], "effective": record["effective"],
                "turbo_lora": record["turbo_lora"], "loras": record["loras"]}
        record["raw_frames"] = mux(ev, out_path, meta, rec_id=rec_id)
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
