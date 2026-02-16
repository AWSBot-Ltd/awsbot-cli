SOURCE_DIR=awsbot_cli
TESTS_PATH=tests

# Default to running unit tests
MARK ?=

.PHONY: test unit-test function-test test-all help
.PHONY: install pylint black flake8 ruff checkin pre-commit

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Project Installation ---
install: ## Install project dependencies
	poetry install

# --- Linting & Formatting Targets (Matching linting.yml) ---
pylint: ## Lint code with pylint
	@echo "Linting code with pylint..."
	poetry run pylint --rcfile=.pylintrc $(SOURCE_DIR)/

black-check: ## Reformat code with black
	@echo "Reformatting code with black..."
	poetry run black --check .

flake8-check: ## Lint code with flake8
	@echo "Linting code with flake8..."
	poetry run flake8 $(SOURCE_DIR)/

ruff-check: ## Lint code with ruff
	@echo "Linting code with ruff..."
	poetry run ruff check .

isort-check: ## Lint code with isort
	@echo "Linting code with ruff..."
	poetry run isort check .

# --- Tests ---
test: ## Runs tests based on MARK.
	@echo "Running tests with filter: $(MARK)"
	@PYTHONPATH=. poetry run pytest \
		$(MARK) \
		-vv \
		-s \
		--cov=$(SOURCE_DIR) \
		$(TESTS_PATH) \
		--cov-report=term \
		--cov-report=html

unit-test: ## Alias for isolated unit tests
	@$(MAKE) test MARK="-m unit"

function-test: ## Alias for functional workflow tests
	@$(MAKE) test MARK="-m functional"

# --- Git / Utils ---
checkin: ## Git commit and push, allows for a dynamic comment
	@echo "Checking in changes..."
	@git status
	$(eval COMMENT := $(shell bash -c 'read -e -p "Comment: " var; echo $$var'))
	@git add --all; \
	 git commit --no-verify -m "$(COMMENT)"; \
	 git push

# Install pre-commit hooks into your .git/ directory
install-hooks:
	poetry run pre-commit install

# Run all hooks manually on all files
pre-commit:
	poetry run pre-commit run --all-files

test-pip-install:
	@echo "Installing from testpypi..."
	pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ awsbot-cli