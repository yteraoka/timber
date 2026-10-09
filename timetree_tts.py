"""TimeTree の指定日の予定を読み上げる音声ファイルを作る。

予定を読み上げ用の文章にし、Gemini API の TTS モデルで音声にする。
TimeTree の認証情報は timetree コマンドと同じく TIMETREE_USERNAME / TIMETREE_PASSWORD、
Gemini API のキーは GEMINI_API_KEY (または GOOGLE_API_KEY) から読む。
予定のタイトルと場所は、読み方の辞書 (yomi.toml) で読み仮名に置き換えてから読み上げる。

    uv run timetree-tts -o today.wav
    uv run timetree-tts 2026-09-30 --model flash-lite -o schedule.mp3
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import tomllib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from timetree import Occurrence, TimeTreeError, add_fetch_arguments, fetch

MODELS = {
    "flash": "gemini-3.8-flash-tts",
    "flash-lite": "gemini-3.8-flash-lite-tts",
}
DEFAULT_VOICE = "Kore"
DEFAULT_STYLE = "朝のお知らせのように、明るく落ち着いたトーンで、聞き取りやすくゆっくりと"
# 出力ファイルの拡張子と Gemini API に指定する MIME タイプの対応
MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
}
WEEKDAYS = "月火水木金土日"


def default_yomi_file() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(config_home) / "timetree" / "yomi.toml"


def load_yomi(path: Path) -> dict[str, str]:
    """読み方の辞書 (語 = "読み" の TOML) を読む。"""
    with path.open("rb") as f:
        data = tomllib.load(f)
    bad = [k for k, v in data.items() if not isinstance(v, str) or not k]
    if bad:
        raise ValueError(f"readings must be non-empty keys with string values: {', '.join(bad)}")
    return data


def apply_yomi(text: str, yomi: dict[str, str]) -> str:
    """辞書の語を読みに置き換える。長い語を優先し、置き換えた結果は再度置き換えない。"""
    if not yomi or not text:
        return text
    pattern = re.compile("|".join(map(re.escape, sorted(yomi, key=len, reverse=True))))
    return pattern.sub(lambda m: yomi[m.group(0)], text)


def _date_ja(d: date) -> str:
    return f"{d.month}月{d.day}日"


def _time_ja(dt: datetime) -> str:
    return f"{dt.hour}時" + (f"{dt.minute}分" if dt.minute else "")


def _describe(o: Occurrence, day: date, yomi: dict[str, str]) -> str:
    """1 件の予定を読み上げ用の文にする。"""
    title, location = apply_yomi(o.title, yomi), apply_yomi(o.location, yomi)
    if o.all_day:
        start, end = date.fromisoformat(o.start), date.fromisoformat(o.end)
        when = "終日" if start == end else f"{_date_ja(start)}から{_date_ja(end)}まで"
        sentence = f"{when}、{title}"
    else:
        start, end = datetime.fromisoformat(o.start), datetime.fromisoformat(o.end)

        # 前日から、または翌日以降まで続く予定は日付も読む
        def at(dt: datetime) -> str:
            return _time_ja(dt) if dt.date() == day else f"{_date_ja(dt.date())}{_time_ja(dt)}"

        when = f"{at(start)}から" if start == end else f"{at(start)}から{at(end)}まで"
        sentence = f"{when}、{title}"
    if location:
        sentence += f"、場所は{location}"
    return sentence + "。"


def build_script(day: date, occurrences: list[Occurrence], today: date, yomi: dict[str, str] | None = None) -> str:
    """予定一覧から読み上げる文章を作る。"""
    label = "今日" if day == today else "明日" if (day - today).days == 1 else ""
    header = f"TimeTreeです。{_date_ja(day)}、{WEEKDAYS[day.weekday()]}曜日"
    if not occurrences:
        return f"{header}。{label or 'この日'}の予定はありません。"
    lines = [f"{header}。{label or 'この日'}の予定は{len(occurrences)}件です。"]
    lines += [_describe(o, day, yomi or {}) for o in occurrences]
    lines.append("以上です。")
    return "\n".join(lines)


def synthesize(text: str, model: str, voice: str, style: str, mime_type: str) -> bytes:
    # google-genai の import は数百ミリ秒掛かるため、--text-only では読み込まない
    from google import genai

    client = genai.Client()
    content: dict = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    interaction = client.interactions.create(
        model=model,
        input=[{"type": "user_input", "content": [content]}],
        response_format={"type": "audio", "mime_type": mime_type},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    audio = interaction.output_audio
    if audio is None or not audio.data:
        raise RuntimeError(f"no audio in response (status: {interaction.status})")
    return base64.b64decode(audio.data)


def main() -> int:
    parser = argparse.ArgumentParser(description="TimeTree の指定日の予定を読み上げる音声ファイルを作る")
    add_fetch_arguments(parser)
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("schedule.wav"),
        help="出力ファイル。拡張子 (.wav / .mp3) で形式を決める (default: schedule.wav)",
    )
    parser.add_argument(
        "-m", "--model", default="flash",
        help=f"TTS モデル。flash ({MODELS['flash']}) / flash-lite ({MODELS['flash-lite']}) "
        "またはモデル ID (default: flash)",
    )
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"声の名前 (default: {DEFAULT_VOICE})")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="話し方の指示。空文字で指定なし")
    parser.add_argument(
        "--yomi-file", type=Path,
        help="読み方の辞書 (default: $XDG_CONFIG_HOME/timetree/yomi.toml。無ければ使わない)",
    )
    parser.add_argument("--text-only", action="store_true", help="音声を作らず、読み上げる文章を表示して終わる")
    args = parser.parse_args()

    mime_type = MIME_TYPES.get(args.output.suffix.lower())
    if not args.text_only and mime_type is None:
        parser.error(f"unsupported output extension: {args.output.suffix} (use {' / '.join(MIME_TYPES)})")
    model = MODELS.get(args.model, args.model)

    # 既定の辞書は無ければ使わない。--yomi-file で指定したものは無ければエラー
    yomi_file = args.yomi_file or default_yomi_file()
    yomi: dict[str, str] = {}
    if args.yomi_file or yomi_file.exists():
        try:
            yomi = load_yomi(yomi_file)
        except OSError as e:
            print(f"error: failed to load yomi file: {e}", file=sys.stderr)
            return 1
        except (tomllib.TOMLDecodeError, ValueError) as e:
            print(f"error: failed to load yomi file {yomi_file}: {e}", file=sys.stderr)
            return 1

    try:
        day, results = fetch(args)
    except (TimeTreeError, requests.RequestException) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    text = build_script(day, results, datetime.now(ZoneInfo(args.tz)).date(), yomi)
    if args.text_only:
        print(text)
        return 0

    try:
        audio = synthesize(text, model, args.voice, args.style, mime_type)
    except Exception as e:  # google-genai の例外は種類が多いのでまとめて扱う
        print(f"error: TTS failed: {e}", file=sys.stderr)
        return 1
    args.output.write_bytes(audio)
    print(f"wrote {args.output} ({model}, {len(audio)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
