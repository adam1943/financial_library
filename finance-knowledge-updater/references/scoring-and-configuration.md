# Scoring And Configuration

## Config Shape

`knowledge_base/config.json` is created automatically when missing. Important fields:

- `knowledge_base_dir`: output directory, usually `knowledge_base`.
- `lookback_days`: recent window for scoring public sentiment.
- `top_n`: number of candidates shown in reports.
- `news_sources`: RSS/Atom sources with `name`, `type`, `url`, and optional `enabled`.
- `watchlist`: assets with `symbol`, `name`, `asset_type`, `market`, and `provider`.
- `keyword_sets`: positive, risk, and theme keywords.
- `method_input_csv`: path to local trading-method CSV relative to the output directory.

## Provider Notes

`eastmoney_stock`:

- Works best for China exchange-traded instruments.
- Accepts `sh600519`, `sz000001`, `sh510300`, `600519`, or explicit `secid`.
- Uses latest quote fields only; it is not a full historical data source.

`fundgz`:

- Accepts China mutual-fund codes such as `161725`.
- Returns estimated NAV and estimated change when available.

`stooq`:

- Accepts symbols such as `aapl.us`, `msft.us`, `spy.us`.
- Useful for simple global/US quote snapshots.

## Scoring Heuristic

The score is a prioritization heuristic:

```text
base 50
+ sentiment component from recent matched public items
+ short-term momentum component from latest quote change
+ trading-method theme-fit bonus
- risk keyword penalty
- sharp negative quote penalty
```

Scores are clipped to `0..100`. Use them to decide what to research next. Do not present them as return forecasts.

## Safer Source Policy

Prefer public RSS/Atom, official data APIs, exchange disclosures, and user-provided local files. Avoid scraping logged-in forums, paid communities, chat rooms, or personal accounts unless the user explicitly confirms permission and provides a compliant integration route.

## Suggested Review Loop

After each update:

1. Open `latest.md`.
2. Check source failures first.
3. Review hot themes and risk flags.
4. Inspect `candidates.csv` for score, mentions, momentum, and rationale.
5. Add or remove watchlist assets in `config.json`.
6. Add concrete trading methods to `input/trading_methods.csv`.
