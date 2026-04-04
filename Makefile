.PHONY: format format-check lint lint-fix typecheck check dev

# -------- Formatting --------
format:
	uv run yapf -i -r src/
	uv run djlint src/ --reformat

format-check:
	uv run yapf -d -r src/
	uv run djlint src/ --check

# -------- Linting --------
lint:
	uv run ruff check .

# -------Lint Fixing-------
lint-fix:
	uv run ruff check . --fix

# -------- Type checking --------
typecheck:
	uv run mypy src/

# -------- Combined --------
check: format-check typecheck lint

# -------- Dev server --------
dev:
	uv run pywrangler dev