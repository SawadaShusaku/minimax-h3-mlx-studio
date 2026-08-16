#!/usr/bin/env python3
"""生成履歴の読み書き。h3gen.py と h3hist.py が共用する。

履歴は history/history.jsonl に1行1レコードで追記する。書き込みは常に追記で、
過去の行を書き換えない。種別は kind で区別する:

  generation — 1回の生成。成功も失敗も残す（避けるべき設定の根拠になるため）
  annotation — 後から付ける評価やメモ。target で対象の id を指す

読むときに annotation を generation へ畳み込むので、評価を付け直しても
履歴そのものは失われない。
"""

import datetime
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT / "models" / "ddalcu"
OUTPUT_DIR = PROJECT / "outputs"
INPUTS_DIR = PROJECT / "inputs"      # 生成に渡したキーフレーム画像
HISTORY_DIR = PROJECT / "history"
HISTORY_FILE = HISTORY_DIR / "history.jsonl"
THUMBS_DIR = HISTORY_DIR / "thumbs"
GALLERY_FILE = HISTORY_DIR / "gallery.html"

# 生成モード。どの入力を要求するかがモードで決まる。
MODES = {
    "t2va": "テキストから",
    "fl2va": "画像から",
    "interp": "2枚の間を補間",
    "ref2va": "参照つき",
}

# モードごとに使うパック。FL2VA と REF2VA は DiT の重みだけが違う別物で、
# 参照を扱えるのは REF2VA、キーフレームと窓の連結を扱えるのは FL2VA だけ。
# 1つのサーバに --model-dir で両方を置き、リクエストの "model" で選び分ける。
PACKS = {
    "fl2va": "MiniMax-H3-FL2VA-MLX-Serve-8bit",
    "ref2va": "MiniMax-H3-REF2VA-MLX-Serve-8bit",
}
MODE_PACK = {"t2va": "fl2va", "fl2va": "fl2va", "interp": "fl2va", "ref2va": "ref2va"}


def pack_for(mode):
    """そのモードを実際に処理できるパックのモデルidを返す。"""
    return PACKS[MODE_PACK.get(mode, "fl2va")]


def ensure_dirs():
    for d in (OUTPUT_DIR, INPUTS_DIR, HISTORY_DIR, THUMBS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def new_id():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def rel(path):
    """プロジェクト基準の相対パスにする（フォルダごと移動しても壊れないように）。"""
    if path is None:
        return None
    p = Path(path)
    try:
        return str(p.resolve().relative_to(PROJECT))
    except ValueError:
        return str(p)


def append(record):
    ensure_dirs()
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load():
    """generation を古い順に返す。annotation は畳み込み済み。"""
    if not HISTORY_FILE.exists():
        return []
    gens, order = {}, []
    for lineno, line in enumerate(HISTORY_FILE.read_text("utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            print(f"  警告: history.jsonl の {lineno} 行目を読めないので飛ばします")
            continue
        kind = rec.get("kind")
        if kind == "generation":
            if rec["id"] not in gens:
                order.append(rec["id"])
            gens[rec["id"]] = rec
        elif kind == "annotation":
            target = gens.get(rec.get("target"))
            if target is None:
                continue
            for key in ("rating", "notes"):
                if rec.get(key) is not None:
                    target[key] = rec[key]
    return [gens[i] for i in order]


def find(rec_id):
    """id の完全一致、なければ前方一致で1件に絞れたものを返す。"""
    records = load()
    exact = [r for r in records if r["id"] == rec_id]
    if exact:
        return exact[0]
    hits = [r for r in records if r["id"].startswith(rec_id)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit(f"id '{rec_id}' が {len(hits)} 件に一致します。もっと長く指定してください")
    raise SystemExit(f"id '{rec_id}' の記録が見つかりません")
