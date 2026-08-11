#!/usr/bin/env python3
"""生成履歴の閲覧・評価・静的ギャラリー生成（CLI）。

  ./h3hist.py list                        直近の生成を一覧
  ./h3hist.py show <id>                   1件の全項目
  ./h3hist.py rate <id> 4 --notes "..."   評価とメモを付ける
  ./h3hist.py gallery                     history/gallery.html を作り直す

gallery.html は history.jsonl から毎回作り直す派生物なので、消しても失われない。
生成もその場で行いたいときは h3app.py（Webアプリ）を使う。
"""

import argparse
import json
import sys
import webbrowser

import h3lib
import h3view


def cmd_list(args):
    records = h3lib.load()
    if not records:
        print("履歴がまだありません")
        return
    if args.failed:
        records = [r for r in records if r["status"] != "ok"]
    if args.mode:
        records = [r for r in records if r.get("mode", "t2va") == args.mode]
    if args.search:
        needle = args.search.lower()
        records = [r for r in records if needle in r["prompt"].lower()]
    records = records[-args.limit:]

    print(f"{'ID':<16} {'状態':<5} {'モード':<8} {'解像度':<10} {'尺':<7} "
          f"{'step':<5} {'seed':<6} {'評価':<4} ラベル")
    print("-" * 100)
    for r in records:
        eff, req = r.get("effective") or {}, r["requested"]
        res = f"{eff.get('width', req['width'])}x{eff.get('height', req['height'])}"
        dur = f"{eff['duration_sec']}s" if eff.get("duration_sec") else "-"
        rating = "★" * r["rating"] if r.get("rating") else "-"
        state = "ok" if r["status"] == "ok" else "失敗"
        mode = h3lib.MODES.get(r.get("mode", "t2va"), "?")
        print(f"{r['id']:<16} {state:<5} {mode:<8} {res:<10} {dur:<7} "
              f"{req['steps'] or '-':<5} {req['seed']:<6} {rating:<4} {r.get('label','')}")


def cmd_show(args):
    print(json.dumps(h3lib.find(args.id), ensure_ascii=False, indent=2))


def cmd_rate(args):
    target = h3lib.find(args.id)
    if args.rating is not None and not 1 <= args.rating <= 5:
        raise SystemExit("評価は1〜5で指定してください")
    h3lib.append({"kind": "annotation", "target": target["id"],
                  "created_at": h3lib.now_iso(),
                  "rating": args.rating, "notes": args.notes})
    print(f"{target['id']} に記録しました"
          f"{f'（評価 {args.rating}）' if args.rating else ''}")


def cmd_gallery(args):
    records = list(reversed(h3lib.load()))
    ok = sum(1 for r in records if r["status"] == "ok")
    body = "\n".join(h3view.card_html(r) for r in records)
    page = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiniMax-H3 生成履歴</title><style>{h3view.CSS}</style></head><body>
<h1>MiniMax-H3 生成履歴</h1>
<div class="sub">{len(records)} 件（成功 {ok} / 失敗 {len(records) - ok}）・新しい順。
サムネイルをクリックすると再生します。</div>
<input id="q" type="search" placeholder="プロンプトやラベルで絞り込み">
{body}
<script>
{h3view.CLICK_TO_PLAY_JS}
const q = document.getElementById('q');
q.addEventListener('input', () => {{
  const n = q.value.toLowerCase();
  document.querySelectorAll('.card').forEach(c => {{
    c.style.display = c.dataset.hay.includes(n) ? '' : 'none';
  }});
}});
</script></body></html>"""
    h3lib.ensure_dirs()
    h3lib.GALLERY_FILE.write_text(page, encoding="utf-8")
    print(f"生成しました: {h3lib.GALLERY_FILE}（{len(records)} 件）")
    if args.open:
        webbrowser.open(h3lib.GALLERY_FILE.as_uri())


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="一覧")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--search", help="プロンプト本文で絞り込み")
    pl.add_argument("--failed", action="store_true", help="失敗したものだけ")
    pl.add_argument("--mode", choices=list(h3lib.MODES), help="モードで絞り込み")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("show", help="1件の詳細")
    ps.add_argument("id")
    ps.set_defaults(func=cmd_show)

    pr = sub.add_parser("rate", help="評価とメモを付ける")
    pr.add_argument("id")
    pr.add_argument("rating", type=int, nargs="?", help="1〜5")
    pr.add_argument("--notes")
    pr.set_defaults(func=cmd_rate)

    pg = sub.add_parser("gallery", help="gallery.html を作り直す")
    pg.add_argument("--open", action="store_true", help="生成後にブラウザで開く")
    pg.set_defaults(func=cmd_gallery)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
