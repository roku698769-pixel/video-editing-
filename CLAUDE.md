# CLAUDE.md — 動画編集プロジェクト（汎用）

このファイルはClaude Codeがセッション開始時に読み込む指示書です。
「The Next Destination」シリーズとは別の、ユーザーが撮影した素材の編集を行うためのリポジトリです。
The Next Destination の確定ルール（テロップの文型・尺・投稿パックなど）はここでは適用しません。必要なものだけ、ユーザーの指示があったときに取り入れます。

## 基本

- 対象：ユーザーが自分で撮った素材の編集。縦型ショート（1080×1920）と横型の通常動画（1920×1080）の両方。内容は案件ごとに臨機応変に決める
- ユーザーとのやり取りは日本語。動画内の文字や投稿文の言語は案件ごとに確認する
- 作業はclaude.aiのクラウドセッション（一時コンテナ）で行う。セッション終了でコンテナは消えるため、残すものは必ず commit して main に push する

## 品質の方針（ユーザーの指示）

- このリポジトリでは**超高品質な動画**を目指す
- 処理時間・ファイル容量・手間の少なさを理由に、品質の低い方法を選ばない。迷ったら品質の高い方を選ぶ
- 妥協した点、確認できていない点は隠さずユーザーに伝える
- 「動けばよい」で終わらせず、仕上がりをフレーム画像で目視して、気になる点があれば自分から直す

## コネクター（ユーザーの許可）

- 品質を上げるために必要なら、コネクター（外部サービス）を使ってよい（ユーザーが許可済み）
- 使うときは、どのサービスに何を送り、何に使うかを報告に書く
- 現時点で接続されていて、編集に使えそうなもの（未使用・未検証）：Adobe for creativity（音声の明瞭化 `media_enhance_speech`、画像の補正・背景除去、動画のリサイズなど）、Higgsfield AI（動画・画像の高解像度化 `upscale_video`／`upscale_image` など。クレジット消費あり）、Canva
- Descript（接続済み・接続確認済み）：文字起こし（`transcribe_audio_video`、SRT字幕の書き出し）、AI編集エージェント（Agent Underlord。フィラー除去、Studio Soundによる音声の明瞭化など）、ショートの切り出し（`create_social_clips`）、書き出し（`publish_project`、最大4K）、他の編集ソフト向けタイムラインの書き出し。素材はURL（Driveリンク可）か直接アップロードで取り込む。効果は ffmpeg の処理と比べてから使う
- 有料のクレジットを使う処理は、使う前に見込みの消費量をユーザーに伝える
- 編集中に、接続済みのコネクターやスクリプトでは足りない機能が出てきたら、追加すべきコネクターを探してユーザーに提案する（ユーザーの指示）。追加は不要と判断した候補（2026-11時点）：OpusClip（Descriptで足りなければ）、Shutterstock（検索のみで素材は使えない）、ElevenLabs（音声エージェント管理用でナレーション生成には使えない）

## 編集の道具（2026-11-20 ユーザーの指示で導入）

FFmpeg・yt-dlp・Whisper・Remotion・HyperFrames を編集の道具として使える状態にしてある。コンテナはセッションごとに消えるため、**セッション開始時に `bash scripts/setup_tools.sh` を実行する**（約10秒。`--model` を付けると Whisper のモデル約2.9GBも先に取得する。付けなくても初回の文字起こしで自動取得、約20秒）。

| 道具 | 版（2026-11-20） | 置き場所 | 主な用途 |
|---|---|---|---|
| FFmpeg | 6.1.1（環境に既存） | — | 書き出し全般（edit.py など） |
| yt-dlp | 2026.10.26 | pip | URL の動画・音声の取得、メタデータの確認 |
| Whisper | faster-whisper 1.2.1＋large-v3 | pip、モデルは `models/whisper/`（Git対象外） | 文字起こし・字幕（`scripts/transcribe.py`） |
| Remotion | 4.0.545 | `motion/`（`src/`） | 透明背景の重ね素材（テロップ等） |
| HyperFrames | 0.9.71 | `motion/`（ひな形 `hyperframes-template/`） | HTML＋GSAP で作る透明背景の重ね素材 |

### Whisper（文字起こし）

- `python3 scripts/transcribe.py <音声または動画> --lang ja` → `<同名>.srt`（字幕）と `<同名>.json`（区間・単語ごとの開始／終了秒と確からしさ）。`--out` で出力先、`--max-chars` で字幕1枚の文字数（既定 日本語16・英語42）、`--prompt` で固有名詞の手がかりを渡せる
- 本家の openai-whisper ではなく、同じ Whisper の重みを CTranslate2 で動かす faster-whisper を使う（本家は PyTorch が必要で、CPU だけのこの環境では重く遅いため）。既定は最上位モデル large-v3、beam 5、無音区間の除去あり、単語ごとの時刻あり
- 実測（テスト音声、Microsoft の読み上げ音声）：日本語9.0秒・英語6.4秒とも一字一句正しく書き起こした。処理時間は音声の約1.5〜2倍（CPU 4コア、int8）
- 日本語は句読点を省きがちなため、`--lang ja` で `--prompt` を省いたときは句読点の付いた文を手がかりに渡す（付けた結果、字幕の切れ目が「。」の位置になった）。字幕は「句読点の直後」「発話の間」「真ん中に近い所」で切る（語の途中で切らない）
- 確からしさが 0.5 未満の語は実行時に一覧で出る。聞き違いの可能性がある所としてユーザーに確認する
- 実際の撮影素材（環境音・風切り音・複数の話者）での精度は未検証。Descript の文字起こしとどちらを使うかは、本物の素材で比べて決める
- faster-whisper に音声ファイルの名前をそのまま渡すと、同梱の PyAV の版の食い違いで落ちる（実測）。transcribe.py は ffmpeg で 16kHz に変換した配列を渡している

### yt-dlp（URL からの取得）

- yt-dlp は YouTube 専用ではなく、多数の動画サイトに対応している（YouTube 以外のサイトでも使う。2026-11-20 ユーザー指摘）。対応サイトは `yt-dlp --list-extractors`
- 例：`yt-dlp -f "bv*+ba/b" --merge-output-format mp4 -o "footage/<案件>/%(title)s.%(ext)s" <URL>`。中身の確認だけなら `--simulate --print "%(title)s %(duration)s %(resolution)s"`、選べる画質の一覧は `-F`
- サイトごとの実測（2026-11-20）：
  - Wikimedia Commons：取得できた（画質の一覧の取得と、854×480 の一部区間のダウンロードまで確認）
  - YouTube：取得できない（HTTP 429 Too Many Requests。クラウドの接続元が YouTube に制限されている）。YouTube の動画はファイルか Drive のリンクで受け取る。試すときは `--js-runtimes node` を付ける（付けないと「JavaScript の実行環境がない」と警告が出る）
  - Vimeo：ログインが必要と返された
  - 上記以外のサイトは未確認。初めてのサイトは `--simulate` で取得できるかを先に確かめる
- `--download-sections "*開始-終了"` の区間ダウンロードは、キーフレーム単位で切られるため指定より長くなる（5秒を指定して6.8秒になった）。編集で使う区間は、全体を取得してから edit.py の `in`／`out` で決める
- 使ってよいのは、ユーザー自身の動画や、利用が許されている素材に限る

### Remotion（ユーザーの許可）

- テロップやモーショングラフィックスを作るために Remotion を使ってよい（2026-11 ユーザーが許可）
- 役割：**重ねる素材（透明背景）を作る道具**として使う。撮影素材の読み込み・色の処理・最終の書き出しは従来どおり edit.py（ffmpeg）で行い、Remotion の出力をその上に重ねる
  - 理由：撮影素材を Remotion に通すとブラウザで映像を読み込むため、10bit や HDR の情報が失われるおそれがある（未検証）。また Remotion の通常の MP4 は色の範囲がフル（yuvj420p）で書き出され、このリポジトリの設定とそろわない（実測）
- プロジェクト：`motion/`（入口 `src/index.ts`、コンポジションの登録 `src/Root.tsx`、動作確認用 `src/Sample.tsx`）。案件ごとのコンポジションを `src/` に足す
- 書き出し（`motion/` で実行。実測で動作確認済み）：
  - `npx remotion render src/index.ts <コンポジションID> <出力.mov> --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png --browser-executable=$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell)`（出力は yuva444p12le の ProRes）
  - テスト：1080×1920・60フレームの書き出しに約18秒。ffmpeg の overlay で重ねたフレームを目視し、正しく重なることを確認した（2026-11-20 にも再確認）
- フォント：何も指定しないと IPAゴシックなどの代替フォントで描画される（実測）。`fonts/` のフォントを `@font-face`（`staticFile` など）で読み込み、書き出したフレームで書体を確認する
- 文字の動きはイージング付き（`interpolate` の `easing`、`spring`）にする。テロップの品質の方針（下記「テロップ・仕上がりの質」）は Remotion でも同じ
- パッケージ：`motion/package.json`（`remotion`・`@remotion/cli` 4.0.545、`react@18.3.1`・`react-dom@18.3.1`）。版は package-lock.json で固定。`node_modules` は Git に入れない
- ライセンス：個人と従業員3人以下の会社は無料。それより大きな組織で使う場合は有料の会社向けライセンスが必要

### HyperFrames

- HeyGen のオープンソースの道具。HTML・CSS・GSAP のアニメーションをブラウザで1フレームずつ撮り、動画にする。Remotion と同じく**透明背景の重ね素材を作る道具**として使い、撮影素材の処理と最終の書き出しは edit.py で行う
- Remotion との使い分け：HTML と GSAP で書けるので、文字組みの細かい調整や、GSAP の豊富なイージング・テキスト分割の演出が手早く書ける。どちらで作るかは案件ごとに決める（決めかねるときは両方で試してフレームを比べる）
- ひな形：`motion/hyperframes-template/`（1080×1920、透明背景、2秒）。案件ごとにフォルダごと複製して `index.html` を書き換える。ルート要素の `data-duration`、各要素の `data-start`／`data-duration`、`window.__timelines["main"]` への GSAP タイムラインの登録が決まりごと（`npx hyperframes docs` で詳細）
- 書き出し（ひな形のフォルダで実行。実測で動作確認済み）：
  - `HYPERFRAMES_BROWSER_PATH=$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell) npx hyperframes render --format mov --fps 30 -o <出力.mov>`（出力は yuva444p12le の ProRes。PNG 連番は `--format png-sequence`）
  - `HYPERFRAMES_BROWSER_PATH` を付けないと、別の Chrome を使って準備だけで約32秒かかった（付けると全体で約9秒、1080×1920・60フレーム）
  - ffmpeg の overlay で重ねたフレームを目視し、透明部分が正しく抜けることを確認した
  - 書き出し前の確認：`npx hyperframes check`（構文・実行時エラー・レイアウト）
- GSAP は既定で CDN（jsdelivr）から読み込む。この環境から読み込めた
- **利用状況の送信（テレメトリ）は既定で有効**のため、setup_tools.sh で無効にしている（`npx hyperframes telemetry disable`）。この設定は `~/.hyperframes/config.json` に書かれ、下記のスキルの media-use の送信もこれで止まる（スクリプトで確認）。コンテナが新しくなると消えるため、HyperFrames を使う前に必ず setup_tools.sh を実行する
- 使わない機能：`publish`・`share`・`cloud`（素材を外部へアップロードする）、`lambda`・`cloudrun`。使う必要が出たら、何を送るかをユーザーに伝えて許可を得る
- HyperFrames 自身の文字起こし（whisper-cpp）・読み上げ（Kokoro）・BGM 生成は入れていない。文字起こしは transcribe.py を使う

### HyperFrames のスキル（2026-11-20 ユーザーの指示で導入）

- `npx hyperframes skills` で公開されている全9本を入れ、リポジトリの `.claude/skills/` に置いた（v0.9.71、本体と同じ版。入手元と版の記録は `.claude/skills/hyperframes-skill-lock.json`）。このリポジトリでセッションを始めると読み込まれる
  - `hyperframes`（最初に読む。書き出しの決まりと作業の流れ）、`hyperframes-cli`（CLI の使い方）、`hyperframes-registry`（約450の既製の部品：トランジション、タイトル、字幕、図表など）、`media-use`（音楽・効果音・画像・読み上げ・切り抜き・色の扱い）、`remotion-to-hyperframes`（Remotion などで作ったものの移植）、`reverse-any-video`（既存の動画を HyperFrames で作り直す）、`style-sampling`（決まった型での作成）、`slideshow`（発表用スライド）、`figma`（Figma の取り込み）
- スキルは CLI を `node .claude/skills/hyperframes/scripts/plugin-cli.mjs <コマンド>` で呼ぶ。この呼び方は**既定で npm の最新版**を使うため、版をそろえるために次の2つを付けて実行する（ひな形での `check` で動作確認済み）：
  - `HYPERFRAMES_STANDALONE_CLI_VERSION=0.9.71`（motion/package.json と同じ版に固定）
  - `HYPERFRAMES_BROWSER_PATH=$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell)`（上記の速い書き出し）
- **このファイルの方針がスキルより優先する**：HyperFrames は透明背景の重ね素材を作る道具として使い、撮影素材の色の処理と最終の書き出しは edit.py で行う。スキルの中の、外部へアップロードする機能（`publish`・`share`・`cloud` など）と、HeyGen・Gemini などの有料・外部のサービス（API キーが必要。いずれも未設定）は、ユーザーの許可なく使わない
- スキルの中のスクリプト（106本）は、送信先を確認した：利用状況の送信（上記の設定で停止）、HeyGen・Gemini・OpenAI・Groq の API（キーがないと動かない）、フォントや部品の取得（Google Fonts、jsdelivr、GitHub）、素材検索（Openverse、Freesound、Pixabay）
- スキルを更新するときは `npx hyperframes skills update` を motion/ の版の更新と同時に行い、`.claude/skills/` に写し直す（ホームに入るため、そのままではセッション終了で消える）

## セッション開始時の準備

1. `ffmpeg`、`ffprobe`、`python3` が使えるか確認する
2. `bash scripts/setup_tools.sh` で編集の道具（yt-dlp・Whisper・Remotion・HyperFrames）を入れる（上記「編集の道具」）
3. テロップを使う案件では `bash scripts/get_fonts.sh` でフォントを取得する（フォント本体はGitに入れていない）
4. 新しい案件は `projects/<案件名>/` に作る

## フォルダ構成

```
.
├── CLAUDE.md
├── footage/            ← 受け取った素材を置く（Git対象外）
├── projects/<案件名>/
│   ├── edit.json       ← 編集指示（どの素材のどこを、どの順で、どうつなぐか）
│   ├── telop.json      ← テロップの内容（telop.py で telop.ass にする）
│   ├── telop.ass
│   ├── notes.md        ← 案件のメモ（ユーザーの指示、決まったこと、修正履歴）
│   └── work/           ← 中間ファイル・チャンク・確認用フレーム（Git対象外）
├── fonts/              ← テロップ用フォント（get_fonts.sh で取得。直下に置く。Git対象外）
├── motion/             ← Remotion（src/）と HyperFrames（hyperframes-template/）。重ね素材を作る
├── models/whisper/     ← Whisper のモデル（Git対象外）
├── scripts/
└── output/             ← 完成動画（MP4はGit対象外）
```

## 進め方

1. **素材の受け取り**：ファイルの添付か、Google Driveのリンクで受け取る（下記）
2. **下調べ**：`python3 scripts/probe.py footage/<案件>/` で尺・解像度・fps・回転・HDR・音声を一覧にする。気になる点（可変fps、HDR、縦横の混在など）はユーザーに伝える
3. **構成の相談**：素材の中身をフレーム画像で確認し、使う区間・順番・つなぎ・テロップ・尺をユーザーと決める。確認していない素材の中身を推測で語らない
4. **色の決定**：`scripts/compare.py` で、HDRの変換方式やカラーグレーディングの候補を同じフレームで並べ、ユーザーに選んでもらう
5. **確認用の書き出し**：`python3 scripts/edit.py projects/<案件>/edit.json --draft` で半分の解像度の軽い版を作り、構成とタイミングを見てもらう
6. **本番の書き出し**：`python3 scripts/edit.py projects/<案件>/edit.json` でマスターと投稿用（軽量版）を作る
7. **検証**：`python3 scripts/verify.py projects/<案件>/work/manifest.json` でフレーム数・色空間・音量を確認し、書き出された確認用フレームを目視する。数値を添えて報告する
8. **区切りごとに commit & push**（構成確定・完成）。素材と動画はGitに入れない

## スクリプト

- `probe.py <ファイル/フォルダ>`：素材の一覧（回転を反映した表示サイズ、fps、可変fpsの疑い、HDR、10bit、音声）
- `edit.py <edit.json> [--draft] [--no-light] [--clean]`：書き出し。出力は `output/<name>_master.mp4`・`_post.mp4`（`--draft` は `_draft.mp4`）。テロップのフォントが代替フォントに置き換わる場合は止まる
- `compare.py <edit.json> <クリップ番号> <秒> --variants <variants.json>`：設定違い（HDR変換方式、グレーディング）を同じ1フレームで横に並べたPNGを作る。書式はスクリプト冒頭のコメント
- `telop.py <telop.json> <telop.ass>`：テロップのASSを作る。出方・消え方はイージング付きで1フレームずつ計算する（書式はスクリプト冒頭のコメント）
- `get_fonts.sh [名前...]`：フォントを npm（@expo-google-fonts）から `fonts/` に取得し、ASSで指定する名前を一覧表示する
- `transcribe.py <音声/動画> [--lang ja] [--out ...] [--max-chars N] [--prompt ...]`：Whisper（faster-whisper large-v3）で文字起こしし、SRT と単語ごとの時刻付き JSON を書き出す
- `setup_tools.sh [--model]`：編集の道具を入れる（セッション開始時に実行）
- `verify.py <manifest.json> [--at 秒,...] [--silence]`：フレーム数の照合、色空間（BT.709）、確認用フレームの書き出し（冒頭・各つなぎの前後・末尾・指定時刻）、ラウドネスとトゥルーピーク、無音区間

## edit.json の書式

```json
{
  "name": "lisbon_day1",
  "format": "vertical",
  "fps": "auto",
  "grade": {"contrast": 0.2, "saturation": 1.08, "vibrance": 0.15, "warmth": 0.1,
            "sharpen": 0.25, "vignette": 0.2},
  "tonemap": {"method": "hable", "npl": 100},
  "loudness": {"i": -14, "tp": -1.0},
  "subtitles": "projects/lisbon_day1/telop.ass",
  "clips": [
    {"src": "footage/lisbon/IMG_0012.MOV", "in": 3.2, "out": 7.8, "stabilize": true,
     "transition": {"type": "fade", "dur": 0.5}},
    {"src": "footage/lisbon/IMG_0015.MOV", "in": 0, "out": 4, "speed": 0.5, "interp": "motion",
     "focus_x": 0.35},
    {"src": "footage/lisbon/IMG_0020.MOV", "in": 10, "out": 13, "fit": "blur",
     "denoise": true, "highpass": 80, "grade": {"exposure": 0.3}},
    {"src": "footage/lisbon/photo.jpg", "dur": 2.5, "fit": "fit"}
  ],
  "audio": [
    {"src": "footage/lisbon/narration.wav", "start": 0.4, "volume_db": 2, "denoise": true}
  ]
}
```

- `format`：`vertical`（1080×1920）／`horizontal`（1920×1080）／`square`（1080×1080）／`{"w":..,"h":..}`
- `fps`：`"auto"`（既定。素材の尺で最も多いフレームレートを標準値に合わせて使う。29.97なら `30000/1001`）／数値／`"30000/1001"` のような分数
- `grade`：全クリップにかけるカラーグレーディング。クリップの `grade` で項目ごとに上書きでき、`"grade": false` でそのクリップだけ外せる。RGB 16bitで処理する
  - `exposure`（段。+1で倍の明るさ）、`warmth`（-1〜1。+で暖色）、`contrast`・`shadows`・`highlights`（-1〜1。1本のトーンカーブにまとめる）、`saturation`（1が元のまま）、`vibrance`（色の薄いところを中心に彩度を上げる。0.1〜0.3程度）、`sharpen`（0〜1。CAS）、`vignette`（0〜1。周辺減光の強さ）、`deband`（true でグラデーションの段差を減らす）、`lut`（.cube ファイル）、`curves`（ffmpeg curves の指定をそのまま）
- `tonemap`：HDR素材（HLG/PQ）をSDRにする方式。`method` は `hable`／`mobius`／`reinhard`／`clip`、`npl` は基準の明るさ。**素材ごとに見え方が違うので compare.py で比べて決める**。クリップごとに `"tonemap": {...}` で上書き、`"tonemap": false` で変換しない
- `loudness`：完成版の音量。既定は統合ラウドネス -14 LUFS・トゥルーピーク -1 dBTP（YouTube向け）。2パスの loudnorm（linear）で合わせる。`null` で合わせない
- クリップの項目
  - `in`／`out`：素材の使う区間（秒）。`out` を省くと素材の最後まで。静止画は `dur`（秒）
  - `fit`：`fill`（はみ出しを切る。既定）／`fit`（全体を収めて黒帯）／`blur`（全体を収めて、背景をぼかした同じ映像で埋める）
  - `focus_x`／`focus_y`：`fill` で切り出す位置（0＝左・上、0.5＝中央、1＝右・下）。横の素材を縦型にするときの寄せ先
  - `speed`：再生速度（2＝倍速、0.5＝スロー）。音声も同じ速度で、音程は保つ
  - `interp`：フレームが足りないときの補い方。`none`（既定。コマを繰り返す）／`blend`（前後のコマを混ぜる）／`motion`（動きを推定して中間のコマを作る。なめらかだが、細かい模様や重なる動きで崩れることがあるので必ずフレームで確認する）。スロー再生や 24/25fps 素材を 30fps にするとき、足りなければ edit.py が注意を出す
  - `stabilize`：手ブレ補正（vidstab の2パス）。`true` か `{"shakiness": 6, "smoothing": 20, "zoom": 0}`。`zoom` 0 は黒い縁が出ない最小の拡大を自動で選ぶ。テストでは、フレーム間の揺れの平均が 18.6px → 2.6px になった
  - `volume`：素材の音の大きさ（0で無音）
  - `denoise`：素材の音のノイズ除去（`true` か強さの数値。既定12dB）。`highpass`：低音カットの周波数（風切り音対策に 80〜120）
  - `vf`：このクリップだけにかける追加フィルタ（画面サイズに合わせた後、グレーディングの前）
  - `transition`：**次のクリップへの**つなぎ。`type` は `cut`（既定）か ffmpeg xfade の種類（`fade`、`fadeblack`、`fadewhite`、`dissolve`、`slideleft`、`wipeleft`、`zoomin`、`smoothleft` など）、`dur` は秒
- `audio`：ナレーションやBGMなど外部の音声。`start` は完成版の何秒目から鳴らすか。`in`／`out` で素材の使う区間。`denoise`・`highpass` も使える
- `clip_audio`：false にすると素材の音をすべて使わない
- `chunk_max`：チャンク1本の最長秒数（既定20）

## 画質と音質の設定（品質の方針に基づく既定値）

- 中間ファイル：**10bit 4:4:4 の可逆圧縮**（x264 lossless）。最終のエンコード以外で画質が落ちる工程はない
- 色の処理：縮小は lanczos（精度優先のフラグ付き）、グレーディングは RGB 16bit、色空間は BT.709 に明示的に変換してタグも付ける（素材が BT.601 の場合も変換する）
- 10bit → 8bit は誤差拡散ディザ（空などのグラデーションに段差を出さない）
- マスター：libx264 `veryslow`、crf 12、aq-mode 3（暗部の段差を抑える）、音声 AAC 320k
- 投稿用（軽量版）：crf 17、maxrate 24M、音声 AAC 256k
- 写真：EXIFの向きを反映する。HEICは `pip install pillow-heif` があれば読める
- 音声：リサンプルは soxr（高精度）、内部は 32bit float、最後に -14 LUFS へ合わせる

## できないこと・注意点（実測）

- GPUを使う処理（libplacebo によるHDR変換など）はこの環境では動かない（Vulkanが使えない）
- 手ブレ補正（vidstab）は 8bit でしか動かないため、その工程だけ 8bit になる
- `interp: "motion"` の中間フレームは、元のフレームより少し柔らかくなる（テストで確認）
- HDR変換の方式とグレーディングの値は、テスト素材でしか確認していない。**本物の素材が届いたら compare.py で比べて決める**
- ffmpeg 6.1 では、atempo の後ろに soxr のリサンプルを置くと処理が止まる（実測）。edit.py はリサンプル → atempo の順にしてある
- 書き出しの時間の目安：縦型12秒・5クリップ（手ブレ補正あり）でマスター＋投稿用が約1分30秒。`interp: "motion"` は重い

## 編集の事故防止（The Next Destination の制作で実際に起きた事故から）

- **長い単一パスのエンコードはしない。** 区間ごとのチャンク → concat demuxer で `-c copy` 結合 → 最後に音声を mux。edit.py はこの手順で作ってある（単一エンコードが中断されて moov のない再生不能ファイルになった事故が複数回あった）
- 音声は **48kHz WAV で組み立ててから** mux する。連結したMP3を `-c copy` で入れるとタイムスタンプが壊れ、映像の末尾が欠けた事故がある
- `scale` を `eval=frame` で動かすようなズーム演出は、**その区間だけ別ファイルとして先にレンダリング**する（大きなフィルタグラフの中に入れると ffmpeg が落ちる）。`crop` の w/h はフレームごとに動かないので、cropでズームは作れない
- 完成の確認は、コンテナの再生時間ではなく **`ffprobe -count_frames` のフレーム数** と **フレーム画像の目視** で行う
- 出力は「マスター」と「投稿用（軽量版）」の2種を別名で置く（設定は上の「画質と音質の設定」）

## スマホ素材の注意

- **回転**：縦で撮った動画は「横の映像＋回転メタデータ」で保存されていることが多い。ffmpeg は自動で回転を反映する。probe.py の表示サイズは回転後の値
- **可変フレームレート**：スマホ動画はfpsが一定でないことがある。edit.py は下ごしらえで `fps` フィルタにより固定fpsにする
- **HDR**（iPhoneの既定はHLG）：そのまま扱うと白っぽく色が抜ける。edit.py はHDRを検出すると zscale＋tonemap でSDRに変換する。方式は compare.py で比べて決め、細かい色は `grade` で整える
- **写真の向き**：ffmpeg はJPEGのEXIF回転を反映しないが、edit.py が反映した画像を作ってから使う

## テロップ・仕上がりの質

- **安っぽい仕上がりにしない。** 安易なフォント・色・縁取り・エフェクトで質を落とさない
- テロップの位置は、フォントの数値計算ではなく**実際に書き出したフレーム画像で測って**決める（libassの描画幅は計算より狭くなることがある）
- 文字の動きは telop.py のイージング付きアニメーション（`rise`・`blur`・`track`・`scale`・`chars`・`fade` の組み合わせ）を使う。等速の動き（ASSの `\move`）は安っぽく見えるので使わない
- フォントは `get_fonts.sh` で取得したものと、環境の Inter を使う。既定の一式：日本語ゴシック（Noto Sans JP、Zen Kaku Gothic New）、日本語明朝（Noto Serif JP、Shippori Mincho）、英字（Montserrat、Playfair Display、Cormorant Garamond、DM Serif Display）。案件ごとにユーザーと決める
- libass は `fonts/` の直下しか探さない。サブフォルダに入れると代替フォントで描画される（実測。edit.py はこの置き換えを検出して止まる）

## 素材の受け取り（Google Drive）

- 大きな動画はDriveのリンクで受け取ることがある。コネクターの `download_file_content` は中身を会話に返すため動画には使えない
- 手順：
  1. `get_file_permissions` で共有範囲を確認する。「リンクを知っている全員」でなければ `share_file`（permissionId `anyoneWithLink`、role `reader`）で変更する。**この変更はユーザーから恒久的に許可済み（毎回の確認は不要）**
  2. `curl -L -o <出力> "https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t"` で取得し、`file` で中身が動画か確認する（ログイン画面のHTMLが返ることがある）
  3. コネクターには共有を外す機能がないため、「制限付き」に戻す作業はユーザーに依頼する
- ネットワーク：Driveのホスト（`drive.google.com`、`drive.usercontent.google.com`）が許可されたクラウド環境で作業する

## 作業上の作法

- 確認していないことを「確認した」と言わない。検証は数値とフレーム画像で行う
- 修正の方向が曖昧なときは、大きく変える前にユーザーに確認する。一度に大きく変えず、小さく刻む
- ユーザーは短い日本語で素早く決める。選択肢は具体的な秒数・ファイル・数値で示す
- ユーザーの返答や情報提供を、着手の指示と取り違えない
- BGMを編集で入れるか、投稿時にアプリで付けるかは案件ごとに確認する
