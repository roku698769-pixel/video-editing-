#!/usr/bin/env python3
"""Whisper（faster-whisper）で音声・動画を文字起こしし、SRT／JSON（単語ごとの時刻付き）を書き出す。

使い方:
  python3 scripts/transcribe.py <音声または動画> [--lang ja] [--model large-v3] [--out <出力の拡張子なしパス>]
                                [--max-chars 16] [--prompt "固有名詞など"]

出力:
  <out>.srt   字幕（1行の文字数を --max-chars で区切る。単語の時刻で切るので、字幕の切れ目が発話とずれない）
  <out>.json  区間・単語ごとの開始／終了秒と確からしさ（テロップの出現タイミングに使う）

既定値は品質優先：モデル large-v3、beam_size 5、VAD（無音区間の除去）あり、単語ごとの時刻あり。
モデルは models/whisper/ に保存する（約2.9GB、Git対象外）。初回だけ Hugging Face から取得する。
音声・動画とも ffmpeg で 16kHz モノラルに変換してから渡す。
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models", "whisper")


def load_audio(src):
    """ffmpeg で 16kHz モノラル float32 にして numpy 配列で返す（faster-whisper 内蔵の PyAV 読み込みは版の食い違いで落ちることがあるため使わない）。"""
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-af", "aresample=resampler=soxr", "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


PUNCT = ("。", "、", "！", "？", ".", ",", "!", "?", "…")


def split_words(words, max_chars, cjk):
    """単語列を max_chars 以内の塊に再帰的に分ける。切る位置は「句読点の直後」「発話の間（ポーズ）が長い所」「真ん中に近い所」を優先する。"""
    join = (lambda ws: "".join(w["word"].strip() for w in ws)) if cjk else (lambda ws: "".join(w["word"] for w in ws).strip())
    text = join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [words]
    best, best_score = None, None
    total = len(text)
    for i in range(1, len(words)):
        left = join(words[:i])
        gap = max(0.0, words[i]["start"] - words[i - 1]["end"])
        punct = left.endswith(PUNCT)
        balance = abs(len(left) - total / 2) / total  # 0 が真ん中
        score = (3.0 if punct else 0.0) + min(gap, 1.0) * 4.0 - balance * 2.0
        if best_score is None or score > best_score:
            best, best_score = i, score
    return split_words(words[:best], max_chars, cjk) + split_words(words[best:], max_chars, cjk)


def cues_from_words(segments, max_chars, cjk):
    cues = []
    for seg in segments:
        words = seg["words"] or [{"start": seg["start"], "end": seg["end"], "word": seg["text"]}]
        for part in split_words(words, max_chars, cjk):
            text = "".join(w["word"].strip() for w in part) if cjk else "".join(w["word"] for w in part).strip()
            cues.append({"start": part[0]["start"], "end": part[-1]["end"], "text": text})
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--lang", default=None, help="ja / en など。省略で自動判定")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--compute", default="int8", help="int8（既定）/ float32（わずかに精度寄り・約2倍遅い）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-chars", type=int, default=None, help="字幕1枚の最大文字数（既定 ja 16 / それ以外 42）")
    ap.add_argument("--prompt", default=None, help="固有名詞や表記の手がかり（initial_prompt）。--lang ja では省略時に句読点を出させる文を使う")
    ap.add_argument("--no-vad", action="store_true")
    a = ap.parse_args()

    from faster_whisper import WhisperModel

    out = a.out or os.path.splitext(a.src)[0]
    if True:
        wav = load_audio(a.src)
        t0 = time.time()
        prompt = a.prompt
        if prompt is None and a.lang == "ja":
            prompt = "こんにちは。今日は、いい天気ですね。"  # 句読点を付けて書き起こさせるための手がかり
        model = WhisperModel(a.model, device="cpu", compute_type=a.compute,
                             download_root=MODEL_DIR, cpu_threads=os.cpu_count() or 4)
        segs, info = model.transcribe(
            wav, language=a.lang, beam_size=5, word_timestamps=True,
            vad_filter=not a.no_vad, initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        segments = []
        for s in segs:
            segments.append({
                "start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3),
                "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                           "prob": round(w.probability, 3)} for w in (s.words or [])],
            })
        elapsed = time.time() - t0

    lang = info.language
    cjk = lang in ("ja", "zh", "ko")
    max_chars = a.max_chars or (16 if cjk else 42)
    cues = cues_from_words(segments, max_chars, cjk)

    with open(out + ".srt", "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n\n")
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump({"language": lang, "language_probability": round(info.language_probability, 3),
                   "duration": round(info.duration, 3), "model": a.model, "segments": segments},
                  f, ensure_ascii=False, indent=1)

    low = [w for s in segments for w in s["words"] if w["prob"] < 0.5]
    print(f"言語 {lang}（{info.language_probability:.2f}）／音声 {info.duration:.1f}秒／処理 {elapsed:.1f}秒")
    print(f"字幕 {len(cues)}枚 → {out}.srt ／ 詳細 → {out}.json")
    if low:
        print("確からしさが低い語（聞き違いの可能性。確認が必要）：" +
              "、".join(f"{w['word'].strip()}@{w['start']:.1f}s" for w in low[:20]))


if __name__ == "__main__":
    sys.exit(main())
