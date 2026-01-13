### 🥇 The winner in [Tinkoff invest robot contest](https://github.com/Tinkoff/invest-robot-contest) 01.06.2022

# Semantix AI Trading

This is a bot for trading on Tinkoff broker.
It uses [Tinkoff investments API](https://github.com/Tinkoff/investAPI)

App name is `SemantixAITrading`

## How to run
To run the bot, you need to have a Tinkoff account.
- Generate token for your account at [the settings](https://www.tinkoff.ru/invest/settings/)
- Create a file `.env` with required env variables. You can find an example in `.env.example`
- Create a file `instuments_config.json` with configurations. You can find an example in `instruments_config.json.example`
- [Optional] Create virtual environment and activate it:
  ```bash
  pip install virtualenv
  virtualenv --python=python3.9 venv
  source venv/bin/activate
  ```
- Install dependencies with
  ```bash
  pip install -r requirements.txt
  ```
- Run the bot with 
  ```bash 
  make start
  ```

## Kubernetes (k8s)

### Helm (recommended)

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set secrets.TOKEN="your-token" \
  --set image.repository="ghcr.io/YOUR_ORG/tinkoff-trading-bot"
```

See `helm/tinkoff-trading-bot/README.md` for details.

### Kustomize (alternative)

Manifests + Dockerfile are included. See `k8s/README.md`.

## .env file content
- `TOKEN`: Your Tinkoff token. You can generate it in [the settings](https://www.tinkoff.ru/invest/settings/)
Can be a token for sandbox or for real account.
- `ACCOUNT_ID`: Your Tinkoff account id. You can get it using [get accounts tool](#get-accounts-tool). If not specified, the first account  used.
- `SANDBOX`: Set to `false` if you want to use real account. Default is `true`.
  - Safety latch: if `SANDBOX=false`, you **must** also set `I_KNOW_WHAT_I_AM_DOING=true` or the bot will refuse to start.

## instruments_config.json file content
#### instruments
List of instruments you want to trade along with their settings.
Each list element is a dictionary with the following keys:
- `figi`: Tinkoff instrument id
- `strategy`: The strategy configuration
  - `name`: The name of the strategy to use
  - `parameters`: Parameters of the strategy. More details can be found in the documentation of the strategy

#### Interval strategy parameters
- `interval_size`: The percent of the prices to include into interval
- `days_back_to_consider`: The number of days back to consider in interval calculation
- `check_interval`: The interval in seconds to check for a new prices and for interval recalculation
- `stop_loss_percent`: The percent from the price to trigger a stop loss
- `quantity_limit`: The maximum quantity of the instrument to have in the portfolio

## Strategies
### Interval strategy
Main strategy logic is to buy at the lowest price and sell at the highest price of the
calculated interval.

Interval is calculated by taking `interval_size` percents of the last prices
for the last `days_back_to_consider` days. By default, it's set to 80 percents which means
that the interval is from 10th to 90th percentile.

### intraday_vwap_momentum (FX breakout mode)
The same strategy name supports an additional **breakout mode** designed for intraday futures (e.g. USD/RUB).
Enable it by setting `breakout_lookback > 0` and using `timeframe: "5min"`.

- **Trend filter (1h)**: allow long only when higher-timeframe trend is bullish, short only when bearish
  - Use `trend_timeframe: "1h"` and `trend_ema_fast` / `trend_ema_slow`
  - Aliases from the spec are supported: `ema_fast_1h` / `ema_slow_1h`
- **Entry (breakout_lookback > 0)**:
  - long if `close > VWAP` AND `close > donchian_high(prev N bars)`
  - short if `close < VWAP` AND `close < donchian_low(prev N bars)`
  - volume filter via `volume_window` + `min_volume_ratio`
- **Risk / exits**:
  - ATR stops via `atr_sl_mult` (alias: `atr_stop_mult`)
  - ATR take-profit via `atr_tp_mult`
  - ATR trailing via `atr_trail_mult` (in breakout mode trailing activates after profit reaches `atr_trail_mult * ATR`)
  - exit before session end via `exit_before_session_end_minutes` (alias: `exit_before_close_minutes`)

#### Risk limits (config)
Per-instrument risk overrides support both money and percent limits:
- `max_daily_loss_rub` / `max_weekly_loss_rub`
- `max_daily_loss_pct` / `max_weekly_loss_pct` (e.g. `2.0` means 2%)

## Get accounts tool
This is the tool to get your Tinkoff accounts. Useful when you don't know your account id.
To run use this command:
```bash
make get_accounts
```

## Backtest
In `test/strategies/interval/backtest/conftest.py` you can find the test configuration.
Set up `figi`, `comission`, strategy config object, and `from_date` offset.
To run backtest use this command:
```bash
make backtest
```
The result is saved in `test/strategies/interval/backtest/test_on_historical_data.txt`

## Stats displaying
Use this command to display stats:
```bash
make display_stats
```
It will display the list of executed trades
