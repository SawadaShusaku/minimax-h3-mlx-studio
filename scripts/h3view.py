#!/usr/bin/env python3
"""画面の見た目。静的ギャラリー（h3hist.py）とWebアプリ（h3app.py）で共用する。

カードのHTMLはここだけで作る。HTMXがサーバの返すHTML断片を差し替える方式なので、
描画ロジックをブラウザ側に複製せずに済む。
"""

import html
import random
from collections import Counter
from pathlib import Path

import h3core
import h3lib

# 解像度は mlx-serve が持つ一覧に合わせる。短辺768がモデルのネイティブ。
RESOLUTIONS = [
    (1024, 768, "1024×768 (4:3)"),
    (768, 768, "768×768 (正方)"),
    (1344, 768, "1344×768 (16:9・最も精細)"),
    (768, 1024, "768×1024 (3:4 縦)"),
    (768, 1344, "768×1344 (9:16 縦)"),
    (1536, 672, "1536×672 (21:9 シネマ)"),
    (960, 544, "960×544 (短辺544・非推奨)"),
]
# 17k+5 の階段。124未満は学習範囲外なので出さない。
FRAME_LADDER = [124 + 17 * i for i in range(15)]

CSS = """
:root { color-scheme: light dark; --bg:#faf9f7; --card:#fff; --fg:#1b1b1b;
        --muted:#6b6b6b; --line:#e3e0da; --chip:#f0ede8; --accent:#3b6ea5; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --card:#1e2126; --fg:#e8e6e3; --muted:#9aa0a6;
          --line:#2f343b; --chip:#282d33; --accent:#7aa7d9; } }
* { box-sizing:border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
       font:15px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:15px; margin:0 0 14px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
.wrap { display:grid; grid-template-columns:minmax(340px,420px) 1fr; gap:26px;
        align-items:start; max-width:1500px; }
@media (max-width:1000px) { .wrap { grid-template-columns:1fr; } }
.panel { position:sticky; top:24px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; margin-bottom:18px; }
.head { display:flex; flex-wrap:wrap; gap:10px; align-items:baseline;
        margin-bottom:12px; }
.id { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px;
      color:var(--muted); }
.label { font-weight:600; }
.star { color:#e0a92b; letter-spacing:1px; }
.media { width:100%; border-radius:8px; display:block; cursor:pointer;
         background:var(--chip); }
video.media { cursor:default; }
.prompt { white-space:pre-wrap; font-size:13.5px; margin:12px 0;
          padding:11px 13px; background:var(--chip); border-radius:8px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { font-size:12px; padding:3px 4px 3px 3px; border-radius:99px;
        background:var(--chip); color:var(--fg); display:inline-flex;
        align-items:center; gap:6px; font-family:ui-monospace,Menlo,monospace; }
.chip b { font-weight:500; font-size:11px; color:var(--muted); padding:1px 7px;
          border-radius:99px; background:var(--bg);
          font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif; }
.chip[title] { cursor:help; }
.notes { font-size:13px; color:var(--muted); margin-top:10px;
         border-left:3px solid var(--line); padding-left:10px; }
.err { color:#c0392b; font-size:13px; margin-top:8px; }
.fail { border-color:#c0392b55; }
label { display:block; font-size:13px; font-weight:600; margin:14px 0 5px; }
.hint { font-weight:400; color:var(--muted); font-size:12px; margin-left:6px; }
textarea, input, select { width:100%; padding:9px 11px; border:1px solid var(--line);
        border-radius:8px; background:var(--bg); color:var(--fg);
        font:14px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif; }
textarea { min-height:150px; resize:vertical; }
.row { display:flex; gap:10px; }
.row > * { flex:1; }
.check { display:flex; align-items:center; gap:8px; margin-top:14px;
         font-size:13px; font-weight:600; }
.check input { width:auto; }
button { font:600 14px -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;
         padding:10px 16px; border-radius:8px; border:1px solid var(--line);
         background:var(--chip); color:var(--fg); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff;
                 width:100%; margin-top:18px; padding:12px; }
button:disabled { opacity:.5; cursor:not-allowed; }
button.mini { font-size:12px; padding:5px 10px; font-weight:500; }
.actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px;
           align-items:center; }
.actions .sep { flex:1; }
#progress:empty { display:none; }
.prog { background:var(--card); border:1px solid var(--accent); border-radius:12px;
        padding:14px 16px; margin-bottom:18px; }
.bar { height:6px; background:var(--chip); border-radius:99px; overflow:hidden;
       margin-top:10px; }
.bar > div { height:100%; background:var(--accent); width:0; transition:width .4s; }
.prog .line { font-size:13px; color:var(--muted); }
.prog .stage { font-weight:600; color:var(--fg); font-size:14px; }
#q { width:100%; padding:9px 12px; border:1px solid var(--line); border-radius:8px;
     background:var(--card); color:var(--fg); font-size:14px; margin-bottom:18px; }
.modes { display:flex; flex-direction:column; gap:2px; margin-bottom:16px; }
.modes button { text-align:left; border:1px solid transparent; background:none;
                padding:9px 12px; font-weight:500; color:var(--fg); }
.modes button:hover { background:var(--chip); }
.modes button.on { background:var(--chip); border-color:var(--line); font-weight:600; }
.modes .d { display:block; font-size:12px; font-weight:400; color:var(--muted);
            margin-top:2px; }
.drop { border:1px dashed var(--line); border-radius:8px; padding:10px;
        text-align:center; }
.drop img { max-width:100%; border-radius:6px; margin-bottom:8px; display:none; }
.drop input[type=file] { border:none; padding:0; font-size:12px; background:none; }
.warn { font-size:12px; color:#b8860b; margin-top:6px; }
.inputs { display:flex; gap:8px; margin-bottom:10px; }
.inputs figure { margin:0; flex:1; }
.inputs img { width:100%; border-radius:6px; display:block; }
.inputs figcaption { font-size:11px; color:var(--muted); margin-top:3px; }
"""

CLICK_TO_PLAY_JS = """
document.body.addEventListener('click', e => {
  const img = e.target.closest('img.media');
  if (!img) return;
  const v = document.createElement('video');
  v.src = img.dataset.video; v.controls = true; v.autoplay = true;
  v.loop = true; v.className = 'media';
  img.replaceWith(v);
});
"""


MODE_HELP = {
    "t2va": "プロンプトだけで作る",
    "fl2va": "渡した画像が1コマ目になり、そこから動き出す",
    "interp": "始点と終点を指定し、その間を作る",
    "ref2va": "見本を渡して似せる。動画には現れない",
}


def mode_selector(current):
    """サイドバーのモード選択。押すと選択状態ごとフォームが差し替わる。"""
    out = []
    for key, name in h3lib.MODES.items():
        on = " on" if key == current else ""
        out.append(
            f'<button class="mini{on}" hx-get="/form?mode={key}" '
            f'hx-target="#composer" hx-swap="outerHTML">{html.escape(name)}'
            f'<span class="d">{html.escape(MODE_HELP[key])}</span></button>')
    return f'<div class="modes">{"".join(out)}</div>'


def composer_html(src=None, mode=None):
    """モード選択とフォームは選択状態を共有するので、まとめて差し替える。"""
    if mode is None:
        mode = (src or {}).get("mode", "t2va")
    return (f'<div id="composer">{mode_selector(mode)}'
            f"{form_html(src, mode)}</div>")


def image_field(name, label, existing=None):
    """画像の入力欄。ファイルは JS が base64 にして hidden へ入れる。"""
    esc = html.escape
    preview = f' src="/media/{esc(existing)}" style="display:block"' if existing else ""
    return f"""<label>{esc(label)}</label>
<div class="drop">
  <img id="{name}-prev"{preview} alt="">
  <input type="hidden" name="{name}_b64" value="{'keep' if existing else ''}">
  <input type="file" accept="image/png,image/jpeg" data-target="{name}">
</div>"""


REF_KINDS = (("image", "画像", "image/png,image/jpeg", 9),
             ("video", "動画", "video/*", 3),
             ("audio", "音声", "audio/*", 3))


def ref_fields(existing, size_mode="match"):
    """参照の入力欄。ファイルはJSが base64 にして hidden の JSON に積む。"""
    rows = []
    for kind, label, accept, cap in REF_KINDS:
        rows.append(
            f'<label>参照{label} <span class="hint">最大{cap}件</span></label>'
            f'<div class="drop"><input type="file" accept="{accept}" multiple '
            f'data-ref="{kind}"></div>')
    listed = "".join(
        f'<span class="chip"><b>{html.escape(r["kind"])}</b>'
        f'{html.escape(Path(r["path"]).name)}</span>'
        for r in (existing or []))
    # 派生元が max で作られていたら選択状態で出す。既定に戻ってしまうと、
    # 引き継いだつもりの設定が黙って変わる。
    size_options = "".join(
        f'<option value="{value}"{" selected" if value == size_mode else ""}>'
        f"{label}</option>"
        for value, label in (("match", "match（生成解像度に合わせる）"),
                             ("max", "max（元の大きさを活かす）")))
    return f"""<div class="notes">見本は動画には現れません。人物・画風・場所の
一貫性を保つために使います。全種類あわせて最大12件。</div>
{"".join(rows)}
<input type="hidden" name="refs_json" value="{'keep' if existing else '[]'}">
<div class="chips" id="ref-list">{listed}</div>
<label>参照画像の扱い <span class="hint">既定は match</span></label>
<select name="ref_image_size">{size_options}</select>"""


def form_html(src=None, mode=None):
    """生成フォーム。src を渡すとその設定で埋める（「この設定で作り直す」）。"""
    req = (src or {}).get("requested", {})
    inputs = (src or {}).get("inputs", {})
    if mode is None:
        mode = (src or {}).get("mode", "t2va")
    esc = html.escape

    prompt = esc(req.get("prompt", ""))
    width = req.get("width", 1024)
    height = req.get("height", 768)
    frames = req.get("frames", 124)
    defaults = h3core.defaults_for(mode)
    steps = req.get("steps") or defaults["steps"]
    seed = req.get("seed", random.randint(1, 99999))
    turbo = req.get("turbo", defaults["turbo"])
    fast = req.get("fast", defaults["fast"])
    chain = req.get("chain_windows", 1)
    loras = (src or {}).get("loras", [])

    res_options = "".join(
        f'<option value="{w}x{h}"{" selected" if (w, h) == (width, height) else ""}>'
        f"{esc(text)}</option>" for w, h, text in RESOLUTIONS)
    frame_options = "".join(
        f'<option value="{n}"{" selected" if n == frames else ""}>'
        f"{n}フレーム（{n / 24:.1f}秒）</option>" for n in FRAME_LADDER)
    chain_options = "".join(
        f'<option value="{n}"{" selected" if n == chain else ""}>'
        f"{n}窓{'' if n == 1 else f'（約{n * frames / 24:.0f}秒）'}</option>"
        for n in range(1, 7))

    image_fields = ""
    if mode == "ref2va":
        image_fields = ref_fields((src or {}).get("refs"),
                                  (src or {}).get("ref_image_size") or "match")
    if mode in ("fl2va", "interp"):
        image_fields = image_field("first_frame", "始点の画像",
                                   inputs.get("first_frame"))
    if mode == "interp":
        image_fields += image_field("last_frame", "終点の画像",
                                    inputs.get("last_frame"))

    lora_rows = ""
    for i in range(2):
        l = loras[i] if i < len(loras) else {}
        lora_rows += f"""<div class="row">
  <input type="text" name="lora_path" value="{esc(l.get('path', ''))}"
         placeholder=".safetensors への絶対パス">
  <input type="number" name="lora_scale" value="{l.get('scale', 1.0)}"
         step="0.05" min="0" max="2" style="max-width:90px">
</div>"""

    # 窓の連結は FL2VA のキーフレーム条件付けに乗る仕組みなので、
    # 参照つき（REF2VA）では選ばせない。
    chain_block = "" if mode == "ref2va" else (
        '<label>連結する窓 <span class="hint">前の窓の最終コマから続けて作る</span>'
        f'</label><select name="chain_windows" id="chain-sel">{chain_options}</select>')

    banner = ""
    if src:
        banner = (f'<div class="notes">{esc(src["id"])} の設定を引き継いでいます。'
                  f"変えたい項目だけ書き換えてください。</div>")

    return f"""<form id="form" hx-post="/generate" hx-target="#progress"
      hx-swap="innerHTML" hx-disabled-elt="#go">
  {banner}
  <input type="hidden" name="mode" value="{mode}">
  <input type="hidden" name="forked_from" value="{esc(src['id'] if src else '')}">
  {image_fields}

  <label>プロンプト
    <span class="hint">末尾の overall_soundscape: 以降が音声の指示になる</span></label>
  <textarea name="prompt" required
    placeholder="Hand-drawn 2D cel animation ... overall_soundscape: ...">{prompt}</textarea>

  <label>解像度 <span class="hint">短辺768がネイティブ</span></label>
  <select name="resolution">{res_options}</select>

  <label>1窓の長さ <span class="hint">124フレーム未満は学習範囲外</span></label>
  <select name="frames" id="frames-sel">{frame_options}</select>

  {chain_block}

  <div class="row">
    <div>
      <label>ステップ <span class="hint">{"Base既定30" if mode == "ref2va" else "turbo時6〜8"}</span></label>
      <input type="number" name="steps" value="{steps}" min="1" max="50">
    </div>
    <div>
      <label>シード <span class="hint">同じ値で再現</span></label>
      <input type="number" name="seed" value="{seed}" min="0">
    </div>
  </div>

  <label class="check"><input type="checkbox" name="turbo" value="1"
    {"checked" if turbo else ""}>turbo（{"Ref2VA対応LoRAを導入した場合のみ" if mode == "ref2va" else "4ステップ蒸留LoRAを使う"}）</label>
  <label class="check"><input type="checkbox" name="slow" value="1"
    {"" if fast else "checked"}>品質優先（アテンション再利用をやめる）</label>
  <div class="warn" id="slow-warn" style="display:none">
    時間が約4倍になります。かわりにメモリ使用量は下がります。</div>

  <label>スタイルLoRA <span class="hint">turboと重ねられる・空欄可</span></label>
  {lora_rows}

  <button id="go" class="primary" type="submit">生成する</button>
</form>"""


def fmt_duration(seconds):
    """秒数を読める長さにする。「かかった」のような述語で補わなくて済むように。"""
    if seconds is None:
        return None
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}秒"
    return f"{seconds // 60}分{seconds % 60:02d}秒"


def card_html(r, media_prefix="../", interactive=False):
    """1件分のカード。media_prefix は動画への相対パスの前置き。"""
    eff, req = r.get("effective") or {}, r["requested"]
    rt = r.get("runtime") or {}
    esc = html.escape
    # (ラベル, 値, 補足) — ラベルが単位の意味を示すので、値に述語を付けない。
    chain = req.get("chain_windows", 1)
    loras = r.get("loras") or []
    turbo_lora = r.get("turbo_lora") or {}
    turbo_name = turbo_lora.get("filename")
    turbo_hash = turbo_lora.get("sha256")
    if req.get("turbo") and turbo_lora.get("status") == "missing":
        turbo_value = "見つからない"
    elif turbo_name:
        turbo_value = turbo_name + (f" [{turbo_hash[:12]}]" if turbo_hash else "")
    else:
        # 旧履歴にはファイルの識別情報がない。推定で版を表示しない。
        turbo_value = "記録なし" if req.get("turbo") else None
    chips = [
        ("モード", h3lib.MODES.get(r.get("mode", "t2va")),
         MODE_HELP.get(r.get("mode", "t2va"))),
        ("解像度", f"{eff.get('width', req['width'])}×{eff.get('height', req['height'])}",
         "短辺768がモデルのネイティブ解像度"),
        ("フレーム", str(eff.get("frames", req["frames"])),
         "24fps。17k+5 の階段に丸められる"),
        ("尺", f"{eff['duration_sec']}秒" if eff.get("duration_sec") else None, None),
        ("連結", f"{chain}窓" if chain > 1 else None,
         "前の窓の最終コマを引き継いで繋いだ数。フレーム数は1窓あたりの指定"),
        ("ステップ", str(req["steps"]) if req.get("steps") else None,
         "拡散のステップ数。turbo使用時は6〜8が推奨"),
        ("turbo", "あり" if req.get("turbo") else "なし",
         "4ステップ蒸留LoRA。速いが動きが激しいと残像が出る"),
        ("Turbo LoRA", turbo_value,
         f"SHA-256: {turbo_hash}" if turbo_hash else
         "旧履歴には使用ファイルの識別情報がない"),
        ("品質優先", "あり" if req.get("fast") is False else None,
         "アテンション再利用をやめた状態。約4倍の時間がかかるがメモリは減る"),
        ("LoRA", "・".join(f"{Path(l['path']).stem} {l.get('scale', 1.0)}"
                           for l in loras) if loras else None,
         "turboに重ねたスタイルLoRAと、その強度"),
        ("シード", str(req["seed"]), "乱数の種。同じ値と同じ設定なら同じ映像が再現される"),
        ("生成時間", fmt_duration(rt.get("total_sec")), "重みのロードからmp4化までの合計"),
        ("1ステップ", fmt_duration(rt.get("sec_per_step")),
         "1ステップあたりの所要時間。設定を変えたときの見積もりに使う"),
        ("参照", "・".join(f"{k}{n}" for k, n in
                          sorted(Counter(x["kind"] for x in (r.get("refs") or [])).items()))
         or None, "生成に渡した見本の内訳。見本自体は動画には現れない"),
        ("参照の扱い", r.get("ref_image_size") if r.get("refs") else None,
         "match は生成解像度に合わせる、max は元の大きさを活かす"),
        ("派生元", r.get("forked_from"), "この記録の設定を引き継いだ元の生成"),
    ]
    chip_html = "".join(
        f'<span class="chip"{f" title={esc(hint)!r}" if hint else ""}>'
        f"<b>{esc(label)}</b>{esc(value)}</span>"
        for label, value, hint in chips if value
    )

    if r["status"] == "ok" and r.get("output"):
        video = media_prefix + r["output"]
        if r.get("strip"):
            media = (f'<img class="media" loading="lazy" '
                     f'src="{esc(media_prefix + r["strip"])}" '
                     f'data-video="{esc(video)}" alt="クリックで再生">')
        else:
            media = f'<video class="media" controls preload="none" src="{esc(video)}"></video>'
    else:
        media = f'<div class="err">失敗: {esc(r.get("error", "不明"))}</div>'

    # 入力画像。何から生成したかが分からないと記録として弱いので併記する。
    shots = "".join(
        f'<figure><img loading="lazy" src="{esc(media_prefix + path)}" alt="">'
        f"<figcaption>{caption}</figcaption></figure>"
        for key, caption in (("first_frame", "始点"), ("last_frame", "終点"))
        for path in [(r.get("inputs") or {}).get(key)] if path)
    shots += "".join(
        f'<figure><img loading="lazy" src="{esc(media_prefix + sample["path"])}" alt="">'
        f'<figcaption>圧縮前 f{sample["frame"]}</figcaption></figure>'
        for sample in (r.get("raw_frames") or []))
    # 参照画像も並べる。何を見本にしたか分からない記録は価値が落ちる。
    shots += "".join(
        f'<figure><img loading="lazy" src="{esc(media_prefix + ref["path"])}" alt="">'
        f'<figcaption>見本 {esc(ref.get("name", ""))}</figcaption></figure>'
        for ref in (r.get("refs") or []) if ref["kind"] == "image")
    shots = f'<div class="inputs">{shots}</div>' if shots else ""

    stars = f'<span class="star">{"★" * r["rating"]}</span>' if r.get("rating") else ""
    notes = f'<div class="notes">{esc(r["notes"])}</div>' if r.get("notes") else ""
    hay = esc((r["prompt"] + " " + r.get("label", "") + " " + r["id"] + " "
               + h3lib.MODES.get(r.get("mode", "t2va"), "")).lower())

    actions = ""
    if interactive:
        rate_buttons = "".join(
            f'<button class="mini" hx-post="/rate" hx-target="closest .card" '
            f'hx-swap="outerHTML" hx-vals=\'{{"id":"{r["id"]}","rating":{n}}}\'>'
            f'{"★" * n}</button>' for n in range(1, 6))
        fork = ""
        if r["status"] == "ok":
            fork = (f'<button class="mini" hx-get="/form?from={r["id"]}" '
                    f'hx-target="#composer" hx-swap="outerHTML">この設定で作り直す</button>')
        actions = (f'<div class="actions">{fork}<span class="sep"></span>'
                   f'{rate_buttons}</div>')

    return f"""<div class="card{'' if r['status'] == 'ok' else ' fail'}" data-hay="{hay}">
  <div class="head"><span class="label">{esc(r.get('label', ''))}</span>
    <span class="id">{esc(r['id'])}</span>{stars}</div>
  {shots}{media}
  <div class="prompt">{esc(r['prompt'])}</div>
  <div class="chips">{chip_html}</div>
  {notes}{actions}
</div>"""
