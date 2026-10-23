# Next.jsフロントエンド・認証・実行環境設計

## この文書の目的

本書は、画面とAPIの役割、認証境界、サービス間通信、設定項目を定義します。
公開リポジトリであることと、アプリを複数利用者へ公開できることは別です。認証は許可IDの照合方式であり、利用者別のデータ分離はありません。

## 1. 画面とAPIの役割

`frontend/`のNext.js App Router＋TypeScriptを唯一の画面とし、FastAPIは認証付きJSON APIへ
役割を限定しています。

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
数値ユーザーIDを`AUTH_ALLOWED_GITHUB_ID`と照合します。許可リストに一致するアカウントだけが利用できます。設定はカンマ区切りの複数IDを受け付けます。

メールアドレスやログイン名は変更可能なので、主識別子には使いません。

ローカル開発では、次の条件をすべて満たした場合だけOAuthを省略できます。

```text
NODE_ENV != production
かつ
AUTH_ALLOW_INSECURE_LOCAL=true
```

`production`では`AUTH_ALLOW_INSECURE_LOCAL=true`を設定しても迂回できません。

### 3.2 Next.jsからFastAPIまで

ブラウザーはFastAPIのURLやサービス間トークンを知りません。Next.jsのRoute Handlerが本人セッションを
再確認した後、サーバー環境変数の`INTERNAL_API_TOKEN`を付けてFastAPIへ転送します。

FastAPIは`secrets.compare_digest`でBearerトークンを比較します。CORSやURLの秘匿は認証として扱いません。
APIトークンは32文字以上とし、Next.jsとFastAPIへ同じ値を設定します。

この方式はWebブラウザーからの利用を対象とします。ネイティブアプリ向けの認証は未対応です。

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
| `AUTH_ALLOWED_GITHUB_ID` | Next.js | いいえ | 許可するアカウントの数値ID（カンマ区切り） |
| `AUTH_ALLOW_INSECURE_LOCAL` | Next.js | いいえ | ローカル限定の認証省略フラグ |
| `API_AUTH_REQUIRED` | FastAPI | いいえ | JSON APIのBearer認証を有効化 |

秘密値はサーバー側で保持し、Git、クライアントJavaScript、応答本文へ含めません。
`AUTH_SECRET`と`INTERNAL_API_TOKEN`は異なる値を使用します。
Composeの画面とAPIの公開ポートは、既定でループバックアドレスに束縛します。

## 6. 未解決事項

操作監査、ブラウザーE2E検証、公開環境の構成、複数インスタンス時の実行制御は
[未解決・残課題](open-issues.md#認証と実行環境)を参照してください。
