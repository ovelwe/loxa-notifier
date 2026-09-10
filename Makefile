.PHONY: help install test check dry seed preview export discover docker-run clean

help:
	@echo "make install    — поставить зависимости"
	@echo "make test       — прогнать тесты"
	@echo "make dry        — проверить сайт, ничего не отправляя"
	@echo "make check      — реальная отправка в Telegram"
	@echo "make seed       — запомнить текущий контент (не отправлять его)"
	@echo "make preview    — посмотреть, как выглядит сообщение"
	@echo "make export     — выгрузить весь контент сайта в Markdown"
	@echo "make docker-run — запустить в Docker (разовая проверка)"

install:
	python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

test:
	python3 -m unittest discover -s tests -v

dry:
	python3 bot.py check --dry-run --verbose

check:
	python3 bot.py check

seed:
	python3 bot.py seed

preview:
	python3 bot.py preview

export:
	python3 bot.py export

discover:
	python3 bot.py discover

docker-run:
	docker compose run --rm notifier

clean:
	rm -rf __pycache__ src/__pycache__ tests/__pycache__ export
