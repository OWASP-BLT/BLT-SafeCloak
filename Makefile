.PHONY: dev deploy format format-check typecheck check setup clean

dev:
	pywrangler dev

deploy:
	pywrangler deploy

format:
	yapf -i -r src/

format-check:
	yapf -d -r src/

typecheck:
	mypy src/

check: format-check typecheck

setup:
	pip install -r requirements-dev.txt

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
