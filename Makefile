PYTHON ?= python

.PHONY: install run-api run-bot test compile clean

install:
	$(PYTHON) -m pip install -e .[dev]

run-api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-bot:
	$(PYTHON) -m bot.telegram_bot

test:
	pytest -q

compile:
	$(PYTHON) -m compileall app bot tests

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
