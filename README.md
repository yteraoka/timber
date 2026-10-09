# timber

共有カレンダーサービス [TimeTree](https://timetreeapp.com/) から、指定した日付の予定を取得するコマンドラインツールです。予定を読み上げる音声ファイル (Google Home などで再生する用) も作成できます。

TimeTree Web 版が内部で利用している API を使っています。公式に公開された API ではないため、TimeTree 側の変更で動かなくなる可能性があります。

## 必要なもの

- [uv](https://docs.astral.sh/uv/)
  - Python 本体と依存パッケージ (`requests`, `python-dateutil`, `google-genai`) は `uv run` が自動でインストールします。使う Python のバージョンは `.python-version`、依存パッケージのバージョンは `uv.lock` で固定しています
- メールアドレスとパスワードでログインできる TimeTree アカウント
  - Apple / Google / Facebook アカウントでのログインには対応していません
- 読み上げ音声を作る場合は [Gemini API](https://ai.google.dev/gemini-api/docs/speech-generation) の API キー

## セットアップ

```sh
git clone https://github.com/yteraoka/timber.git
cd timber
uv sync
```

`uv sync` を省略しても、初回の `uv run` で同じ準備 (`.venv` の作成と依存パッケージのインストール) が行われます。

## 使い方

ログイン情報を環境変数で渡します。

```sh
export TIMETREE_USERNAME=you@example.com
export TIMETREE_PASSWORD=your-password
```

[direnv](https://direnv.net/) を使う場合は `.envrc` に書いておくと便利です (`.envrc` は `.gitignore` 済み)。

```sh
# 今日の予定
uv run timetree

# 指定日の予定
uv run timetree 2026-10-01

# カレンダーを指定し、キープを除外して JSON で出力
uv run timetree 2026-10-01 -c 家族の予定 --exclude-keep --json
```

リポジトリの外からも `timetree` コマンドとして使いたい場合は、uv のツールとしてインストールします。

```sh
uv tool install .
timetree 2026-10-01
```

### オプション

| オプション | 説明 |
| --- | --- |
| `date` | 取得する日付 (`YYYY-MM-DD`)。省略時は今日 |
| `-c`, `--calendar` | 対象カレンダー。名前・alias_code (URL の `/calendars/xxxx` 部分)・ID のいずれかで指定。複数指定可。省略時は全カレンダー |
| `--tz` | 日付の解釈と表示に使うタイムゾーン (既定: `Asia/Tokyo`) |
| `--exclude-keep` | キープ (`category` が 2 の予定) を除外する |
| `--session-file` | ログインセッションの保存先 (既定: `$XDG_CACHE_HOME/timetree/session.json`、未設定なら `~/.cache/timetree/session.json`) |
| `--json` | JSON で出力する |

### 出力例

テキスト出力では、終日予定を先頭に、その後に開始時刻順で 1 行ずつ表示します (タブ区切り)。

```
終日	燃えるゴミ	[家族の予定]
終日 2026-09-30 - 2026-10-02	出張	[家族の予定]
10:00 - 11:00	歯医者	[家族の予定]	@駅前歯科
```

`--json` を付けると次の形式で出力します。

```json
[
  {
    "calendar": "家族の予定",
    "title": "歯医者",
    "all_day": false,
    "start": "2026-10-01T10:00:00+09:00",
    "end": "2026-10-01T11:00:00+09:00",
    "location": "駅前歯科",
    "note": "",
    "url": "",
    "category": 1
  }
]
```

終日予定の `start` / `end` は日付 (`YYYY-MM-DD`) で、`end` はその日を含む最終日です。

## 予定の読み上げ音声を作る

`timetree-tts` は指定日の予定を読み上げ用の文章にし、Gemini API の TTS モデルで音声ファイルにします。TimeTree のログイン情報に加えて、Gemini API のキーを環境変数で渡します (`GOOGLE_API_KEY` でも可)。

```sh
export GEMINI_API_KEY=your-api-key

# 今日の予定を schedule.wav に出力
uv run timetree-tts

# Flash-Lite モデルで MP3 に出力
uv run timetree-tts --model flash-lite -o today.mp3

# 音声を作らず、読み上げる文章だけ確認する
uv run timetree-tts --text-only
```

読み上げる文章は次のようになります。

```
TimeTreeです。10月9日、金曜日。今日の予定は3件です。
終日、燃えるゴミ。
10月8日から10月10日まで、出張。
10時から11時30分まで、歯医者、場所は駅前歯科。
以上です。
```

### Gemini API キーの作成

1. [Google AI Studio の API キーのページ](https://aistudio.google.com/apikey) を開き、Google アカウントでログインします (初回は利用規約への同意を求められます)
2. 「API キーを作成」(Create API key) を押します
3. キーを紐付ける Google Cloud プロジェクトを選びます。プロジェクトが無ければ、その場で新しく作成できます
4. 表示された API キーをコピーし、`GEMINI_API_KEY` に設定します

`TIMETREE_USERNAME` などと同じく、`.envrc` に書いておくと便利です。API キーはパスワードと同じように扱い、リポジトリにコミットしないでください。

API キーは無料枠で使い始められますが、無料枠にはリクエスト数の制限があり、送信した内容が Google のサービス改善に使われることがあります。制限を超えて使う場合や、データを改善に使わせたくない場合は、AI Studio でプロジェクトに課金 (Billing) を設定してください。料金と制限は [料金ページ](https://ai.google.dev/gemini-api/docs/pricing) と [レート制限のページ](https://ai.google.dev/gemini-api/docs/rate-limits) で確認できます。

### オプション

`date`, `-c`, `--tz`, `--exclude-keep`, `--session-file` は `timetree` と同じです。

| オプション | 説明 |
| --- | --- |
| `-o`, `--output` | 出力ファイル (既定: `schedule.wav`)。拡張子で形式を決めます (`.wav`: 24kHz モノラル 16bit PCM の WAV、`.mp3`: MP3) |
| `-m`, `--model` | TTS モデル。`flash` (`gemini-3.8-flash-tts`、既定)、`flash-lite` (`gemini-3.8-flash-lite-tts`)、またはモデル ID をそのまま指定 |
| `--voice` | 声の名前 (既定: `Kore`)。`Puck`, `Charon`, `Aoede` など。一覧は [ドキュメント](https://ai.google.dev/gemini-api/docs/speech-generation) を参照 |
| `--style` | 話し方の指示 (既定: 朝のお知らせのように明るく落ち着いたトーン)。空文字を渡すと指定しません |
| `--yomi-file` | 読み方の辞書 (既定: `$XDG_CONFIG_HOME/timetree/yomi.toml`、未設定なら `~/.config/timetree/yomi.toml`)。既定の場所に無ければ辞書なしで動きます |
| `--text-only` | 音声を作らず、読み上げる文章を表示して終了します |

### 読み方の辞書

TTS モデルが予定のタイトルや場所の読み方を間違える場合は、読み方の辞書に登録しておくと、その語を読みに置き換えてから音声にします。辞書は `語 = "読み"` の形式の TOML ファイルです。

```toml
"上野" = "うえの"
"上野駅" = "うえのえき"
"駅前歯科" = "えきまえしか"
"PTA" = "ピーティーエー"
```

- 置き換えるのは予定のタイトルと場所だけです
- 長い語を優先して置き換えます (上の例の「上野駅」は「上野」より先に一致します)。置き換えた後の文字列が再度置き換えられることはありません
- ひらがなにすると抑揚が不自然になる場合は、`"上野" = "上の"` のように漢字を混ぜた読みも使えます
- `--text-only` で置き換え後の文章を確認できます

## 仕組み

1. `https://timetreeapp.com/signin` の `<meta name="csrf-token">` から CSRF トークンを取得し、`PUT /api/v1/auth/email/signin` でログインします
2. `GET /api/v2/calendars` でカレンダー一覧を取得します
3. `GET /api/v1/calendar/{id}/events/sync` で予定を取得します。日付範囲を指定する API が無いため、`chunk` が `true` の間 `since` を付けて取得を繰り返し、全件を取得してから指定日の予定を絞り込みます
4. 繰り返し予定は `RRULE` を展開して判定します

### ログインセッションの再利用

ログイン API には回数制限があり、短時間に何度もログインすると `HTTP 429` で失敗します。これを避けるため、ログイン後のセッション (Cookie と CSRF トークン) を `--session-file` のパスにパーミッション 600 で保存し、次回以降の実行で再利用します。保存したセッションが無効になっている場合だけ自動でログインし直します。

`HTTP 429` が出た場合は、しばらく時間を置いてから再実行してください。

## 注意事項

- キープを `category` が 2 の予定として扱っていますが、TimeTree の仕様として確認したものではありません
- 予定を毎回全件取得するため、予定の多いカレンダーでは実行に数秒かかります
