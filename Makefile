.PHONY: test install-dev lint clean

test:
	python -m pytest tests/ -v

install-dev:
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]"; \
	fi

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check hr/ tests/; \
	else \
		echo "ruff not installed, skipping lint"; \
	fi

clean:
	rm -rf __pycache__ hr/__pycache__ tests/__pycache__
	rm -rf .pytest_cache
	rm -rf *.egg-info dist build
