# macOS標準のmakeでも利用できる、ローカル運用の入口。
.DEFAULT_GOAL := help
SHELL := /bin/sh
# DBの初期化・ビルド・検査をmake -jでも順序どおり実行する。
.NOTPARALLEL:

COMPOSE = docker compose
SYMBOLS ?=
SERVICE ?= api
export SYMBOLS SERVICE

.PHONY: help setup start stop restart status logs update financials preopen bootstrap \
        build build-python build-frontend migrate test lint frontend-check check doctor validate-symbols rsi-study rsi-watch-study

help:
	@printf '%s\n' \
	  'TOMOSHIBIYORI — makeコマンド一覧' \
	  '' \
	  '毎日の操作' \
	  '  make start          アプリをバックグラウンド起動（コード・設定変更も反映）' \
	  '  make update         日足・財務・市場候補を更新（完了まで待機）' \
	  '  make stop           画面・API・DBを停止（保存データは維持）' \
	  '  make status         コンテナの起動・正常性を確認' \
	  '  make logs           APIログを追跡（Ctrl+Cでログ表示だけ終了）' \
	  '  make restart        画面・APIを再起動（コード再読込。設定変更はstart）' \
	  '' \
	  '必要なデータだけ更新' \
	  '  make financials     ウォッチ・保有銘柄の財務だけ更新' \
	  '  make financials SYMBOLS="7203 8058"   指定銘柄の財務だけ更新' \
	  '  make preopen        寄り付き前の市場環境を更新（Alpha Vantageを使用）' \
	  '  make bootstrap      全市場の日足履歴を初回投入（長時間・多数のAPI呼出し）' \
	  '' \
	  '初期設定・開発' \
	  '  make setup          .envを未作成の場合だけ作成。続けて設定を編集する' \
	  '  make build          Python・画面のイメージと画面の依存関係を更新' \
	  '  make migrate        DBスキーマだけ更新' \
	  '  make check          pytest・ruff・画面の型検査・ESLintを順に実行' \
	  '  make rsi-study      保存日足でRSI逆行の比較検証（売買区分は変更しない）' \
	  '  make rsi-watch-study RSI逆行後の反転監視を比較検証' \
	  '  make test           pytestを分離テストDBで実行' \
	  '  make lint           ruff check .を実行' \
	  '  make frontend-check 画面の型検査・ESLintを実行' \
	  '' \
	  '画面: http://localhost:3000' \
	  '画面ログ: make logs SERVICE=frontend（api / frontend / dbを指定可能）' \
	  '起動では市場データを取得しません。日々の更新はmake updateを実行してください。'

setup:
	@if [ -e .env ]; then \
	  printf '%s\n' '.envは作成済みです。既存設定を維持しました。'; \
	else \
	  (umask 077; set -C; cat .env.example > .env) || exit 1; \
	  printf '%s\n' '.envを作成しました。取得元・APIキーを編集してください（Light契約はJQUANTS_PLAN=light）。'; \
	fi
	@printf '%s\n' '設定後: make start → make update → http://localhost:3000'

doctor:
	@command -v docker >/dev/null 2>&1 || { printf '%s\n' 'Dockerが見つかりません。Docker Desktopをインストールしてください。' >&2; exit 1; }
	@$(COMPOSE) version >/dev/null 2>&1 || { printf '%s\n' 'Docker Composeが利用できません。Docker Desktopの設定を確認してください。' >&2; exit 1; }
	@docker info >/dev/null 2>&1 || { printf '%s\n' 'Dockerへ接続できません。Docker Desktopの起動とアクセス権を確認してください。' >&2; exit 1; }

build-python: doctor
	@$(COMPOSE) build app

build-frontend: doctor
	@$(COMPOSE) build frontend
	@$(COMPOSE) run --rm --no-deps frontend pnpm install --frozen-lockfile

build: build-python build-frontend

start: build
	@$(COMPOSE) up -d --force-recreate api frontend
	@printf '%s\n' '起動しました: http://localhost:3000（初回の画面コンパイルには少し時間がかかります）' 'データ更新: make update ／ 状態確認: make status'

stop: doctor
	@$(COMPOSE) stop frontend api db
	@printf '%s\n' '画面・API・DBを停止しました。保存データは維持しています。再開: make start'

restart: doctor
	@$(COMPOSE) restart api frontend
	@printf '%s\n' '画面・APIを再起動しました。停止中のDBや設定変更を含む起動にはmake startを使ってください。'

status: doctor
	@$(COMPOSE) ps --all

logs: doctor
	@case "$$SERVICE" in api|frontend|db) ;; *) printf '%s\n' 'SERVICEはapi、frontend、dbから指定してください。' >&2; exit 2;; esac; \
	$(COMPOSE) logs --follow --tail=100 "$$SERVICE"

update: build-python
	@printf '%s\n' '日足・財務・市場候補を更新します。API制限と取得範囲により時間がかかります。'
	@$(COMPOSE) run --rm batch
	@printf '%s\n' 'データ更新が完了しました。開いている画面を再読み込みしてください。'

validate-symbols:
	@set -f; \
	for symbol in $$SYMBOLS; do \
	  case "$$symbol" in *[!0-9A-Z]*|'') printf '%s\n' 'SYMBOLSは4文字の半角英大文字・数字を空白で区切って指定してください。' >&2; exit 2;; esac; \
	  [ "$${#symbol}" -eq 4 ] || { printf '%s\n' '証券コードは4文字で指定してください。例: make financials SYMBOLS="7203 186A"' >&2; exit 2; }; \
	done

financials: validate-symbols build-python
	@set -f; $(COMPOSE) run --rm app stock-signal financial-sync $$SYMBOLS

preopen: build-python
	@$(COMPOSE) run --rm preopen

bootstrap: build-python
	@printf '%s\n' 'Lightで取得可能な全市場の日足履歴を投入します。中断後は同じコマンドで再開できます。'
	@$(COMPOSE) run --rm app stock-signal jquants-bulk-bootstrap
	@printf '%s\n' '履歴投入が完了しました。続けてmake updateで分析候補を生成してください。'

migrate: build-python
	@$(COMPOSE) run --rm migrate

test: build-python
	@$(COMPOSE) run --rm test pytest

lint: build-python
	@$(COMPOSE) run --rm --no-deps test ruff check .

frontend-check: build-frontend
	@$(COMPOSE) run --rm --no-deps frontend sh -c 'pnpm typecheck && pnpm lint'

check: test lint frontend-check
	@printf '%s\n' 'すべての品質検査に成功しました。'

# 検証スクリプトは標準入力で渡すため、実行イメージへの個別コピーは不要。
rsi-study: build-python
	@printf '%s\n' '保存済み日足で比較します。数分かかります。購入区分と市場データは更新しません。'
	@$(COMPOSE) run --rm -T app python < scripts/evaluate_rsi_divergence.py
	@printf '%s\n' '検証結果: reports/rsi-divergence-study.json（探索的検証。収益改善の保証ではありません）'

rsi-watch-study: build-python
	@printf '%s\n' '100銘柄の保存日足で反転監視を検証します。購入区分は変更しません。'
	@$(COMPOSE) run --rm -T app python < scripts/evaluate_divergence_watch.py
	@printf '%s\n' '検証結果: reports/divergence-watch-study.json'
