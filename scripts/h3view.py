#!/usr/bin/env python3
"""画面の見た目。静的ギャラリー（h3hist.py）とWebアプリ（h3app.py）で共用する。

カードのHTMLはここだけで作る。HTMXがサーバの返すHTML断片を差し替える方式なので、
描画ロジックをブラウザ側に複製せずに済む。
"""

import html

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
    chips = [
        ("解像度", f"{eff.get('width', req['width'])}×{eff.get('height', req['height'])}",
         "短辺768がモデルのネイティブ解像度"),
        ("フレーム", str(eff.get("frames", req["frames"])),
         "24fps。17k+5 の階段に丸められる"),
        ("尺", f"{eff['duration_sec']}秒" if eff.get("duration_sec") else None, None),
        ("ステップ", str(req["steps"]) if req.get("steps") else None,
         "拡散のステップ数。turbo使用時は6〜8が推奨"),
        ("turbo", "あり" if req.get("turbo") else "なし",
         "4ステップ蒸留LoRA。速いが動きが激しいと残像が出る"),
        ("シード", str(req["seed"]), "乱数の種。同じ値と同じ設定なら同じ映像が再現される"),
        ("生成時間", fmt_duration(rt.get("total_sec")), "重みのロードからmp4化までの合計"),
        ("1ステップ", fmt_duration(rt.get("sec_per_step")),
         "1ステップあたりの所要時間。設定を変えたときの見積もりに使う"),
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

    stars = f'<span class="star">{"★" * r["rating"]}</span>' if r.get("rating") else ""
    notes = f'<div class="notes">{esc(r["notes"])}</div>' if r.get("notes") else ""
    hay = esc((r["prompt"] + " " + r.get("label", "") + " " + r["id"]).lower())

    actions = ""
    if interactive:
        rate_buttons = "".join(
            f'<button class="mini" hx-post="/rate" hx-target="closest .card" '
            f'hx-swap="outerHTML" hx-vals=\'{{"id":"{r["id"]}","rating":{n}}}\'>'
            f'{"★" * n}</button>' for n in range(1, 6))
        fork = ""
        if r["status"] == "ok":
            fork = (f'<button class="mini" hx-get="/form?from={r["id"]}" '
                    f'hx-target="#form" hx-swap="outerHTML">この設定で作り直す</button>')
        actions = (f'<div class="actions">{fork}<span class="sep"></span>'
                   f'{rate_buttons}</div>')

    return f"""<div class="card{'' if r['status'] == 'ok' else ' fail'}" data-hay="{hay}">
  <div class="head"><span class="label">{esc(r.get('label', ''))}</span>
    <span class="id">{esc(r['id'])}</span>{stars}</div>
  {media}
  <div class="prompt">{esc(r['prompt'])}</div>
  <div class="chips">{chip_html}</div>
  {notes}{actions}
</div>"""
