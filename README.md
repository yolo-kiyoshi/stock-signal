# TOMOSHIBIYORI

保有銘柄、ウォッチリスト、市場全体候補の日足とルールベース分析を確認するための
投資判断アプリです。「日足から、明日の判断に小さな灯を。」をコンセプトに、
未来を断定せず、毎日の投資判断に使える手掛かりを提示します。
Python 3.12、FastAPI、Next.js、Auth.js、PostgreSQL、SQLAlchemy、Alembic、Plotly、
Docker Composeを使用します。

Next.jsへの画面移行、本人認証、BFF（Backend for Frontend）、外部公開前の構成は、
[フロントエンド・認証・実行環境設計](docs/frontend-auth-architecture.md)に分けて記載しています。
機能仕様・理論的背景・残課題の一覧は[資料目次](docs/README.md)を参照してください。

アプリ名は`TOMOSHIBIYORI`です。既存利用者との互換性を保つため、リポジトリ名、
Pythonパッケージ名、CLIコマンドは引き続き`stock-signal`を使用します。

現在は機械学習を使わず、移動平均、モメンタム、RSI、値幅に加え、完成済みの
レクタングル、トライアングル、ダブルトップ／ボトム、ヘッド＆ショルダーズを検出します。
出来高、ATR基準のブレイク幅・窓開け、暫定流動性を確認し、ロング中心の「購入候補」
「様子見」「新規購入回避」へ分類します。表示する判定スコアとパターン一致度は将来の
確率ではありません。

## 初期設定と日常操作

Docker Desktop（Docker Composeを含む）とmakeが必要です。アプリの実行・品質検査は
すべてDocker Compose内で行います。引数なしの`make`で日本語のコマンド一覧を表示します。

既存環境は`make start`で起動できます。初めて設定する場合は次の順で実行してください。

```bash
make setup
# .envを編集して取得元とAPIキーを設定する（秘密情報はGitへ登録しない）
make start
make update
```

`make setup`は既存の`.env`を上書きしません。J-Quantsを使う場合は
`MARKET_DATA_PROVIDER=jquants`、`JQUANTS_PLAN=light`、`JQUANTS_API_KEY`を設定します。
既に動作している環境のDB接続設定をサンプル値で上書きしないでください。

| やりたいこと | コマンド |
|---|---|
| 起動・コードや設定変更を反映 | `make start` |
| 日足・財務・分析候補の日次更新 | `make update` |
| ウォッチ・保有の財務だけ更新 | `make financials` |
| 特定銘柄の財務だけ更新 | `make financials SYMBOLS="7203 8058"` |
| 寄り付き前の市場環境更新 | `make preopen` |
| 状態・異常を確認 | `make status`、`make logs` |
| 画面ログを見る | `make logs SERVICE=frontend` |
| 画面・APIのコード再読込 | `make restart` |
| 画面・API・DBを停止 | `make stop` |
| 全品質検査 | `make check` |

画面は **http://localhost:3000** です。`make start`はイメージのビルド、画面の依存関係同期、
DBの起動・マイグレーション、API・画面の再作成を順に実行してバックグラウンド起動します。
初回はビルドと画面コンパイルに時間がかかります。起動では市場データを取得しません。

`make update`は完了まで待機し、失敗・一部失敗時は非ゼロで終了します。成功後は画面を
再読み込みしてください。APIを再起動せず最新データを参照できます。`make financials`は
財務同期のみで、市場一覧の再生成には`make update`が必要です。
`make preopen`は別途Alpha Vantageの設定を必要とするため、日次更新とは分離しています。
全市場の長期履歴を初回投入する場合だけ`make bootstrap`を実行し、その後`make update`を実行します。

`make stop`はデータボリュームを削除しません。実行中のデータ更新・検査が完了してから停止してください。
`make logs`のCtrl+Cはログ表示だけを終了します。`make restart`は既存のAPI・画面を再起動するだけです。
DBも停止している場合や依存関係・`.env`を変更した場合は`make start`を使ってください。

開発用には`make build`、`make migrate`、`make test`、`make lint`、`make frontend-check`も用意しています。
`make check`はpytest → ruff → TypeScript → ESLintの順に実行し、途中で失敗した場合は停止します。
makeの並列指定でもDB関連操作は直列実行します。任意のCLI引数や期間指定が必要な場合は、
以降のDocker Composeコマンドを直接使用できます。

`app`、`migrate`、`batch`、`api`、`test`は同じ`tomoshibiyori:local`イメージを使い、
`frontend`はNext.js専用イメージを使います。依存関係を変更した場合は、対応するイメージを
再ビルドしてください。

`api`と`batch`の起動時にも、依存関係として`migrate`が先に実行されます。DBスキーマは
アプリ起動コードから暗黙に変更せず、Alembicの版管理されたマイグレーションで更新します。

## Webアプリケーション

### Next.js画面

外部公開を見据えた新しい画面は、Next.js、Auth.js、FastAPIの認証付きAPIで構成します。
ローカルでは次のコマンドでPostgreSQL、マイグレーション、分析API、フロントエンドを起動します。

```bash
make start
```

ブラウザで`http://localhost:3000`を開きます。初期値ではローカル開発時だけ本人認証を省略します。
GitHub認証をローカルで確認する場合は、`.env`の`AUTH_ALLOW_INSECURE_LOCAL=false`とし、
`AUTH_SECRET`、`AUTH_GITHUB_ID`、`AUTH_GITHUB_SECRET`、`AUTH_ALLOWED_GITHUB_ID`を設定してください。
FastAPIの分析APIは`http://localhost:8001`で起動しますが、Bearerトークン必須であり、ブラウザから
直接利用せずNext.jsのBFFを経由します。現段階では外部サービスへデプロイしません。

旧FastAPI＋Jinja画面は、主要機能の移植完了に伴い削除しました。FastAPIはBearer認証付きの
JSON APIに専念し、画面はNext.jsだけを使用します。`api`と`frontend`には市場データAPIキーを
渡さず、画面はPostgreSQLへ保存済みのデータだけを参照します。

画面では次を確認できます。

- 保有銘柄と、保有情報から独立したウォッチリスト
- 楽天証券の「資産残高（すべて）」CSVによる国内株式の保有一括登録
- 同期済み銘柄マスタから最大200件をまとめてウォッチリストへ追加
- 流動性上位銘柄を対象とする市場全体の分析候補
- 1か月、3か月、6か月、1年、全期間の日足
- 任意の開始日と終了日による期間指定
- ローソク足、出来高、運用スタイルに合う単純移動平均線
- Wilder方式のRSIと、過去の局所高値から求める抵抗帯候補
- 移動平均、RSI、抵抗帯を個別に表示・非表示にする切り替え
- スイング（5営業日先）と中長期の買い場（20営業日先）の目的別分析
- 中長期での20日線・60日線、直近20日安値、上抜け後の旧抵抗帯による支持候補
- 支持候補への接触、終値維持、日足反発を分けた押し目条件進捗
- 購入候補、様子見、新規購入回避の候補と判定要因
- テクニカル方向とは別の「形を確認・条件付き・購入条件が整う・押し目待ち」
- 実行用損切り目安、参考目標、実行用リワード／リスク
- 市場全体候補からのワンクリック・ウォッチリスト追加
- 銘柄ナビゲーションでの市場区分、商品種別、TOPIX-17、東証33業種、決算接近、
  流動性、日足鮮度のタグ表示
- 証券コード・銘柄名・分類の横断検索と、各タグを組み合わせた絞り込み
- 完成済みチャートパターンと一致度、形成期間
- パターン発生後の新規検討期間、勢い弱化、目標、無効化、期限切れ
- 底固め、転換準備、「あと1条件」、転換初動を示す条件進捗
- 転換水準、未達条件、参考目標、無効化水準、参考リスクリワード
- Light財務サマリーによる業績・評価倍率の確認と、資金・銘柄／業種集中・売買コストを反映する上限株数
- TOPIXに対する20日・60日の相対力と単回帰ベータ
- ブレイク時の出来高倍率、ブレイク幅、窓開け
- 最新日足のデータ鮮度（7暦日を超える場合は購入候補を抑制）
- Lightプランで取得するTOPIXトレンドと決算予定日
- 米国株、原油、米国金利、ドル円から算出する寄り付き前の外部環境
- 外部環境が「強い警戒」の場合に、テクニカル方向を維持したまま新規購入候補を様子見へ変更
- Light対象外の業種指数、未契約アドオンを非活性表示
- 4文字の証券コードによるウォッチリスト一括登録
- データ取得元、最終日、調整有無
- 選択銘柄のテクニカル分析と最新Web情報を照合するAI最終確認
- AI調査本文中のクリック可能な引用と、参照情報一覧

株式投資とテクニカル分析を初めて学ぶ場合は、まず
[はじめての株式テクニカル分析とTOMOSHIBIYORIの読み方](docs/technical-analysis-foundations.md)を
参照してください。用語を理解した後、具体的な計算式と閾値は
[投資判断ロジック入門](docs/investment-judgment-guide.md)で確認できます。

候補一覧では20日線乖離・10/20日騰落率、抵抗までの余地、損切り幅を比較できます。
スイングはブレイク追随に加え、独立した押し目反発経路を評価します。
計算式・条件・未算出時の扱いは[スイング押し目反発と価格位置](docs/swing-pullback-and-diagnostics.md)を参照してください。

通常画面ではデイトレード向けに見えやすい1営業日分析を表示しません。スイングは3・5・10・20日、
中長期の買い場は10・20・60日の値動きを中心に評価します。20営業日先は長期保有期間そのものでは
なく、長期保有を開始する前の中期的な買い場確認です。上抜けを追うスイング判定とは分離し、
20日線・60日線の関係と長期線の傾きで中期トレンドを確認した後、20日線、60日線、直近20日安値、
上抜け後の旧抵抗帯への接触、終値での維持、反発日足を順に確認します。支持帯への接触だけでは
購入候補にしません。さらに財務数値の確認を必須にします。事業価値の評価は限定的です。

チャートでは、スイング選択時に5日・20日移動平均とRSI 14日、過去60営業日の抵抗帯候補を、
中長期選択時に20日・60日移動平均とRSI 28日、過去120営業日の抵抗帯候補に加え、
分析した20日線・60日線・直近20日安値・旧抵抗帯の支持候補を青い帯で表示します。
表示期間より前の保存済み日足も指標計算に使うため、1か月表示でも長期線が先頭から描画されます。
抵抗帯は、2回以上の局所高値をATRでまとめた機械的な候補であり、反落や売買を保証する価格では
ありません。画面の切り替えは表示だけを変え、投資判断ロジックの計算結果は変更しません。

### OpenAIによるAI最終確認

AI最終確認は、選択中の1銘柄について、保存済み日足から算出したテクニカル結果をOpenAIの
Responses APIへ渡し、`web_search`で最新の公式発表や関連報道と照合します。画面を開いただけでは
実行せず、「最新情報を検索して確認」を押した場合だけAPIを呼びます。AIはルールベース分析を
置き換えず、検討継続、保留、新規購入見送りの参考区分、反対材料、未確認事項を提示します。

`.env`へ次を設定してください。APIキーはFastAPIの`api`コンテナ内だけで読み、HTMLやAPIレスポンスへ
含めません。

```dotenv
OPENAI_API_KEY=設定したAPIキー
OPENAI_MODEL=gpt-5.5
OPENAI_MAX_OUTPUT_TOKENS=6000
```

依存パッケージが増えるため、初回はイメージを再ビルドします。

```bash
docker compose build app
docker compose up -d --force-recreate api frontend
```

実行ごとにモデルとWeb検索の利用料金が発生する場合があります。回答は保存せず、その場で画面へ
表示します。引用URLは本文中と参照情報一覧の両方でクリック可能にします。最新情報が見つからない
場合は推測せず、未確認として扱います。OpenAIへ送るのは銘柄名、証券コード、運用スタイル、
テクニカル分析結果であり、保有数量、平均取得単価、許容損失額、APIキーは送信内容に含めません。
APIの構成は
[OpenAI公式Web searchガイド](https://developers.openai.com/api/docs/guides/tools-web-search)に従います。
AI出力は投資助言、利益保証、自動注文ではありません。

AI回答の出力上限は既定で6,000トークンです。OpenAIの`max_output_tokens`には表示本文だけでなく
推論トークンも含まれます（[Responses API公式リファレンス](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)）。
APIが`incomplete`を返した場合、途中の文章を完成回答として表示せず、
画面に再実行または上限変更を案内します。必要な場合だけ`OPENAI_MAX_OUTPUT_TOKENS`を
2,000〜20,000の範囲で変更してください。回答プロンプト自体も日本語2,000文字程度を目安にし、
不必要に長い出力と利用料金を抑えます。

### 楽天証券CSVから保有銘柄を登録する

保有銘柄の成長ストーリーと改訂履歴を保存し、Codexのチャットで最新情報と照合できます。
「保有」で銘柄を選び、「投資ストーリーとチャット分析」から登録してください。
CSVプレビューでは数量・取得価格の差分とCSVにない既存保有を確認できます。
保有基準日は確認できる場合だけ入力します。未確認の場合、チャット分析資料の損益は未算出です。
チャットで「保有銘柄を最新情報でレビューして」と依頼すると、専用CLIによる資料生成・
調査結果の保存を行えます。この経路はLLM APIを呼びません。Codexの利用枠は別です。
[保存仕様・入力契約](docs/position-journal.md)を参照してください。

楽天証券で手動ダウンロードした「資産残高（すべて）」CSVを、銘柄ナビゲーションの
「保有」→「楽天証券CSVから取り込む」から登録できます。CSVはCP932（Shift_JIS系）とUTF-8に
対応し、2MB・保有商品詳細5,000行を上限とします。

1. CSVを選択して「内容を確認」を押す。
2. 登録・更新、対象外、要確認の件数と銘柄を確認する。
3. 「銘柄を登録・更新」を押して確定する。

TOMOSHIBIYORIが分析対象とする国内株式だけを登録します。米国株式、投資信託などは種類別の
件数を表示して除外します。4文字の証券コードが銘柄マスタにない場合は推測で登録せず、日次更新で
銘柄マスタを同期してから再度確認してください。

同じ証券コードが特定口座とNISAなど複数行に分かれている場合、保有数量を合算し、平均取得価額を
数量加重平均して1銘柄にまとめます。同じコードを再度取り込むと数量、平均取得価額、口座区分を
上書きするため、同じCSVを再実行しても二重加算されません。CSVにない既存保有は自動削除しない
安全なマージ方式です。不要な保有銘柄は一覧の削除ボタンから明示的に削除してください。
CSVの現在値・時価評価額は取り込まず、価格は従来どおりJ-Quantsの調整済み日足を正とします。

元CSVには資産残高などの個人情報が含まれるため、APIは取込処理後にファイル自体を保存しません。
ブラウザーから本人認証済みBFFを経由し、FastAPIのBearer認証付きAPIへ送信します。プレビューは
DBを変更せず、確定時だけ対象銘柄を一つのトランザクションで登録・更新します。

## CLI

```bash
# 動作確認
docker compose run --rm app stock-signal health

# データベース初期化
docker compose run --rm app stock-signal init-db

# Alpha Vantageの銘柄記号検索
docker compose run --rm app stock-signal search-symbol "Toyota Motor"

# 日足の取得・検証・保存
docker compose run --rm app stock-signal ingest TM

# ウォッチリストへ追加
docker compose run --rm app stock-signal watchlist-add TM \
  --name "トヨタ自動車 ADR" --exchange "NYSE" --currency "USD"

# 保存済み銘柄の一覧
docker compose run --rm app stock-signal list-symbols

# 単独HTMLレポートの生成
docker compose run --rm app stock-signal chart TM --output-dir /app/reports

# 直近1年の過去当てはめ実績HTMLを生成
docker compose run --rm app stock-signal historical-report 7203 \
  --output-dir /app/reports

# 判定期間を指定して生成
docker compose run --rm app stock-signal historical-report 7203 \
  --from 2024-01-01 --to 2025-12-31 --output-dir /app/reports

# 東京市場の寄り付き前に外部市場環境を取得・判定
docker compose run --rm preopen
```

`historical-report`は画面機能ではなく、実行のたびに`reports/`へ単独HTMLを出力するCLIです。
期間未指定時は保存済みの最新日足を基準に直近1年を対象とします。各取引日について、その日以前の
日足だけでスイング5日と中長期20日を再判定し、実績方向、一致・不一致、騰落率、ATR換算値を
一覧化します。購入候補が連続する場合は初日を一つの購入イベントとし、MFE（最大上昇）、
MAE（最大逆行）、参考目標・損切りへの到達も記録します。外部APIは呼ばず、PostgreSQLに保存済みの
調整済み日足だけを読み取ります。

仮の約定価格は判定翌営業日の始値とし、5または20番目の後続取引日の終値で照合します。判定日時点の
Wilder ATRに対してスイングは
±0.5 ATR、中長期は±1.0 ATR以上を上昇・下落、その内側を停滞と分類します。レポートの
「方向一致率」は3分類の当てはまりであり、売買戦略の勝率、利益率、将来確率ではありません。

## Alpha Vantage APIキー

1. `https://www.alphavantage.co/support/#api-key`で無料キーを取得する。
2. `.env`の`ALPHA_VANTAGE_API_KEY`へ設定する。
3. APIキーをソースコード、コマンド履歴、ログ、Gitへ記録しない。

無料の`TIME_SERIES_DAILY`は最新100件の未調整日足を返します。株式分割や配当は
調整されません。また、Alpha Vantageの検索結果に東証上場銘柄が含まれない場合が
あります。ADRを日本上場株として扱わないでください。

## J-Quants API V2（Lightプラン）

Lightプランの日通し株価四本値、TOPIX四本値、決算発表予定日をAPIキー認証で取得します。

1. J-QuantsでLightプランを契約する。
2. ダッシュボードでAPIキーを発行する。
3. `.env`へ次を設定する。

```dotenv
JQUANTS_API_KEY=発行したAPIキー
JQUANTS_PLAN=light
```

日次バッチが全上場銘柄マスタをPostgreSQLへ同期します。同期後は、Web画面へ証券コードを
カンマ、空白、改行区切りで最大200件入力すると、外部APIを呼ばずに正式名称付きで即時追加
できます。追加後はPostgreSQLに保存済みの日足から分析し、その銘柄をすぐ選択表示します。
マスタ同期前のコードだけを確認待ちにし、次回バッチで再照合します。通常の数字
4桁に加え、英字入りの4文字コードにも対応します。WebコンテナへAPIキーは渡しません。

登録済み銘柄は各行の「削除」からウォッチリスト対象外にできます。誤操作防止の確認後に
監視対象と登録状態だけを削除し、取得済みの日足とバッチ実行履歴は再利用・監査のため
保持します。同じ証券コードは後から再登録できます。

既定の異業種10銘柄をまとめて初期登録したい場合だけ、次のコマンドを使用します。

```bash
docker compose run --rm app stock-signal jquants-bootstrap
```

| コード | 銘柄 | 業種 |
|---|---|---|
| 7203 | トヨタ自動車 | 輸送用機器 |
| 6758 | ソニーグループ | 電気機器 |
| 8306 | 三菱UFJフィナンシャル・グループ | 銀行業 |
| 4502 | 武田薬品工業 | 医薬品 |
| 9432 | 日本電信電話 | 情報・通信業 |
| 8058 | 三菱商事 | 卸売業 |
| 9983 | ファーストリテイリング | 小売業 |
| 9503 | 関西電力 | 電気・ガス業 |
| 9020 | 東日本旅客鉄道 | 陸運業 |
| 1925 | 大和ハウス工業 | 建設業 |

Lightプランは60リクエスト/分、取得可能期間は過去5年です。アプリはプラン設定から安全な
リクエスト間隔と初回取得期間を自動設定します。APIのページネーションにも追従します。
Freeから移行した既存銘柄も、Lightでの初回バッチだけ過去5年をバックフィルし、完了後は
保存済み最終日の翌日から差分取得します。画面には取得元と実際の最終取引日が表示されます。

Light以上で公式提供されるバルクAPIを使い、全市場の日足を日付単位で取り込みます。バルクの
未調整値は監査用の`raw_*`列へ保持し、チャートと分析にはREST APIの調整済み四本値・出来高
だけを使います。ファイルごとに「バルク保存」と「調整済み化」の成否を別々に記録するため、
途中で失敗しても同じコマンドで完了済みの工程を飛ばして再開できます。
J-Quantsの全銘柄日足APIは`date`指定が必須なため、月次バルクの対象期間を平日ごとのAPI要求へ
分割します。Lightプランの60リクエスト/分を超えないよう、要求間隔も自動調整します。
取得した全市場日足は1,000行ずつUPSERTし、PostgreSQLの1文あたりパラメータ上限を超えない
ようにします。DBエラー時は巨大なSQL全文を表示せず、原因の先頭500文字だけを記録します。

```bash
docker compose run --rm app stock-signal jquants-bulk-bootstrap
```

進捗だけを外部APIへ接続せず確認する場合は、次を実行します。

```bash
docker compose run --rm app stock-signal jquants-bulk-status
```

`completed`が`total`と一致し、`incomplete`が`0`になって初めて全市場日足の同期完了です。
最新日だけ成功していても、過去ファイルが未完了ならWebと日次バッチは同期完了として扱いません。
未完了時は`issues`に最大20件のファイル名、未調整・調整済み工程の状態、短縮した失敗理由を
表示します。長いSQL全文を貼り付ける必要はありません。

画面に古い価格や古い同期状態が残る場合は、次の順序で確認します。`batch`だけでは、保有・
ウォッチ対象外の全市場銘柄について過去データを修復しません。

```bash
# 1. incompleteが0であることを確認
docker compose run --rm app stock-signal jquants-bulk-status

# 2. incompleteが残っていれば、全期間を再開
docker compose run --rm app stock-signal jquants-bulk-bootstrap

# 3. 分析結果を再生成
docker compose run --rm batch

# 4. Pythonコードを読み直すためAPIを再作成し、画面を更新
docker compose up -d --force-recreate api frontend
```

ブラウザの再読み込みだけでは、起動済みUvicornプロセスが読み込んだPythonコードは更新されません。

初回投入後の`batch`は、最後に正常取り込みした日より後だけでなく、直近14暦日（おおむね
10営業日）も毎回重複取得してUPSERTします。これにより、取引所による直近株価の訂正を
反映します。調整係数が1以外の株式分割・併合を検出した銘柄は、過去5年の調整済み日足を
自動で再取得します。UPSERTの一意キーは証券コード・取引日・取得元なので、重複行は増えません。
初回投入を省略した場合も直近90日から開始しますが、5年分のチャートや検証を利用するには
上記ブートストラップを実行してください。

| 項目 | Lightでの扱い | アプリの表示 |
|---|---|---|
| 日通し株価四本値・出来高 | 利用可能 | バルク日足、出来高、テクニカル分析に使用 |
| TOPIX四本値 | 利用可能 | 20・60営業日トレンド、相対力、ベータを評価。同期待ちは明示 |
| 決算発表予定日 | 利用可能 | 5日以内は様子見、6日後から分析期間内は警告表示 |
| TOPIX以外の指数 | 利用不可 | 業種指数対比を非活性表示 |
| TDnet／適時開示 | アドオン | 未契約メッセージ付きで非活性表示 |

業種指数対比はStandard以上、決算以外の適時開示要因はTDnetアドオン契約後にProviderを
追加して有効化する設計です。利用できない項目を0点として扱わず、投資判断への加点・減点から
除外します。

通常の`fetch`、`ingest`、`search-symbol`でもJ-Quantsを使う場合は、次も設定します。

```dotenv
MARKET_DATA_PROVIDER=jquants
```

## 日次データ取得バッチ

初回投入後は、独立した一回実行型のバッチサービスで差分を取得します。

日次運用では、役割の異なる二つの一回実行型ジョブを使います。

```text
08:30頃  preopen  米国株・原油・米国金利・ドル円を取得して当日の外部環境を判定
19:00頃  batch    日本株日足を更新し、テクニカル分析と市場全体候補を再生成
```

`preopen`はAlpha Vantageを5回呼び出すため、無料枠の短時間上限を避けて既定12.1秒間隔で
実行します。完了まで約50秒です。間隔は`MARKET_ENVIRONMENT_REQUEST_INTERVAL_SECONDS`で
変更できます。原油・米国金利は配信元の公表遅延があり得るため、観測日が4暦日を超えて古い
指標はスコアから除外します。取得指標が40%未満なら推測せず「未評価」にします。

```bash
docker compose run --rm preopen
```

判定結果と取得値は`market_regime_snapshots`、`market_observations`へ保存され、画面表示中に
外部APIを呼びません。外部環境が「強い警戒」の日だけ、新規の「購入候補」を「様子見」へ
変更します。銘柄の現在のテクニカル方向は変更せず、既存ポジションの自動売却判断にも
使用しません。

```bash
# 対象銘柄と取得元だけを表示し、外部APIは呼ばない
docker compose run --rm batch stock-signal daily --dry-run

# 差分取得、検証、保存、5・20営業日のルール分析を実行
docker compose run --rm batch
```

バッチは銘柄・取得元ごとの保存済み最終取引日を確認します。個別取得はその翌日以降、J-Quantsの
全市場日足とTOPIXは遡及訂正を拾うため直近14暦日も重複して要求します。
J-Quants利用時はTOPIX、決算予定日、全上場銘柄マスタ、全市場バルク日足を更新し、保有銘柄と
ウォッチリスト銘柄を補完します。その後、条件を満たす全銘柄を5・20営業日基準で分析します。
同じデータを再取得してもUPSERTされるため重複しません。1銘柄が失敗しても残りの処理を
続け、全体と銘柄別の結果を`pipeline_runs`、`pipeline_run_items`へ記録します。一部失敗または
全件失敗の場合、コマンドは終了コード1を返します。

「全市場バルク日足の取得」と「市場全体タブの分析対象」は別です。LightプランのバルクCSVから
取得した日足は、分析対象に選ばれなかった銘柄も含めて`daily_bars`へ保存します。一方、日次の
分析処理は25営業日以上の調整済み日足がある現行の普通株を、既定では全件対象にします。
`MARKET_SCREENING_LIMIT=0`が全件、正の整数を指定すると直近60営業日の平均売買代金順に
その件数へ制限します。中長期に必要な履歴が不足する銘柄はデータ不足として扱います。
APIは1ページ最大500件でオフセット取得し、画面は全件を検索・絞り込みした結果を
100件ずつ表示します。処理時間・保存量や購入候補の財務補完取得は対象数に応じて増えます。
既存環境でも`.env`の`MARKET_SCREENING_LIMIT=0`を設定し、`make update`で再分析してください。

同じPostgreSQLに対するバッチの多重起動はAdvisory Lockで拒否します。Web、バッチ、将来の
複数ホストから同じDBへ接続しても排他が有効です。定期実行はコンテナ内へ
cronを入れず、macOSの`launchd`や将来のクラウドスケジューラーから上記コマンドを1日1回
呼び出してください。

初期投入の`jquants-bulk-bootstrap`と継続処理の`daily`は役割が異なります。

```text
jquants-bulk-bootstrap  全市場5年分の日足と全上場銘柄マスタの初回投入
daily                    全市場差分、保有・ウォッチ銘柄、分析候補の日次更新
```

ここでいう「差分」には、遡及訂正を拾う直近14暦日の重複更新を含みます。初回の5年分投入は
API制限下で長時間かかるため、日次バッチのAdvisory Lock内では実行せず、進捗保存と再開機能を
持つ`jquants-bulk-bootstrap`へ分離しています。

銘柄マスタから消えた上場廃止銘柄は非活性にしますが、マスタ行と取得済み日足は削除しません。
将来のウォークフォワード検証で、現在も上場している銘柄だけを選ぶ生存者バイアスを抑えるためです。

### データ処理の運用フロー

`jquants-bulk-bootstrap`は常駐処理ではなく、初期構築、DB再作成、過去期間の再投入が必要な
場合にだけ実行する一回実行型コマンドです。Lightプランの既定値では過去5年を対象とし、
調整済み日足のページネーションとLightプランのリクエスト上限を守りながら実行するため、
通常の開発用PCでは約60〜180分が目安です。対象期間、休止銘柄数、ネットワーク状況によって
前後します。途中終了しても同じコマンドで再開できます。

```bash
# 1. 初回だけ、銘柄マスタと全市場の過去日足を投入
docker compose run --rm app stock-signal jquants-bulk-bootstrap

# 2. 初回投入後に、分析スナップショットと市場全体候補を生成
docker compose run --rm batch

# 3. Next.js画面を起動または更新して結果を確認
docker compose up -d frontend
```

日々実行するのは2番目の`batch`です。終値と配信データの確定後、余裕を持って平日19時頃に
1日1回実行することを想定しています。通常は未取得日と直近14暦日の再取得だけを行うため、
約1〜5分が目安です。実行しなかった日があっても、次回に未取得期間を追いつきます。
分析ロジックを更新した場合も`batch`を一度実行すると、市場全体の転換段階と条件進捗を
新しいエンジン版で再生成できます。

処理の関係は次のとおりです。

```text
J-QuantsバルクCSV（未調整値）
  -> daily_bars.raw_*（監査・再処理用に保存）
J-Quants REST API（調整済み値）
  -> daily_bars.open/high/low/close/volume（チャート・分析用）
  -> dailyバッチ（流動性上位を分析）
  -> analysis_snapshots（分析結果をPostgreSQLへ保存）
  -> Webの「市場全体」候補に表示
```

初回投入の正常な結果では、`failed`が`0`であり、かつ`rows`が`0`より大きくなります。
`files`は対象ファイル数、`skipped`は全工程が完了済みのファイル数、`raw_rows`は今回保存した
未調整日足数、`rows`は今回保存した調整済み日足数、`refreshed_symbols`は分割・併合を検知して
過去5年を再取得した銘柄数です。`errors`には最初の10件まで、失敗ファイルと具体的な理由が
入ります。次のような結果は、ファイル一覧の取得には成功していますが、調整済み日足を1件も
登録していない異常な状態です。

```json
{
  "files": 69,
  "skipped": 0,
  "raw_rows": 0,
  "rows": 0,
  "refreshed_symbols": 0,
  "failed": 69,
  "errors": [
    {
      "file_key": "対象ファイル",
      "message": "具体的な失敗理由"
    }
  ]
}
```

`status=success`でも`row_count=0`、または`adjusted_status=success`でも
`adjusted_row_count=0`のファイルは完了済みとして扱いません。同じ
`jquants-bulk-bootstrap`を再実行すると未完了工程から再開するため、`bulk_files`を手動で削除
しないでください。CSVの列構成が想定外、有効な日足が0件、調整済み系列に分割比率相当の段差が
残る場合は失敗として記録し、コマンドも終了コード1を返します。

株式分割・併合によりJ-Quantsの過去の調整済み値が遡及変更された場合、日次バッチは
`AdjFactor`を検知した銘柄だけ過去5年を再取得します。これにより、5803の1:6分割や2264の
1:2・1:4分割のような段差を、通常の値動きとしてテクニカル分析へ渡しません。
品質検査では、分割日の未調整価格比率と調整済み価格比率を直接比較します。1:1.1などの小さな
分割を通常の値動きと誤認せず、調整済み値が未調整値と同じ価格基準のままの場合だけ失敗とします。

DBへの登録件数と対象期間は、別のターミナルから確認できます。

```bash
docker compose exec db psql -U tomoshibiyori -d tomoshibiyori -c "
SELECT provider,
       COUNT(*) AS rows,
       COUNT(DISTINCT symbol) AS symbols,
       COUNT(*) FILTER (WHERE is_adjusted) AS adjusted_rows,
       COUNT(*) FILTER (WHERE NOT is_adjusted) AS unadjusted_rows,
       MIN(trade_date) AS first_date,
       MAX(trade_date) AS last_date
FROM daily_bars
GROUP BY provider
ORDER BY provider;"
```

市場全体候補の生成件数は次で確認できます。日足投入直後は`0`であり、正常な日足がある状態で
`batch`を実行した後に増加します。

```bash
docker compose exec db psql -U tomoshibiyori -d tomoshibiyori -c "
SELECT COUNT(*) AS snapshots,
       MIN(as_of_date) AS first_date,
       MAX(as_of_date) AS last_date
FROM analysis_snapshots;"
```

失敗したファイルと原因は次で確認できます。

```bash
docker compose exec db psql -U tomoshibiyori -d tomoshibiyori -c "
SELECT file_key,
       status,
       row_count,
       adjusted_status,
       adjusted_row_count,
       COALESCE(adjusted_error_message, error_message) AS error_message
FROM bulk_files
WHERE status <> 'success'
   OR row_count = 0
   OR adjusted_status <> 'success'
   OR adjusted_row_count = 0
ORDER BY target_date, file_key;"
```

## PostgreSQLと旧SQLiteからの移行

開発環境ではComposeの`db`サービスと名前付きボリューム`postgres-data`を使用します。
接続先は`DATABASE_URL`だけに集約しているため、AWS移行時はAmazon RDS for PostgreSQLの
接続文字列へ変更し、同じ`alembic upgrade head`を適用できます。

旧`data/stock_signal.db`を残している場合は、削除せずに次の一回限りの移行を実行します。

```bash
docker compose run --rm app stock-signal migrate-sqlite \
  --source /app/data/stock_signal.db
```

日足、ウォッチリスト、登録状態、決算予定、同期状態、バッチ履歴をUPSERTします。移行完了を
確認するまではSQLiteファイルを保管してください。移行処理はDB本体とWALをコンテナ内の
一時領域へ複製し、SHMは一時領域で再生成するため、読み取り専用の移行元を変更しません。

調整済み日足対応へ更新した既存環境では、イメージを再ビルドしてマイグレーションを適用後、
同じブートストラップを再実行します。未調整価格から生成された分析結果は移行時に破棄され、
最後の`batch`で調整済み価格から再生成されます。

```bash
docker compose build
docker compose run --rm app stock-signal init-db
docker compose run --rm app stock-signal jquants-bulk-bootstrap
docker compose run --rm batch
docker compose up -d frontend
```

## HTTP API

```text
GET /api/v1/health
GET /api/v1/watchlist
GET /api/v1/watchlists
POST /api/v1/watchlists/{watchlist_name}/items/bulk
DELETE /api/v1/watchlist/{symbol}?provider=jquants
GET /api/v1/instruments?query=トヨタ
GET /api/v1/portfolio/positions
POST /api/v1/portfolio/positions
DELETE /api/v1/portfolio/positions/{symbol}?provider=jquants
POST /api/v1/portfolio/imports/rakuten/preview  （CSV本体、Content-Type: text/csv）
POST /api/v1/portfolio/imports/rakuten/apply    （CSV本体、Content-Type: text/csv）
GET /api/v1/market-candidates?action=buy_candidate&horizon=5
GET /api/v1/watchlist/registrations
POST /api/v1/watchlist/registrations  {"symbol":"7203"}
GET /api/v1/data-plan
GET /api/v1/instruments/{symbol}/daily-bars?range=3m
GET /api/v1/instruments/{symbol}/daily-bars?from=2026-01-01&to=2026-08-14
GET /api/v1/instruments/{symbol}/analysis/latest?horizon=5
GET /api/v1/ai-investment-review/capability
POST /api/v1/instruments/{symbol}/ai-investment-review?horizon=5&provider=jquants
GET /api/v1/instruments/{symbol}/predictions/latest?horizon=5
GET /api/v1/candidates?action=buy_candidate&horizon=5
GET /api/v1/candidates?direction=up&horizon=5
GET /api/v1/reference-signals?direction=up
```

## 品質検査

```bash
docker compose run --rm test
docker compose run --rm test ruff check .
```

`test`サービスは開発DBとは別の一時PostgreSQLを使い、テスト終了後に永続データを残しません。

## 注意事項

チャートパターンは価格帯またはネックラインの突破を確認できた場合だけ判定要因に採用します。
出来高倍率1.5倍などの条件を満たしても、将来の値動きは保証されません。Lightで取得できる
TOPIXと決算予定日は評価し、対象外の業種指数と未契約の適時開示は理由付きで非活性表示します。
下降判定は新規購入を避ける警戒フラグであり、空売り指示ではありません。それでも
過去の形から将来の値動きは保証されません。本アプリケーションが表示する情報は分析目的であり、
投資助言や利益保証ではありません。
自動取引へ進む前に、ウォークフォワード検証、確率校正、取引コスト、リスク制限、
ペーパートレード、注文照合を実装する必要があります。


## Light財務・保有リスク（2026年9月追加）

[財務・保有リスクの仕様](docs/light-research-design.md)を参照してください。
Lightを維持し、ウォッチ・保有銘柄の財務履歴を日次バッチで同期します。
日次処理時間は銘柄数とAPIのレート制限により増えます。
財務だけの初回同期・再同期は次のコマンドで実行できます。

```bash
docker compose run --rm migrate
docker compose run --rm app stock-signal financial-sync
# 指定銘柄だけ同期する場合
docker compose run --rm app stock-signal financial-sync 7203 8058
```

画面の「企業・イベント・保有リスク」から資金設定を登録してください。
資金・許容損失の架空の初期値はありません。未設定時は上限株数を表示しません。
設定変更後の市場一覧は旧候補を保留し、銘柄を選択すると最新条件で再分析します。
財務未同期の市場銘柄は中長期の購入候補に昇格しません。

APIは `GET/PUT /api/v1/portfolio/risk-settings`、銘柄分析の `research` を追加しています。
財務・資金ルールは確率ではありません。EDINET本文や追加の公的マクロAPIは未実装です。

### 購入候補の監査と財務同期

`make update`は市場のテクニカル条件を先に評価し、通過した銘柄のLight財務を自動取得して最終区分を更新します。財務の保留理由は「データ取得待ち」（同期未完了・失敗・4日超未更新）、「算出に必要な項目不足」（取得済みデータに必要な数値がない）、「比較・内容確認が必要」（前年同期の比較不能・ゼロ以下の比較基準・連結範囲変更など）に分けて表示します。複数の理由がある場合は詳細に併記し、一覧では取得待ち、内容確認、項目不足の順に代表理由を表示します。赤字・減益・高PERによる警戒は比較上の問題で上書きせず、リスク保留を優先します。

最新決算のBPSが欠け、過去のBPSがある場合は「比較・内容確認が必要」とします。過去BPSの基準日・株式分割・資本変動を照合するまでは最新PBRに代用しません。前年同期がゼロ以下なら増減率を作らず、黒字転換・赤字幅などの確認を求めます。取得済み項目の欠損は、新しい開示の公開待ちと断定しません。`make update`や`make financials SYMBOLS="7203"`で再取得しても、開示内容・取得可能項目・比較条件が変わらなければ保留は解消しません。これらは限定的な数値による分析情報です。詳細は[財務・保有リスクの仕様](docs/light-research-design.md)を参照してください。

RSIダイバージェンスの条件追加は、`make rsi-study`で保存日足から比較できます。現時点では収益改善を裏付ける結果が得られていないため、購入区分には反映していません。[検証機能の仕様](docs/rsi-divergence-study.md)を参照してください。

強気逆行を起点とする反転監視は、`make rsi-watch-study`で検証できます。監視開始・安値更新による失効・戻り高値超えによる反転確認を分けた研究用実装です。ライブ購入判定には未接続です。[反転監視の仕様](docs/divergence-watch-study.md)を参照してください。

### 三尊候補で待つ条件

中長期の押し目評価では、未完成の三尊候補を検出した場合、反発条件だけで
購入候補にせず「待つ」と表示します。左右各5営業日以上、全体60営業日以内、
肩の価格差3%以内、頭が両肩より2.5%以上高い形を対象とし、局所高値は
後続2営業日で確認します。右肩から20営業日以内の候補を監視します。
三つの山の間の最安値を谷として固定し、営業日数で傾斜を延長した
ネックラインと、直近の谷の安値を区別して理由欄に示します。
終値での下抜け後の回復確認、右肩上抜けによる仮説の弱まり、頭上抜けによる
撤回条件も表示します。一度下抜けた候補や右肩高値を超えた候補は未完成の
待機理由から除外し、完成済み下降パターンの既存評価を優先します。
これは形状に基づく分析情報で、待つことが最適という保証や下落確率ではありません。
利益成長・配当の持続性・価格妥当性は別途確認します。特定銘柄・価格の固定判定はしません。

### 支持帯と反発時の下落確認

日足チャートは抵抗帯に加え、支持帯を緑の点線で表示できます。支持帯は局所安値の
2回以上の接触をATRで集約し、現在の終値で明確に下抜けた帯を除外します。
スイングは60営業日、中長期は120営業日を参照し、現在値に近い最大3帯の中心を示します。
表示終了日より後のデータは使いません。将来の反発を保証する線ではありません。

中長期の押し目評価では、直近3本の安値が連続して切り下がり、直近5本の安値から
0.5 ATR以上低下している場合、陽線反発だけでは購入候補にしません。
安値切り下げ停止後も支持帯維持・反発継続などの既存条件を再確認します。
スイング候補が0件の場合は、表示日・分析件数と主な保留理由を一覧に表示します。
候補件数を増やすために条件を緩めたり、古い日の候補を混ぜたりはしません。

ダブルトップ検出は、直近8個の局所高値の組み合わせを確認します。途中の小さな戻り高値を
挟んでも、既存の期間・高値の近さ・先行上昇・終値でのネックライン割れを満たせば対象です。
途中に両頂点を大きく超える高値がある組み合わせは対象外とし、下抜け前は完成扱いにしません。
