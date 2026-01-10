start:
	PYTHONPATH=./ python app/main.py

backtest:
	PYTHONPATH=./ python tools/backtest.py

display_stats:
	PYTHONPATH=./ python tools/display_stats.py

get_accounts:
	PYTHONPATH=./ python tools/get_accounts.py