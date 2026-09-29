# timber

共有カレンダーサービス [TimeTree](https://timetreeapp.com/) から、指定した日付の予定を取得するコマンドラインツールです。

TimeTree Web 版が内部で利用している API を使っています。公式に公開された API ではないため、TimeTree 側の変更で動かなくなる可能性があります。

## 必要なもの

- [uv](https://docs.astral.sh/uv/)
  - Python 本体と依存パッケージ (`requests`, `python-dateutil`) は `uv run` が自動でインストールします。使う Python のバージョンは `.python-version`、依存パッケージのバージョンは `uv.lock` で固定しています
- メールアドレスとパスワードでログインできる TimeTree アカウント
  - Apple / Google / Facebook アカウントでのログインには対応していません

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
