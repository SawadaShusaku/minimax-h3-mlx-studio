# minimax-h3-mlx-studio

Run **MiniMax-H3 (Hailuo 3.0)** locally on Apple Silicon — video *and* its stereo
soundtrack generated in a single pass — with a small web UI and a generation log
that records what you ran, what actually ran, and how long it took.

*[日本語版はこちら](README.ja.md)*

> ### ⚠️ Territorial restriction on the model
>
> The MiniMax H3 Community License defines the Applicable Territory as
> **worldwide EXCLUDING the European Union, the United Kingdom, the Republic of
> Korea, and the United States of America**, and prohibits use, reproduction,
> modification, distribution and display outside it.
>
> **No weights are distributed in this repository** — the code downloads them
> from Hugging Face. Check whether your jurisdiction permits you to use these
> files *before* downloading. This affects a large share of readers, so please
> do not skip it.

---

## Why this exists

Getting usable output from H3 on a Mac is less about the code than about four
settings that are easy to get wrong. This repo carries the code, and — more
usefully — **the measurements and the failures behind those settings**.

The generation log includes a deliberately bad run with its analysis, because
knowing *what breaks* turned out to be harder to find than knowing what works.

## Requirements

| | |
|---|---|
| Hardware | Apple Silicon. Measurements here are from an **M4 Max / 128 GB** Mac Studio |
| OS | macOS 26.2+ (required by mlx-serve) |
| Runtime | [mlx-serve](https://github.com/ddalcu/mlx-serve) — a Zig server, **no Python needed** |
| Model | [ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit](https://huggingface.co/ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit) — 65 GB download |
| Also | `ffmpeg` (muxing), Python 3.9+ (stdlib only — no pip install) |

**Memory:** the 65 GB of weights are *not* all resident at once. The text encoder
(25.6 GB) is freed before the DiT (20 GB) loads, so the measured peak is
**~28 GB**. A 32 GB Mac can run the 4-bit pack instead.

## Setup

```bash
brew tap ddalcu/mlx-serve https://github.com/ddalcu/mlx-serve
brew trust ddalcu/mlx-serve && brew install mlx-serve
mlx-serve pull ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit   # 65 GB
```

`mlx-serve pull` always writes to `~/.mlx-serve/models/`. Move the pack into
`models/` here (or point `scripts/serve.sh` at wherever you keep it).

```bash
./scripts/serve.sh      # start mlx-serve
./scripts/h3app.py      # open the web UI at http://127.0.0.1:8765/
```

## The web UI

Form on the left, progress and history on the right. Python **standard library
only** plus a vendored copy of htmx — no build step, no `node_modules`, no venv.

* Live progress with a **remaining-time estimate derived from the measured
  seconds-per-step**, which matters when a run takes 20+ minutes
* **"Rebuild with these settings"** on any past card refills the form and records
  the lineage in `forked_from`
* Star ratings and notes, stored as appended annotations
* One generation at a time (it saturates the GPU). Closing the browser does not
  stop it; reopening reattaches to the running job

Prefer the terminal:

```bash
./scripts/h3gen.py --prompt "..." --frames 124 --width 1024 --height 768 --steps 6 --turbo
./scripts/h3gen.py --from 20260811-1523 --seed 42     # inherit and vary
./scripts/h3hist.py list
./scripts/h3hist.py gallery --open
```

## Settings that actually decide quality

Every one of these was learned by getting it wrong first. Miss them and output
degrades in ways that look like model limitations but are not.

| Setting | Use | Why |
|---|---|---|
| **Frames ≥ 124** | 124–362 | The trained range. Below 124 is off-distribution, not merely "short" |
| **Short side 768** | `1024×768`, `768×768`, `1344×768` | `960×544` is below the model's native short edge |
| **Steps 6–8** with turbo | 6 | The LoRA author's range. 4 is the floor, not a recommendation |
| **LoRA strength 1.0** | default | Only adjust for a specific defect (0.8–0.95 over-sharp, 1.05–1.2 blurry) |

**Frame counts sit on a `17k + 5` ladder** — 124, 141, 158 … 362. This is not
arbitrary: H3's video VAE was trained on 17-frame clips (`CLIP_LENGTH = 17`,
4× temporal compression → 5 latent tokens per chunk), and decoding walks those
chunks with overlap and cross-fades. Off-ladder counts are silently snapped up,
which is why the log records both requested and effective values. At 24 fps you
cannot hit exactly 5.0 s — generate 124 frames (5.17 s) and trim afterwards.

### A worked failure

`960×544 / 107 frames / 4 steps` broke all four thresholds at once. The result:
mottled noise frozen into flat background areas, mushy faces, malformed hands.
The prompt made it worse by asking for `dynamic smear frames` and `fast camera
push` — the LoRA documents motion smearing at 4 steps with heavy motion.

Fixing the settings **and** describing the background concretely (an undescribed
background invites residual noise to become the background) produced clean
watercolor paper texture instead. Both runs are in `history/history.jsonl`.

## Prompt shape

Six blocks, in order. Style declaration first — it decides the domain, and a
weak opening lets everything after it drift toward photorealism.

```
[style declaration]. [subject] in [place], [passive motion].
Behind them, [2-3 concrete background layers].
[linework], [color], [texture], [lighting].
Camera [slow movement].
overall_soundscape: [2-3 audio layers].
```

* Text after **`overall_soundscape:`** becomes the audio instruction
* Prefer wind-driven motion over body motion — it buys movement at far lower risk
* **Omit** `8k`, `hyperdetailed`, `octane render`, `masterpiece`. They pull toward
  heavy rendering, which is usually the opposite of what you asked for
* `soft even lighting` is worth stating: without it the model reaches for
  cinematic lighting, a main source of the "AI look"

## Measured timings (M4 Max, 128 GB, turbo)

| Resolution | Frames | Steps | Time |
|---|---|---|---|
| 960×544 | 39 (1.6 s) | 4 | ~2 min |
| 960×544 | 107 (4.5 s) | 4 | 7 min 28 s |
| **1024×768** | **124 (5.2 s)** | **6** | **22 min 44 s** |

Relative-cost labels like "1.8× slower" compare resolutions *at equal frame
count*. Raising resolution and frame count together multiplies — an estimate of
16 min came out at 23.

## How the log works

`history/history.jsonl`, one JSON object per line, append-only.

* **`requested` and `effective` are both stored.** The server snaps frame counts
  and changes the default step count depending on turbo, so the request alone
  cannot reproduce a run
* **Failures are recorded too** — that is where the thresholds above came from
* Ratings and notes are *appended* as annotation records and folded in on read,
  so re-rating never rewrites the original entry
* The same metadata is embedded in each mp4's comment field, so a video that
  travels alone still carries its settings (`ffprobe -show_entries format_tags=comment`)

Not tracked by git: the weights (65 GB) and `outputs/` (videos grow without
bound and git keeps every version forever). The 4-frame contact strips **are**
tracked — roughly 20 KB each, and the only visual record that survives a clone.

## Layout

```
scripts/h3app.py    web UI (stdlib HTTP server + htmx)
scripts/h3core.py   generation, muxing, strips   — shared by CLI and UI
scripts/h3lib.py    history read/write
scripts/h3view.py   CSS and card HTML            — rendering lives in one place
scripts/h3gen.py    CLI: generate
scripts/h3hist.py   CLI: list / show / rate / gallery
web/htmx.min.js     vendored, no CDN
history/            history.jsonl, thumbs/, gallery.html
```

Server-rendered fragments swapped by htmx, so card markup exists only in
`h3view.card_html`. Progress is a value update rather than a fragment swap, so
it uses the browser's own `EventSource`.

## License

Code and documentation: [MIT](LICENSE). Bundled htmx is Zero-Clause BSD.
The model weights are **not** distributed here and carry their own license —
see the territorial restriction above.
