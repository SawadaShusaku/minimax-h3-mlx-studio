# minimax-h3-mlx-studio

MiniMax-H3（Hailuo 3.0）を Apple Silicon 上でローカル実行する環境。
映像とステレオ音声を1パスで同時生成する。

*[English version](README.md)*

> ### ⚠️ モデルの地域制限
>
> MiniMax H3 Community License は利用可能地域を
> **欧州連合・英国・大韓民国・アメリカ合衆国を除く全世界**と定義し、
> それ以外の地域での使用・複製・改変・配布・展示を禁じている。
>
> **このリポジトリに重みは含まれない**（コードがHugging Faceから取得する）。
> ダウンロードする前に、自分の所在地が対象かどうかを確認すること。

- 実行環境: Mac Studio / M4 Max / 128GB / macOS 26.6.1
- ランタイム: [mlx-serve](https://github.com/ddalcu/mlx-serve) 26.8.4（Homebrew、Python不要）
- モデル: [ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit](https://huggingface.co/ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit)（65GB、8bit量子化）

## 構成

```
minimax-h3-mlx-studio/
├── models/ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit/   重み一式（65GB）
├── scripts/serve.sh                                  mlx-serve 起動
├── scripts/h3app.py                                  Webアプリ（生成＋履歴）
├── scripts/h3gen.py                                  生成（CLI）
├── scripts/h3hist.py                                 履歴の閲覧・評価・静的ギャラリー
├── scripts/h3core.py                                 生成の実処理（共用）
├── scripts/h3lib.py                                  履歴の読み書き（共用）
├── scripts/h3view.py                                 CSS・カードのHTML（共用）
├── web/htmx.min.js                                   同梱（CDN不要）
├── outputs/                                          生成した動画
├── inputs/                                           渡したキーフレーム画像
├── history/history.jsonl                             全生成の記録（1行1件・追記のみ）
├── history/gallery.html                              静的な一覧（再生成可能）
└── logs/                                             ログ
```

## 使い方（Webアプリ）

mlx-serve を起動してから、アプリを起動する:

```bash
./scripts/serve.sh && ./scripts/h3app.py
```

`http://127.0.0.1:8765/` が開く。左のフォームで生成し、右に履歴が並ぶ。

### モード

サイドバーで「何をモデルに渡すか」を選ぶ。

| モード | 渡すもの | 挙動 |
|---|---|---|
| **テキストから**（t2va） | プロンプト | プロンプトだけで作る |
| **画像から**（fl2va） | プロンプト＋画像1枚 | **渡した画像が1コマ目**になり、そこから動き出す |
| **2枚の間を補間**（fl2va） | プロンプト＋画像2枚 | 始点と終点を固定し、間を作る |
| **参照つき**（ref2va） | プロンプト＋見本を最大12件 | 見本は**動画には現れない**。人物・画風・場所の一貫性を保つために使う |

**FL2VA と REF2VA は別のチェックポイント**で、text encoder と両VAEはバイト単位で
同一（DiTだけが違う）だが、できることは重ならない。

| | FL2VA | REF2VA |
|---|---|---|
| テキストから | ✅ | ✅ |
| キーフレーム（画像から・補間） | ✅ | ❌ |
| 窓の連結 | ✅ | ❌ |
| 参照 | ❌ | ✅ |

連結はFL2VAのキーフレーム条件付けに乗る仕組みなので、REF2VAでは動かない。
両パックは1つの `mlx-serve`（`--model-dir`）から提供し、リクエストごとに
必要なパックを名指しするので、モードを切り替えても再起動は要らない。

DiTしか違わないので、FL2VA導入済みなら**追加は35GBで済む**（69GBではない）。
共有ファイルをハードリンクし、`transformer.safetensors` だけ取得する。

参照の上限は**画像9・動画3・音声3、かつ全種類あわせて12**。種類ごとの上限は
合計15になるので、それだけでは本当の上限を表せない。動画は5コマ以上必要。
参照動画は17コマに間引き、音声はPCM16のWAVに変換してから送る（APIが受け取るのは
メディアファイルではなくコマの配列とWAVのため）。

### 長さ・品質・スタイル

- **窓の連結（1〜6）** — フレーム数は**1窓あたり**の指定で、各窓は前の窓の最終コマを
  引き継ぐ。124フレーム×6窓で**1回の生成が約31秒**になる（1窓の上限15秒を超えられる）
- **品質優先**（`fast: false`）— アテンション再利用をやめる。**時間は約4倍だが
  メモリは減る**。他のどの品質トグルとも逆向きなので注意
- **スタイルLoRA** — turboと重ねられる（8枠中7枠。turboが1枠を使う）。強度も個別指定

- 生成中は経過・ステップ・**1ステップの実測から出した残り時間**が出る
- 履歴カードの「この設定で作り直す」でフォームに設定が流し込まれる（`forked_from` に系譜が残る）
- ★ボタンで評価。カードだけが差し替わる
- 生成はGPUを占有するので**同時に1件のみ**。ブラウザを閉じても生成は続き、
  開き直せば進行中のジョブに再接続する

技術構成はPython標準ライブラリのHTTPサーバ＋HTMX。**ビルド工程も外部依存もない**
（HTMXは `web/` に同梱）。画面はサーバがHTML断片を返してHTMXが差し替える方式で、
カードの描画コードはPython側の1箇所（`h3view.card_html`）にしかない。
進捗だけは値の更新なのでブラウザ標準の `EventSource` で受けている。

## 使い方（CLI）

mlx-serve を起動する:

```bash
./scripts/serve.sh
```

動画を生成する（`outputs/` に保存され、`history/history.jsonl` に自動で記録される）:

```bash
./scripts/h3gen.py --prompt "A girl on a grassy hilltop in a strong wind. overall_soundscape: gusting wind." --frames 124 --width 1024 --height 768 --steps 6 --turbo
```

過去の設定を引き継いで派生させる（変えたい項目だけ指定する）:

```bash
./scripts/h3gen.py --from 20260811-1523 --seed 42
./scripts/h3gen.py --prompt "..." --first-frame start.png      # 画像から
./scripts/h3gen.py --prompt "..." --frames 124 --chain-windows 6   # 約31秒
./scripts/h3gen.py --prompt "..." --no-fast                    # 品質優先
./scripts/h3hist.py list --mode fl2va
```

停止する:

```bash
pkill -f 'mlx-serve --model'
```

## 履歴

すべての生成が `history/history.jsonl` に1行1件で追記される。失敗も記録するので、
避けるべき設定の根拠が残る。**送った値（`requested`）と実際に使われた値（`effective`）を
両方持つ** — サーバはフレーム数を `17k+5` に丸め、`steps` を turbo の有無で変えるため、
送った値だけでは再現できない。同じ内容が mp4 のコメント欄にも埋め込まれるので、
動画ファイル単体でも設定が分かる。

Turbo使用時は、モデルパック、`turbo_lora.safetensors` の配置場所、リンク解決先、
実ファイル名、サイズ、SHA-256も自動記録する。履歴画面には実ファイル名と短縮ハッシュを
表示し、全ハッシュは `h3hist.py show` または `history.jsonl` で確認できる。旧履歴は
根拠なく補完せず「記録なし」と表示する。

```bash
./scripts/h3hist.py list                          # 一覧
./scripts/h3hist.py list --search watercolor      # プロンプト本文で絞り込み
./scripts/h3hist.py show 20260811-1523            # 1件の全項目
./scripts/h3hist.py rate 20260811-1523 5 --notes "背景の質感が狙いどおり"
./scripts/h3hist.py gallery --open                # 動画つき一覧をブラウザで開く
```

`gallery.html` は `history.jsonl` から毎回作り直す派生物なので、消しても失われない。
評価とメモは追記される別レコードとして持ち、読み出し時に畳み込むため、
付け直しても生成の記録そのものは書き換わらない。

## パラメータの勘所

| 項目 | 内容 |
|---|---|
| `--frames` | **124以上**を推奨（学習・検証済み範囲は124〜362）。`17k+5` の階段に丸められる |
| `--width` / `--height` | **32の倍数**必須。短辺768がネイティブ。`960×544` はそれ未満で品質が落ちる |
| `--steps` | turbo使用時は**6〜8**。4は下限であって推奨値ではない |
| `--turbo` | 4ステップ蒸留LoRA。付けないと既定30ステップ |
| `overall_soundscape:` | プロンプト末尾にこう書くと、以降が音声の指示になる |

**フレーム階段の理由**: 映像VAEが17フレームを1クリップとして学習されており
（`CLIP_LENGTH=17`、時間4倍圧縮で5潜在トークン）、デコードもその単位で重ねて
繋いでいる。24fpsのため**ちょうど5.0秒は作れない** — 124フレーム（5.17秒）で
生成して後から切る。

## 実測（M4 Max 128GB、turbo）

| 解像度 | フレーム | ステップ | 時間 |
|---|---|---|---|
| 960×544 | 39 (1.6秒) | 4 | 約2分 |
| 960×544 | 107 (4.5秒) | 4 | 7分28秒 |
| **1024×768** | **124 (5.2秒)** | **6** | **22分44秒** |

「1.8倍遅い」といった解像度の相対値は**フレーム数を揃えた比較**なので、
解像度と尺を同時に上げると乗算で効く（16分の見積もりが23分になった）。

メモリ実効ピークは **約28GB**。text_encoder(25.6GB) を解放してから DiT(20GB) を
載せる段階ロードなので、ファイル合計65GBが同時に載ることはない。

## テスト

```bash
python3 tests/test_h3app.py
```

約1秒で終わり、モデルは要らない。`mlx-serve` の代わりに同じ形のSSEを返す
スタブを立てるので、重みもGPUもなしに実際の経路（フォーム送信 → mux →
サムネイル → 履歴書き込み）が通る。

Web経路を意図的に対象にしている。参照機能を最初に入れたとき、CLIでしか
実行しておらず、ブラウザからのリクエストが全部 `KeyError` で落ちていた。
自動化されたPOSTを1回通していれば捕まえられた。

## 注意

- API（`POST /v1/video/generations`）が返すのは base64 の rgb8 生フレームと
  pcm_s16le ステレオ音声で、mp4 ではない。`h3gen.py` が ffmpeg で束ねている
- コードのライセンスは MIT。同梱の htmx は Zero-Clause BSD。
  **モデル本体は MiniMax 独自ライセンスで地域制限がある**（冒頭の警告を参照）

## 参考サイト
- https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills
- https://github.com/Carasibana/ComfyUI-H3-FaceRefine
