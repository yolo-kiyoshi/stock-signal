from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta


class ConfigurationError(ValueError):
    """環境変数の設定値が不正な場合に送出する。"""


INSECURE_LOCAL_API_TOKEN = "change-this-local-token-before-deploying"


def subtract_years(reference: date, years: int) -> date:
    """月日を維持して指定年数だけ遡る。うるう日は2月28日へ丸める。"""
    try:
        return reference.replace(year=reference.year - years)
    except ValueError:
        return reference.replace(year=reference.year - years, day=28)


def plan_history_start(reference: date, years: int) -> date:
    """APIの契約境界を超えないよう、指定年数前の翌日を返す。"""
    return subtract_years(reference, years) + timedelta(days=1)


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+psycopg://tomoshibiyori:tomoshibiyori@db:5432/tomoshibiyori"
    )
    market_data_provider: str = "alpha_vantage"
    notification_dry_run: bool = True
    alpha_vantage_api_key: str | None = None
    jquants_api_key: str | None = None
    jquants_plan: str = "light"
    slack_webhook_url: str | None = None
    market_screening_limit: int = 500
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    openai_max_output_tokens: int = 6_000
    api_auth_required: bool = False
    internal_api_token: str | None = None

    @property
    def jquants_rate_limit_per_minute(self) -> int:
        """契約プランの公式APIコール上限を返す。"""
        return {"free": 5, "light": 60, "standard": 120, "premium": 500}[
            self.jquants_plan
        ]

    @property
    def jquants_request_interval_seconds(self) -> float:
        """上限をわずかに下回る安全なリクエスト間隔を返す。"""
        return 60 / self.jquants_rate_limit_per_minute + 0.05

    @property
    def jquants_history_years(self) -> int:
        return {"free": 2, "light": 5, "standard": 10, "premium": 20}[
            self.jquants_plan
        ]

    @property
    def jquants_data_delay_days(self) -> int:
        return 85 if self.jquants_plan == "free" else 0

    @classmethod
    def from_env(cls) -> Settings:
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL is invalid")

        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://tomoshibiyori:tomoshibiyori@db:5432/tomoshibiyori",
        ).strip()
        if not database_url.startswith("postgresql+psycopg://"):
            raise ConfigurationError(
                "DATABASE_URLはpostgresql+psycopg://形式で指定してください"
            )

        jquants_plan = os.getenv("JQUANTS_PLAN", "light").strip().lower()
        if jquants_plan not in {"free", "light", "standard", "premium"}:
            raise ConfigurationError("JQUANTS_PLAN is invalid")
        try:
            market_screening_limit = int(os.getenv("MARKET_SCREENING_LIMIT", "500"))
        except ValueError as error:
            raise ConfigurationError("MARKET_SCREENING_LIMIT must be an integer") from error
        if not 1 <= market_screening_limit <= 2000:
            raise ConfigurationError("MARKET_SCREENING_LIMIT must be between 1 and 2000")
        openai_model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip()
        if not openai_model or len(openai_model) > 100:
            raise ConfigurationError("OPENAI_MODEL is invalid")
        try:
            openai_max_output_tokens = int(
                os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "6000")
            )
        except ValueError as error:
            raise ConfigurationError(
                "OPENAI_MAX_OUTPUT_TOKENS must be an integer"
            ) from error
        if not 2_000 <= openai_max_output_tokens <= 20_000:
            raise ConfigurationError(
                "OPENAI_MAX_OUTPUT_TOKENS must be between 2000 and 20000"
            )
        api_auth_required = _read_bool("API_AUTH_REQUIRED", False)
        internal_api_token = os.getenv("INTERNAL_API_TOKEN") or None
        if api_auth_required and (
            internal_api_token is None or len(internal_api_token) < 32
        ):
            raise ConfigurationError(
                "API認証を有効にする場合、INTERNAL_API_TOKENは"
                "32文字以上で設定してください"
            )
        if (
            app_env == "production"
            and api_auth_required
            and internal_api_token == INSECURE_LOCAL_API_TOKEN
        ):
            raise ConfigurationError(
                "productionではINTERNAL_API_TOKENのローカル初期値を使用できません"
            )

        return cls(
            app_env=app_env,
            log_level=log_level,
            database_url=database_url,
            market_data_provider=os.getenv(
                "MARKET_DATA_PROVIDER", "alpha_vantage"
            ).strip(),
            notification_dry_run=_read_bool("NOTIFICATION_DRY_RUN", True),
            alpha_vantage_api_key=os.getenv("ALPHA_VANTAGE_API_KEY") or None,
            jquants_api_key=os.getenv("JQUANTS_API_KEY") or None,
            jquants_plan=jquants_plan,
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
            market_screening_limit=market_screening_limit,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=openai_model,
            openai_max_output_tokens=openai_max_output_tokens,
            api_auth_required=api_auth_required,
            internal_api_token=internal_api_token,
        )

    def safe_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["alpha_vantage_api_key"] = "configured" if self.alpha_vantage_api_key else "unset"
        values["jquants_api_key"] = "configured" if self.jquants_api_key else "unset"
        values["slack_webhook_url"] = "configured" if self.slack_webhook_url else "unset"
        values["openai_api_key"] = "configured" if self.openai_api_key else "unset"
        values["internal_api_token"] = (
            "configured" if self.internal_api_token else "unset"
        )
        return values
