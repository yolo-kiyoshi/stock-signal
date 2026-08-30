# Next.jsフロントエンド・認証・実行環境設計

## この文書の目的

この文書は、TOMOSHIBIYORIを将来外部公開できる構成へ段階移行するために、現在のローカル実行環境、
認証境界、秘密情報、サービス間通信、移行順序を定義します。現段階ではVercelやAWSなどへの実際の
デプロイは行いません。

## 1. 現在の到達点

`frontend/`のNext.js App Router＋TypeScriptを唯一の画面とし、FastAPIは認証付きJSON APIへ
役割を限定しています。旧FastAPI＋Jinja画面と静的資産は、主要機能の移植完了後に削除しました。

| URL | 用途 | 備考 |
|---|---|---|
| `http://localhost:3000` | Next.js画面 | 唯一の利用者向け画面 |
| `http://localhost:8001` | 認証付きFastAPI API | ブラウザーから直接使わない |

Next.js画面には、保有銘柄、ウォッチリスト、市場候補の登録・削除・絞り込み、銘柄検索、
スイング・中長期の分析切り替え、任意期間の日足、移動平均、RSI、支持・抵抗帯、投資検討区分、
条件進捗、判定要因、株数計算、AI最終確認を実装しています。

## 2. ローカルのサービス構成

```text
ブラウザー
  |
  | http://localhost:3000
  v
frontend（Next.js）
  |  本人セッションを確認
  |  Authorization: Bearer <INTERNAL_API_TOKEN>
  v
api（FastAPI）
  |
  v
db（PostgreSQL）

batch ── J-Quants API
  |
  v
db
```

`api`はPythonイメージから`API_AUTH_REQUIRED=true`で起動し、`/api/v1/*`へサービス間認証を
必須にします。Jinjaテンプレートを配信するルートはなく、ルートURLは画面として公開しません。

## 3. 認証の二つの境界

### 3.1 利用者からNext.jsまで

外部公開時はAuth.jsのGitHub OAuthを使います。ログイン成功だけでは許可せず、GitHubが返す変更されない
数値ユーザーIDを`AUTH_ALLOWED_GITHUB_ID`と照合します。一致する本人アカウントだけが利用できます。

メールアドレスやログイン名は変更可能なので、主識別子には使いません。GitHub側で多要素認証または
パスキーを有効にしてください。

ローカル開発では、次の条件をすべて満たした場合だけOAuthを省略できます。

```text
NODE_ENV != production
かつ
AUTH_ALLOW_INSECURE_LOCAL=true
```

`production`では`AUTH_ALLOW_INSECURE_LOCAL=true`を設定しても迂回できません。外部公開前には明示的に
`false`へ変更します。

### 3.2 Next.jsからFastAPIまで

ブラウザーはFastAPIのURLやサービス間トークンを知りません。Next.jsのRoute Handlerが本人セッションを
再確認した後、サーバー環境変数の`INTERNAL_API_TOKEN`を付けてFastAPIへ転送します。

FastAPIは`secrets.compare_digest`でBearerトークンを比較します。CORSやURLの秘匿は認証として扱いません。
APIトークンは32文字以上とし、Next.jsとFastAPIへ同じ値を設定します。

この方式はスマートフォンのWebブラウザーからの利用を対象にします。将来ネイティブアプリからAPIを
直接呼ぶ場合は、OIDCのアクセストークンとFastAPI側のJWT検証へ拡張します。

## 4. BFFの責務

Next.jsの`/api/backend/[...path]`はBackend for Frontend（BFF）です。

- 利用者セッションを毎回確認する。
- 変更系リクエストは`Origin`を照合し、同一オリジンだけを許可する。
- 許可された相対パスだけをFastAPIへ転送する。
- `INTERNAL_API_TOKEN`をサーバー側で追加する。
- FastAPIの応答を`no-store`で返す。
- FastAPIの内部接続先やトークンをクライアントJavaScriptへ渡さない。
- API接続エラーで秘密情報や内部スタックトレースを表示しない。

環境変数名に`NEXT_PUBLIC_`を付けるとブラウザーバンドルへ含まれるため、
`FASTAPI_INTERNAL_URL`、`INTERNAL_API_TOKEN`、`AUTH_SECRET`には絶対に付けません。

## 5. 環境変数

| 環境変数 | 配置 | 秘密情報 | 内容 |
|---|---|---|---|
| `FASTAPI_INTERNAL_URL` | Next.js | いいえ | Docker内では`http://api:8000` |
| `INTERNAL_API_TOKEN` | Next.js・FastAPI | はい | 32文字以上のサービス間秘密値 |
| `AUTH_SECRET` | Next.js | はい | Auth.jsのセッション署名・暗号化用 |
| `AUTH_GITHUB_ID` | Next.js | 準秘密 | GitHub OAuth AppのClient ID |
| `AUTH_GITHUB_SECRET` | Next.js | はい | GitHub OAuth AppのClient Secret |
| `AUTH_ALLOWED_GITHUB_ID` | Next.js | いいえ | 許可する本人の数値ID |
| `AUTH_ALLOW_INSECURE_LOCAL` | Next.js | いいえ | ローカル限定の認証省略フラグ |
| `API_AUTH_REQUIRED` | FastAPI | いいえ | JSON APIのBearer認証を有効化 |

秘密値はGitへ登録しません。生成例は次のとおりです。

```bash
openssl rand -base64 48
```

`AUTH_SECRET`と`INTERNAL_API_TOKEN`には異なる生成値を設定してください。
`docker compose config`は`.env`を展開した秘密値まで標準出力へ表示するため、構文確認には
`docker compose config --quiet`を使用します。設定値をログやチャットへ貼り付けないでください。

## 6. ローカル起動

最初はOAuthなしのローカルモードで確認できます。

```dotenv
AUTH_ALLOW_INSECURE_LOCAL=true
INTERNAL_API_TOKEN=32文字以上のローカル用ランダム値
AUTH_SECRET=別の32文字以上のローカル用ランダム値
```

```bash
docker compose up --build frontend
```

`frontend`から`api`、`migrate`、`db`の順に必要なサービスが起動します。ブラウザーで
`http://localhost:3000`を開きます。

Composeの公開ポートは初期値で`127.0.0.1`だけに束縛します。認証省略中の画面を同一LANへ誤って
公開しないためです。スマートフォン実機からLAN経由で確認する場合は、先にGitHub認証を有効化した上で、
一時的なCompose上書き設定で`frontend`だけをLANへ公開します。`api`とPostgreSQLは公開しません。

Composeは高速な反復開発のためDockerfileの`development`ステージを使います。外部公開用と同じ
最小構成イメージが生成できることは、次のコマンドで確認できます。このコマンドはイメージを作るだけで、
外部サービスへ送信しません。

```bash
docker build --target production -t tomoshibiyori-frontend:production ./frontend
```

FastAPIの認証を単独確認する場合は次を使います。実際の値をシェル履歴へ直接書かず、`.env`から
読み込むなどして扱ってください。

```bash
curl -i http://localhost:8001/api/v1/health
# 401 API認証が必要です
```

## 7. GitHub OAuthをローカルで確認する

1. GitHubでOAuth Appを作成する。
2. Homepage URLを`http://localhost:3000`にする。
3. Authorization callback URLを
   `http://localhost:3000/api/auth/callback/github`にする。
4. Client IDとClient Secretを`.env`へ設定する。
5. GitHub APIまたはプロフィール情報から自分の数値ユーザーIDを確認し、
   `AUTH_ALLOWED_GITHUB_ID`へ設定する。
6. `AUTH_ALLOW_INSECURE_LOCAL=false`にする。
7. `frontend`を再作成して、許可アカウントだけが入れることを確認する。

OAuth App作成と外部サービスへの秘密値登録は、デプロイ準備段階で利用者本人が実施します。

## 8. 段階移行

| 段階 | 内容 | 完了条件 |
|---|---|---|
| 1 | Next.js、BFF、サービス間認証、主要な読み取り画面 | 完了 |
| 2 | ウォッチ追加・削除、保有編集、AI最終確認 | 完了 |
| 3 | RSI、支持・抵抗帯、全期間指定などチャート機能 | 完了 |
| 4 | Jinja画面を廃止し、FastAPIをJSON API専用化 | 完了 |
| 5 | 操作監査、ブラウザーE2Eテストの自動化 | 外部公開前の受け入れ試験を通過 |
| 6 | ホスティング先の選定とデプロイ設定 | 利用者が明示的に開始を指示した後 |

## 9. 外部公開前チェックリスト

- `AUTH_ALLOW_INSECURE_LOCAL=false`である。
- `AUTH_SECRET`と`INTERNAL_API_TOKEN`を本番専用値へローテーションした。
- GitHub OAuthのCallback URLが本番URLだけを許可している。
- `AUTH_ALLOWED_GITHUB_ID`が本人の1件だけである。
- FastAPIのAPI認証を無効にできない本番設定になっている。
- PostgreSQLをインターネットへ直接公開していない。
- J-Quants、OpenAI、SlackのキーをNext.jsブラウザーバンドルへ入れていない。
- すべての変更APIで本人セッションを再確認している。
- ログにAuthorizationヘッダー、Cookie、APIキーを残していない。
- 依存関係監査、Next.jsビルド、TypeScript、ESLint、pytest、ruffが通る。
- スマートフォン実機でログイン、チャート、スクロール、ログアウトを確認した。

## 10. 現時点の注意事項

Auth.jsは`next-auth 5.0.0-beta.32`へ固定しています。公式のNext.js統合方式を利用できますが、
外部公開前に安定版の公開状況とセキュリティ更新を再確認し、必要なら版を更新します。
既存FastAPIのAI同時実行制御はプロセス内Lockのため、複数インスタンスへ拡張する前にPostgreSQLの
Advisory Lockまたはジョブテーブルへ置き換えます。

参考資料：

- [Next.js App Router](https://nextjs.org/docs/app)
- [Next.js Authentication](https://nextjs.org/docs/app/guides/authentication)
- [Next.js Backend for Frontend](https://nextjs.org/docs/app/guides/backend-for-frontend)
- [Auth.js](https://authjs.dev/)
