from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

app_metadata = Table(
    "app_metadata",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", Text, nullable=False),
)

instruments = Table(
    "instruments",
    metadata,
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("english_name", Text),
    Column("market", String(100), nullable=False),
    Column("sector_17_code", String(10)),
    Column("sector_17_name", String(100)),
    Column("sector_33_code", String(10)),
    Column("sector_33_name", String(100)),
    Column("instrument_type", String(40), nullable=False, server_default="stock"),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("as_of_date", Date, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("idx_instruments_active_market", instruments.c.is_active, instruments.c.market)
Index("idx_instruments_sector33", instruments.c.sector_33_code)

daily_bars = Table(
    "daily_bars",
    metadata,
    Column("symbol", String(12), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("open", Numeric(20, 6), nullable=False),
    Column("high", Numeric(20, 6), nullable=False),
    Column("low", Numeric(20, 6), nullable=False),
    Column("close", Numeric(20, 6), nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("provider", String(40), nullable=False),
    Column("is_adjusted", Boolean, nullable=False),
    Column("raw_open", Numeric(20, 6)),
    Column("raw_high", Numeric(20, 6)),
    Column("raw_low", Numeric(20, 6)),
    Column("raw_close", Numeric(20, 6)),
    Column("raw_volume", BigInteger),
    Column("adjustment_factor", Numeric(20, 10)),
    Column("retrieved_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("volume >= 0", name="ck_daily_bars_volume_nonnegative"),
    CheckConstraint("low <= high", name="ck_daily_bars_price_range"),
    UniqueConstraint("symbol", "trade_date", "provider", name="uq_daily_bars_identity"),
)
Index(
    "idx_daily_bars_symbol_provider_date",
    daily_bars.c.symbol,
    daily_bars.c.provider,
    daily_bars.c.trade_date.desc(),
)
Index("idx_daily_bars_trade_date", daily_bars.c.trade_date)

watchlists = Table(
    "watchlists",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

watchlist_items = Table(
    "watchlist_items",
    metadata,
    Column(
        "watchlist_id",
        Integer,
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("exchange", String(100), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("sort_order", Integer, nullable=False, server_default="0"),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

portfolios = Table(
    "portfolios",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("base_currency", String(3), nullable=False, server_default="JPY"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

positions = Table(
    "positions",
    metadata,
    Column(
        "portfolio_id",
        Integer,
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("quantity", Numeric(20, 6), nullable=False),
    Column("average_cost", Numeric(20, 6)),
    Column("account_type", String(40), nullable=False, server_default="未設定"),
    Column("memo", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonnegative"),
    CheckConstraint(
        "average_cost IS NULL OR average_cost >= 0",
        name="ck_positions_cost_nonnegative",
    ),
)

model_versions = Table(
    "model_versions",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("horizon_days", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("calibration_method", Text),
    Column("trained_through", Date, nullable=False),
    Column("metrics_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("horizon_days IN (1, 5, 20)", name="ck_model_versions_horizon"),
    CheckConstraint(
        "status IN ('candidate', 'approved', 'retired')",
        name="ck_model_versions_status",
    ),
)

predictions = Table(
    "predictions",
    metadata,
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("model_version_id", String(100), ForeignKey("model_versions.id"), primary_key=True),
    Column("horizon_days", Integer, nullable=False),
    Column("probability_up", Float, nullable=False),
    Column("probability_flat", Float, nullable=False),
    Column("probability_down", Float, nullable=False),
    Column("predicted_class", String(10), nullable=False),
    Column("rank_score", Float, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "idx_predictions_horizon_class_date",
    predictions.c.horizon_days,
    predictions.c.predicted_class,
    predictions.c.as_of_date.desc(),
)

analysis_snapshots = Table(
    "analysis_snapshots",
    metadata,
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("horizon_days", Integer, primary_key=True),
    Column("direction", String(10), nullable=False),
    Column("action", String(30), nullable=False),
    Column("evidence_score", Float, nullable=False),
    Column("analysis_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("horizon_days IN (1, 5, 20)", name="ck_analysis_snapshots_horizon"),
)
Index(
    "idx_analysis_snapshots_candidates",
    analysis_snapshots.c.horizon_days,
    analysis_snapshots.c.action,
    analysis_snapshots.c.as_of_date.desc(),
    analysis_snapshots.c.evidence_score.desc(),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", String(50), primary_key=True),
    Column("pipeline_name", String(100), nullable=False),
    Column("status", String(30), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("summary_json", JSONB, nullable=False, server_default="{}"),
)

pipeline_run_items = Table(
    "pipeline_run_items",
    metadata,
    Column(
        "run_id",
        String(50),
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("symbol", String(20), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("received_count", Integer, nullable=False, server_default="0"),
    Column("upserted_count", Integer, nullable=False, server_default="0"),
    Column("first_date", Date),
    Column("last_date", Date),
    Column("error_message", Text),
    Column("analysis_json", JSONB),
)

earnings_calendar = Table(
    "earnings_calendar",
    metadata,
    Column("symbol", String(12), primary_key=True),
    Column("scheduled_date", Date, primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("company_name", Text, nullable=False),
    Column("fiscal_year", String(30)),
    Column("fiscal_quarter", String(30)),
    Column("retrieved_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

data_sync_status = Table(
    "data_sync_status",
    metadata,
    Column("dataset", String(200), primary_key=True),
    Column("status", String(20), nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("error_message", Text),
)

watchlist_registrations = Table(
    "watchlist_registrations",
    metadata,
    Column("symbol", String(12), primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("watchlist_name", String(100), primary_key=True),
    Column("status", String(20), nullable=False),
    Column("display_name", Text),
    Column("error_message", Text),
    Column("requested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

bulk_files = Table(
    "bulk_files",
    metadata,
    Column("file_key", Text, primary_key=True),
    Column("endpoint", Text, nullable=False),
    Column("target_date", Date, nullable=False),
    Column("status", String(20), nullable=False),
    Column("row_count", Integer, nullable=False, server_default="0"),
    Column("checksum", String(128)),
    Column("error_message", Text),
    Column("adjusted_status", String(20), nullable=False, server_default="pending"),
    Column("adjusted_row_count", Integer, nullable=False, server_default="0"),
    Column("adjusted_error_message", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("idx_bulk_files_endpoint_date", bulk_files.c.endpoint, bulk_files.c.target_date)

market_observations = Table(
    "market_observations",
    metadata,
    Column("indicator_key", String(40), primary_key=True),
    Column("observation_date", Date, primary_key=True),
    Column("provider", String(40), primary_key=True),
    Column("label", String(100), nullable=False),
    Column("value", Numeric(24, 8), nullable=False),
    Column("previous_value", Numeric(24, 8)),
    Column("unit", String(30), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "idx_market_observations_latest",
    market_observations.c.indicator_key,
    market_observations.c.observation_date.desc(),
)

market_regime_snapshots = Table(
    "market_regime_snapshots",
    metadata,
    Column("decision_date", Date, primary_key=True),
    Column("decision_at", DateTime(timezone=True), nullable=False),
    Column("regime", String(20), nullable=False),
    Column("risk_score", Float, nullable=False),
    Column("coverage_ratio", Float, nullable=False),
    Column("components_json", JSONB, nullable=False),
    Column("reasons_json", JSONB, nullable=False),
    Column("cautions_json", JSONB, nullable=False),
    Column("observations_json", JSONB, nullable=False),
    Column("engine_version", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "regime IN ('normal', 'caution', 'severe', 'unavailable')",
        name="ck_market_regime_snapshots_regime",
    ),
)
Index(
    "idx_market_regime_snapshots_latest",
    market_regime_snapshots.c.decision_date.desc(),
)
