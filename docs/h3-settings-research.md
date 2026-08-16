# MiniMax-H3 設定調査メモ

調査日：2026-08-15

## 目的と採用基準

目的は一つの推奨プリセットを固定することではなく、コンテごとに適切な設定を選べるようにすること。MLXや8-bitだけに絞ると事例が少なすぎるため、MiniMax-H3本体で共通する条件を公式実装、ComfyUI、Diffusers、MLX、利用者の比較から抽出した。

採用した情報は次のいずれかに限った。

- 公式仕様または公式ワークフローで再確認できるもの
- 使用モデル、steps、解像度などの条件が書かれた比較
- 複数の利用者から繰り返し報告され、設定選択に実用上の意味がある傾向

単発の感想、パラメータのない作品投稿、宣伝文句は判断基準にしない。コミュニティ報告は公式仕様と同じ確度では扱わない。

## モデル共通の確定事項

MiniMax公式は、出力を4〜15秒、24fps、標準短辺768、32kHz stereoとしている。FL2VAは0〜2枚のキーフレーム、Ref2VAは画像9枚以下、動画3本以下、音声3本以下、総ファイル12以下に対応し、二つは別チェックポイントである。

- [MiniMax-H3 公式リポジトリ](https://github.com/MiniMax-AI/MiniMax-H3)
- [ComfyUI公式 MiniMax-H3ガイド](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)

ComfyUI公式ガイドでは、各辺を32の倍数、フレーム数を24fpsの `17k+5` にする。Refは接続順のタグで参照し、各素材がidentity、style、motion、camera、voiceの何を担当するか明記する。`ref_image_size=match` は生成面積へ縮小し、`max` は短辺2048まで保持するため、後者は同一性を強める代わりに速度とメモリを使う。

公式ComfyUIのT2V/R2Vテンプレートは、Baseの出発点として `res_multistep`、`simple`、20 stepsを使用している。

- [公式T2V workflow JSON](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_t2v.json)
- [公式R2V workflow JSON](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json)

## Turboはファイルごとに条件が違う

ModelTC/LightX2Vの公式表は、Turboを一種類の設定として扱えないことを明確に示している。

| LoRA | 対象 | 学習解像度 | video/audio shift | 公式推奨NFE |
|---|---|---:|---:|---:|
| FL2VA Turbo 4-step v0.1 | FL2VA/T2VA | 544p | 12/3 | 4 |
| FL2VA Turbo 8-step v1.0 | FL2VA/T2VA | 544p | 12/3 | 8または4 |
| FL2VA Turbo 4-step v1.0 | FL2VA/T2VA | 1344x768 | 6/3 | 4 |
| Ref2VA Turbo 4-step v0.1 | Ref2VA | 544p | 12/3 | 4 |

LoRA、steps、video shift、audio shiftは一組で合わせる必要がある。公式サンプルはFL2VAに8-step版、Ref2VAに専用4-step版を使う。

- [ModelTC MiniMax-H3-Turbo](https://github.com/ModelTC/Minimax-H3-Turbo)
- [ModelTC ComfyUI設定手順](https://github.com/ModelTC/Minimax-H3-Turbo/blob/main/COMFYUI_SETUP_AND_INFERENCE.md)

一方、このプロジェクトのFL2VAパックが持つのはLarry系の `v1 ckpt850 EMA` Turbo LoRAで、LightX2V表のLoRAとは別物である。SHA-256は `5a6eeba171cf183020a4ad48774bb2968f29f8168afd6ec17a04987f3528b4ea`。配布元は4〜8 stepsを有効範囲、6〜8を4より良好とし、8を超えると `v1` は過度にシャープになる可能性があるとしている。別のComfyUI変換版では8〜10の利用例もあるが、これはLightX2Vの4-step版へ一般化できない。

- [Larry MiniMax-H3 Turbo LoRA](https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora)
- [Turbo LoRA設定の投稿](https://www.reddit.com/r/StableDiffusion/comments/1vgxf4x/minimax_h3_turbo_lora/)
- [H3ミュージックビデオ制作時の設定と所見](https://www.reddit.com/r/StableDiffusion/comments/1vofr86/lessons_learned_after_making_a_music_video_with_h3/)

## Base stepsの実用範囲

確定している実装上の基点は、公式ComfyUIテンプレートの20 stepsと、MLX ServeのBase既定30 stepsである。

- [mlx-serve](https://github.com/ddalcu/mlx-serve)

利用者の同一seed比較では、15/20/25 stepsのうち25で高ノイズ部のpixel fizzle、動き、口・指、音声が改善したという報告がある。他方で20以降の差を小さいとする意見や、30で輪郭が硬く油絵的になるという報告もある。したがって「多いほど常に良い」ではなく、20を通常の基点、25〜32を実写細部・高速運動・口・音声など難しいショットの選択肢とするのが妥当である。

- [15/20/25 stepsの同条件比較](https://www.reddit.com/r/StableDiffusion/comments/1vmjdiw/minimax_h3_25_steps_should_be_the_lowest_setting/)
- [stepsとsamplerに関する利用者設定](https://www.reddit.com/r/StableDiffusion/comments/1vis9h5/how_many_steps_to_get_the_best_video_result_in/)

この範囲は保証値ではない。画の種類と運動量に応じて選ぶための範囲であり、全生成を高stepsへ固定する根拠にはしない。

## 公式設定の実運用確認

設定と公開出力の両方を確認できる例に限ると、BaseとTurboでは結論が異なる。

- Base Ref2VAの20 steps、`res_multistep/simple`、960x544、24fpsで、23テイクから2分の会話シーンを組んだ制作例がある。作者はクロップした寄りで画素感が出ることと、テイク間の声の不一致を明記しているが、通常画角の映像・音声を編集素材として使えている。公式ComfyUIのBase 20 stepsが実用上の出発点であることは確認できるが、20で全ショットが高精細になるという意味ではない。
- 公式Ref2VAワークフローと公式プロンプトガイドを使い、参照動画の背景・カメラ運動を保ったまま人物を別被写体へ置換した公開例がある。これはモデル分離とRefの役割指定が正しいことを裏づける。一方、投稿には全数値設定がないため、stepsの根拠には使わない。
- ModelTC公式Ref2VA Turboワークフローを使った公開例では、専用LoRA、Euler/Simple、960x544、`match`相当の参照構成で会話と人物の継続を生成できている。ただし作者は公式推奨4ではなく8 stepsへ変更している。この例は専用LoRAの動作を確認できるが、4-stepの画質をそのまま証明しない。
- 4-step Ref2VA Turboの利用報告は、正常に動く例と、背景・手・顔・音声のartifactや「mess」になる例が混在する。ModelTC自身もRef2VA/FL2VA Turboの画質と一貫性改善をroadmapに残している。したがって公式のLoRA・shift・NFE・`match`は互換性上の正しい一組だが、Ref2VAの高品質な既定値として確立したとは判断しない。

以上から、このプロジェクトではRef2VAをBase既定（MLX Serve既定の30 steps）に戻す。専用Turboを導入する場合は公式の一組を崩さず明示的に選び、8 steps等の変更は「公式値」ではなく利用者側の調整として履歴へ残す。

- [Base 20 stepsで会話シーンを制作した例](https://www.reddit.com/r/StableDiffusion/comments/1vj3wlk/i_treated_minimax_h3_like_a_dumb_cameraman_shot/)
- [公式Refガイドとdefault workflowを使ったV2V例](https://www.reddit.com/r/StableDiffusion/comments/1vonhld/testing_v2v_on_minimax_h3/)
- [ModelTC公式Ref2VA Turbo workflowを8 stepsで運用した例](https://www.reddit.com/r/StableDiffusion/comments/1vnk0c7/test_minimax_h3_ref2va_with_lightx2vs_turbo_lora/)
- [Baseと高速化手法の同条件画質比較](https://www.reddit.com/r/StableDiffusion/comments/1vng189/minimax_h3_quality_loss_test/)
- [Ref2VA Turbo 4-stepの設定混同と出力報告](https://www.reddit.com/r/StableDiffusion/comments/1vk8xlt/turbo_4_step_ref2va_working_minimax_h3/)

## 解像度、Ref、速度機能

低解像度は構図やプロンプトの粗い確認には使えるが、同じseedでも最終解像度と内容が一致するとは限らない。最終的な肌、衣服、商品、遠景の顔、文字を判定するなら、短辺768の対象アスペクト比で確認する。通常のアップスケールは、Base生成時に欠けた細部を確実には復元しない。公式の2Kは単純アップスケールではなく、元コンテキストと768p結果を使う別の再生成工程で、現在オープンソース提供されていない。

Refでは、同じ人物の正面・側面・背面・寄りなど役割の異なる画像を明示すると、回り込むカメラでも同一性を保ちやすいという制作報告がある。Refを入れるだけではなく、各素材の担当をプロンプトに書く点は公式ガイドとも一致する。

キャッシュ系高速化は近似であり、同じseedでも軌道が変わる。複数の比較で、静かな寄りでは差が小さくても、高速運動や時間的一貫性で崩れやすいと報告されている。したがって速度機能は一律禁止せず、コンテ上の運動量と細部要求から選ぶ。

- [Turbo、Sage、cacheの比較](https://www.reddit.com/r/StableDiffusion/comments/1vng189/minimax_h3_quality_loss_test/)
- [Turboとcache併用時の比較](https://www.reddit.com/r/StableDiffusion/comments/1vgv1dx/testing_minimax_h3_turbo_lora/)
- [FirstBlockCacheの固定seedベンチマーク](https://www.reddit.com/r/StableDiffusion/comments/1vhlfmw/minimax_h3_firstblockcache_for_comfyui_3033_lower/)

## このプロジェクトへの対応付け

### 現在選べるもの

- 解像度：`1024x768`、`768x768`、`1344x768`、`768x1024`、`768x1344`、`1536x672`。`960x544` は確認用。
- フレーム：124〜362の `17k+5`。モデル仕様上は15秒まで。
- Base/Turbo、steps、`fast`、seed、FL2VA keyframe、Ref2VA素材、`ref_image_size`。

### 現在選べないもの

sampler、scheduler、video shift、audio shiftはローカルUI/APIラッパーに露出していない。ComfyUI利用者の `euler/beta`、`ER_SDE/beta`、`res2s` 等は参考になるが、現状のこのアプリで直接設定できる項目ではない。

### REF Turboについて確認できた問題

現在のmainのRef2VAパックには `turbo_lora.safetensors` がないため、Turbo指定はサーバの400エラーになる。以前のREF生成環境では、FL2VAパックの `ckpt850 EMA` をRef2VAパックへシンボリックリンクして使用していた。これはRef2VA専用LoRAではない。

粗く粒状・モザイク状・油絵状になった夏CMは、Ref2VA 8-bit、1024x768、243 frames、Turbo、6 stepsで生成されていた。サーバログではTurbo時のspeed recipeが `step-cache 0.000, attn-broadcast k=0` であり、ローカルcacheは使われていない。よって、少なくとも「Turboとcacheを同時に使ったこと」が原因ではない。

この一例だけから8-bit量子化、6 steps、タスク違いLoRAの各寄与率は分離できない。ただし、再発防止として次は確定できる。

1. FL2VAパック内のTurboを、配布元の互換性確認なしにRef2VAへ流用しない。
2. 対応LoRAを特定できないRef2VAはBaseで生成する。
3. 同梱FL2VA Turboで最終画質や運動を重視する場合、6を無条件の既定にせず8から選ぶ。
4. Baseで実写の肌・衣服・背景の細部を重視する場合は20だけに固定せず、25〜32を候補にする。
5. 最終出力には短辺768を使い、低解像度previewを画質評価に使わない。

## 設定決定の短い手順

1. コンテから、精細さ、運動量、尺を読む。Refの必要性はコンテと素材から判断する。
2. FL2VA/Ref2VAを決める。Turboを使うなら、そのLoRAが対象タスクと一致するか先に確認する。
3. 作品のアスペクト比で短辺768を選び、尺を `17k+5` にする。
4. Baseは20または25〜32、TurboはLoRA固有の推奨値から、画と運動に合わせて選ぶ。
5. Baseのcacheは、速度ではなくそのカットで細部・運動の変化を許容できるかで決める。
6. 実行時の全設定と使用素材を履歴へ残す。
