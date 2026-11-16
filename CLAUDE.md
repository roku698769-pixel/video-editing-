# CLAUDE.md — 動画編集プロジェクト（汎用）

このファイルはClaude Codeがセッション開始時に読み込む指示書です。
「The Next Destination」シリーズとは別の、ユーザーが撮影した素材の編集を行うためのリポジトリです。
The Next Destination の確定ルール（テロップの文型・尺・投稿パックなど）はここでは適用しません。必要なものだけ、ユーザーの指示があったときに取り入れます。

## 基本

- 対象：ユーザーが自分で撮った素材の編集。縦型ショート（1080×1920）と横型の通常動画（1920×1080）の両方。内容は案件ごとに臨機応変に決める
- ユーザーとのやり取りは日本語。動画内の文字や投稿文の言語は案件ごとに確認する
- 作業はclaude.aiのクラウドセッション（一時コンテナ）で行う。セッション終了でコンテナは消えるため、残すものは必ず commit して main に push する

## セッション開始時の準備

1. `ffmpeg`、`ffprobe`、`python3` が使えるか確認する
2. 新しい案件は `projects/<案件名>/` に作る

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
├── fonts/              ← 案件で使うフォント（edit.json の fontsdir で読み込む）
├── scripts/
└── output/             ← 完成動画（MP4はGit対象外）
```

## 進め方

1. **素材の受け取り**：ファイルの添付か、Google Driveのリンクで受け取る（下記）
2. **下調べ**：`python3 scripts/probe.py footage/<案件>/` で尺・解像度・fps・回転・HDR・音声を一覧にする。気になる点（可変fps、HDR、縦横の混在など）はユーザーに伝える
3. **構成の相談**：素材の中身をフレーム画像で確認し、使う区間・順番・つなぎ・テロップ・尺をユーザーと決める。確認していない素材の中身を推測で語らない
4. **確認用の書き出し**：`python3 scripts/edit.py projects/<案件>/edit.json --draft` で半分の解像度の軽い版を作り、構成とタイミングを見てもらう
5. **本番の書き出し**：`python3 scripts/edit.py projects/<案件>/edit.json` でマスターと投稿用（軽量版）を作る
6. **検証**：`python3 scripts/verify.py projects/<案件>/work/manifest.json` でフレーム数・音量を確認し、書き出された確認用フレームを目視する。数値を添えて報告する
7. **区切りごとに commit & push**（構成確定・完成）。素材と動画はGitに入れない

## スクリプト

- `probe.py <ファイル/フォルダ>`：素材の一覧（回転を反映した表示サイズ、fps、可変fpsの疑い、HDR、音声）
- `edit.py <edit.json> [--draft] [--no-light] [--clean]`：書き出し。出力は `output/<name>_master.mp4`・`_post.mp4`（`--draft` は `_draft.mp4`）
- `telop.py <telop.json> <telop.ass>`：テロップのASSを作る（ぼかし影、フェード、下からせり上がる出方 `anim: "rise"`）
- `verify.py <manifest.json> [--at 秒,...] [--silence]`：フレーム数の照合、確認用フレームの書き出し（冒頭・各つなぎの前後・末尾・指定時刻）、ラウドネスとピーク、無音区間

## edit.json の書式

```json
{
  "name": "lisbon_day1",
  "format": "vertical",
  "fps": 30,
  "look": "eq=saturation=1.08:contrast=1.03",
  "subtitles": "projects/lisbon_day1/telop.ass",
  "clips": [
    {"src": "footage/lisbon/IMG_0012.MOV", "in": 3.2, "out": 7.8,
     "transition": {"type": "fade", "dur": 0.5}},
    {"src": "footage/lisbon/IMG_0015.MOV", "in": 0, "out": 4, "speed": 1.5, "focus_x": 0.35},
    {"src": "footage/lisbon/IMG_0020.MOV", "in": 10, "out": 13, "fit": "blur", "volume": 0.3},
    {"src": "footage/lisbon/photo.jpg", "dur": 2.5, "fit": "fit"}
  ],
  "audio": [
    {"src": "footage/lisbon/narration.wav", "start": 0.4, "volume_db": 2,
     "fade_in": 0, "fade_out": 0}
  ]
}
```

- `format`：`vertical`（1080×1920）／`horizontal`（1920×1080）／`square`（1080×1080）／`{"w":..,"h":..}`
- `fps`：出力のフレームレート（整数）。素材がバラバラでも、ここに揃える
- `look`：全クリップにかける色調整（ffmpegのフィルタ文字列）。クリップごとに外すなら `"look": false`
- クリップの項目
  - `in`／`out`：素材の使う区間（秒）。`out` を省くと素材の最後まで。静止画は `dur`（秒）
  - `fit`：`fill`（はみ出しを切る。既定）／`fit`（全体を収めて黒帯）／`blur`（全体を収めて、背景をぼかした同じ映像で埋める）
  - `focus_x`／`focus_y`：`fill` で切り出す位置（0＝左・上、0.5＝中央、1＝右・下）。横の素材を縦型にするときの寄せ先
  - `speed`：再生速度（2＝倍速、0.5＝スロー）。音声も同じ速度で、音程は保つ
  - `volume`：素材の音の大きさ（0で無音）
  - `vf`：このクリップだけにかける追加フィルタ
  - `transition`：**次のクリップへの**つなぎ。`type` は `cut`（既定）か ffmpeg xfade の種類（`fade`、`fadeblack`、`fadewhite`、`dissolve`、`slideleft`、`wipeleft`、`zoomin`、`smoothleft` など）、`dur` は秒
  - `tonemap`：HDR素材を自動でSDRに変換する（既定 true）
- `audio`：ナレーションやBGMなど外部の音声。`start` は完成版の何秒目から鳴らすか。`in`／`out` で素材の使う区間
- `clip_audio`：false にすると素材の音をすべて使わない
- `chunk_max`：チャンク1本の最長秒数（既定20）

## 編集の事故防止（The Next Destination の制作で実際に起きた事故から）

- **長い単一パスのエンコードはしない。** 区間ごとのチャンク → concat demuxer で `-c copy` 結合 → 最後に音声を mux。edit.py はこの手順で作ってある（単一エンコードが中断されて moov のない再生不能ファイルになった事故が複数回あった）
- 音声は **48kHz WAV で組み立ててから** mux する。連結したMP3を `-c copy` で入れるとタイムスタンプが壊れ、映像の末尾が欠けた事故がある
- `scale` を `eval=frame` で動かすようなズーム演出は、**その区間だけ別ファイルとして先にレンダリング**する（大きなフィルタグラフの中に入れると ffmpeg が落ちる）。`crop` の w/h はフレームごとに動かないので、cropでズームは作れない
- 完成の確認は、コンテナの再生時間ではなく **`ffprobe -count_frames` のフレーム数** と **フレーム画像の目視** で行う
- 出力は「マスター」（crf 16）と「投稿用（軽量版）」（crf 21、maxrate 14M）の2種を別名で置く

## スマホ素材の注意

- **回転**：縦で撮った動画は「横の映像＋回転メタデータ」で保存されていることが多い。ffmpeg は自動で回転を反映する。probe.py の表示サイズは回転後の値
- **可変フレームレート**：スマホ動画はfpsが一定でないことがある。edit.py は下ごしらえで `fps` フィルタにより固定fpsにする
- **HDR**（iPhoneの既定はHLG）：そのまま扱うと白っぽく色が抜ける。edit.py はHDRを検出すると zscale＋tonemap でSDRに変換する。色が意図どおりかはフレーム画像で確認し、気になる場合は `look` で整える
- **写真の向き**：JPEGのEXIF回転は ffmpeg では反映されない。横倒しになっていたら回転させた画像を作ってから使う

## テロップ・仕上がりの質

- **安っぽい仕上がりにしない。** 安易なフォント・色・縁取り・エフェクトで質を落とさない
- テロップの位置は、フォントの数値計算ではなく**実際に書き出したフレーム画像で測って**決める（libassの描画幅は計算より狭くなることがある）
- 英字は Inter が入っている。日本語の質の良いフォントは環境に入っていない（IPAゴシック程度）。日本語テロップを使う案件では、使うフォントをユーザーと決め、`fonts/` に置いて使う

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
