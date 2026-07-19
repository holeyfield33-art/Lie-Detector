# The Lie Detector - developer workflow.
#
# `make demo` runs the full pipeline end-to-end against the bundled toy repo.
# It requires a working Docker daemon and an LLM credential:
#   - Anthropic: ANTHROPIC_API_KEY or a stored `ant auth login` profile
#   - OpenAI-compatible (Featherless, etc.): OPENAI_API_KEY or FEATHERLESS_API_KEY
#     plus OPENAI_BASE_URL (e.g. https://api.featherless.ai/v1)
# Use `--provider openai` to switch backends.

.PHONY: install lint type test test-docker demo doctor check clean

install:
	pip install -e ".[dev,openai]"

lint:
	ruff check liedetector tests

type:
	mypy liedetector

test:
	pytest -q -m "not docker and not llm"

test-docker:
	pytest -q -m docker

check: lint type test

demo:
	liedetector demo

doctor:
	liedetector doctor

clean:
	rm -rf receipts/* reports/* harnesses/* build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
