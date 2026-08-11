#!/usr/bin/env python3
"""MiniMax-H3 の動画生成（CLI）。実処理は h3core、履歴は h3lib が持つ。

例:
  ./h3gen.py --prompt "..." --frames 124 --width 1024 --height 768 --steps 6 --turbo
  ./h3gen.py --from 20260811-1523 --seed 99      # 過去の設定を引き継いで派生
"""

import argparse
import sys
from pathlib import Path

import h3core
import h3lib


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt")
    p.add_argument("--frames", type=int, help="17k+5 の階段に丸められる。124以上を推奨")
    p.add_argument("--width", type=int, help="32の倍数。短辺768がネイティブ")
    p.add_argument("--height", type=int, help="32の倍数")
    p.add_argument("--steps", type=int, help="turbo使用時は6〜8が推奨")
    p.add_argument("--seed", type=int)
    p.add_argument("--turbo", action=argparse.BooleanOptionalAction, default=None,
                   help="蒸留LoRAを使う（--no-turbo で無効）")
    p.add_argument("--fast", action=argparse.BooleanOptionalAction, default=None,
                   help="--no-fast で品質優先（約4倍の時間、メモリは減る）")
    p.add_argument("--chain-windows", dest="chain_windows", type=int,
                   help="窓を連結して長尺にする（1〜6）。--frames は1窓あたり")
    p.add_argument("--first-frame", dest="first_frame_path",
                   help="1コマ目にする画像（PNG/JPEG）")
    p.add_argument("--last-frame", dest="last_frame_path",
                   help="最終コマにする画像。--first-frame と併用で補間になる")
    p.add_argument("--lora", action="append", default=[], metavar="PATH[:SCALE]",
                   help="スタイルLoRA。複数指定可（例 --lora /path/a.safetensors:0.8）")
    p.add_argument("--from", dest="from_id", metavar="ID",
                   help="過去の記録から設定を引き継ぐ（明示した項目だけ上書き）")
    p.add_argument("--label", help="出力ファイル名につける短い名前")
    p.add_argument("--port", type=int, default=h3core.DEFAULT_PORT)
    p.add_argument("--out", help="出力先。相対パスは outputs/ 基準")
    p.add_argument("--timeout", type=int, default=7200)
    a = p.parse_args()

    a.forked_from = None
    if a.from_id:
        src = h3lib.find(a.from_id)
        a.forked_from = src["id"]
        for key in h3core.INHERITED:
            if getattr(a, key) is None:
                setattr(a, key, src["requested"][key])
        if a.label is None:
            a.label = src.get("label")
        print(f"  {src['id']} から設定を引き継ぎました")

    if a.out:
        out = Path(a.out)
        a.out = out if out.is_absolute() else h3lib.OUTPUT_DIR / out
    return a


def show_progress(ev):
    line = (f"  [{ev['elapsed']:7.1f}s] {ev['stage']:<16} "
            f"{ev.get('step', '?')}/{ev.get('total', '?')}")
    print(line, end="\r", flush=True)


def main():
    a = parse_args()
    params = {k: getattr(a, k) for k in h3core.INHERITED}
    loras = []
    for spec in a.lora:
        path, _, scale = spec.rpartition(":")
        if not path:                       # ":" がなければ全体がパス、強度は1.0
            path, scale = spec, "1.0"
        loras.append({"path": path, "scale": float(scale)})
    params.update(label=a.label, forked_from=a.forked_from, out=a.out, loras=loras,
                  first_frame_path=a.first_frame_path,
                  last_frame_path=a.last_frame_path)

    record = h3core.run(params, port=a.port, timeout=a.timeout,
                        on_progress=show_progress)
    print()

    if record["status"] != "ok":
        print(f"  失敗として記録しました: {record['id']}")
        print(f"  {record['error']}")
        return 1

    eff, rt = record["effective"], record["runtime"]
    # 連結時は「1窓あたりの指定」と「連結後の総数」を比べても意味がないので黙る
    if (record["requested"]["chain_windows"] == 1
            and eff["frames"] != record["requested"]["frames"]):
        print(f"  注意: フレーム数が {record['requested']['frames']} → {eff['frames']} "
              f"に丸められました（17k+5の階段）")
    print(f"  生成完了: {rt['total_sec']}s"
          + (f"（{rt['sec_per_step']}s/step）" if rt["sec_per_step"] else ""))
    print(f"  mp4 書き出し: {record['output']} "
          f"({eff['frames']}f {eff['width']}x{eff['height']} {eff['duration_sec']}s)")
    print(f"  履歴に記録: {record['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
