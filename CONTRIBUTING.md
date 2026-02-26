# Contributing to PolyBot

Thanks for your interest in contributing to PolyBot! This document covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Node.js 20+
- Docker & Docker Compose (optional, for full-stack testing)

### Getting Started

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/polybot.git
cd polybot

# Install Python dependencies
uv sync --all-extras

# Install frontend dependencies
cd frontend && npm install && cd ..

# Configure environment
cp .env.example .env
# Generate a secret key and paste it into .env as API_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Or use the Makefile shortcut:

```bash
make install
```

## Running Locally

### Backend (Bot + API)

```bash
# Start in paper trading mode (default)
make dev

# Or manually:
uv run uvicorn api.main:app --reload
```

The API will be at `http://localhost:8000`. The bot starts in paper trading mode by default -- no real funds are used.

### Frontend (Dashboard)

```bash
# In a separate terminal
make frontend

# Or manually:
cd frontend && npm run dev
```

The dashboard will be at `http://localhost:5173`.

### Full Stack (Docker)

```bash
make docker-dev
```

This builds and runs the full stack (bot + frontend + nginx) locally.

## Running Tests

```bash
# Run all tests
make test

# Or manually:
uv run pytest tests/ -v --tb=short

# Run with coverage
uv run pytest tests/ --cov=bot --cov=api --cov-report=term-missing
```

All 2257+ tests must pass before submitting a PR.

## Linting

```bash
# Check for lint errors
make lint

# Auto-format
make format

# Or manually:
uv run ruff check bot/ api/
uv run ruff format bot/ api/
```

## Adding a New Strategy

Strategies live in `bot/agent/strategies/`. To add a new one:

1. **Create a new file** in `bot/agent/strategies/your_strategy.py`

2. **Extend `BaseStrategy`** and implement the required methods:

```python
from bot.agent.strategies.base import BaseStrategy, Signal

class YourStrategy(BaseStrategy):
    name = "your_strategy"

    async def scan(self, markets: list, portfolio, research_results: dict) -> list[Signal]:
        """Scan markets and return trading signals."""
        signals = []
        for market in markets:
            # Your signal detection logic here
            edge = self._calculate_edge(market)
            if edge > self.min_edge:
                signals.append(Signal(
                    market_id=market["condition_id"],
                    question=market["question"],
                    strategy=self.name,
                    side="BUY",
                    token_id=market["tokens"][0]["token_id"],
                    price=market["tokens"][0]["price"],
                    estimated_prob=market["tokens"][0]["price"] + edge,
                    edge=edge,
                    confidence=0.7,
                ))
        return signals

    async def should_exit(self, position, market_data) -> bool:
        """Return True if the position should be closed."""
        # Your exit logic here
        return False
```

3. **Register the strategy** in `bot/agent/engine.py` by adding it to the strategy list.

4. **Write tests** in `tests/test_your_strategy.py`. Aim for 80%+ coverage. Write tests first (TDD).

5. **Update documentation** if the strategy introduces new concepts.

## Pull Request Process

1. **Fork the repo** and create a feature branch from `main`
2. **Write tests first** -- we follow TDD (Red -> Green -> Refactor)
3. **Ensure all tests pass**: `make test`
4. **Ensure lint passes**: `make lint`
5. **Keep commits focused** -- one logical change per commit
6. **Use conventional commit messages**: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `perf:`, `ci:`
7. **Open a PR** against `main` with a clear description of what and why

### PR Checklist

- [ ] Tests pass (`uv run pytest tests/ -v`)
- [ ] Lint passes (`uv run ruff check bot/ api/`)
- [ ] Frontend builds (`cd frontend && npx vite build`)
- [ ] New code has tests (80%+ coverage for new files)
- [ ] No hardcoded secrets or personal data
- [ ] Commit messages follow conventional format

## Code Style

### Immutability

Always create new objects instead of mutating existing ones. This prevents hidden side effects.

```python
# Good
new_config = config.model_copy(update={"max_positions": 10})

# Bad
config.max_positions = 10
```

### Small Files, Small Functions

- Files: 200-400 lines typical, 800 max
- Functions: under 50 lines
- No deep nesting (max 4 levels)

### Error Handling

- Handle errors explicitly at every level
- Never silently swallow exceptions
- Provide clear error messages
- Validate all inputs at system boundaries

### Constants Over Magic Numbers

```python
# Good
MIN_EDGE_PCT = 0.02
if edge > MIN_EDGE_PCT:

# Bad
if edge > 0.02:
```

## Project Structure

```
bot/                  # Trading bot (Python asyncio)
  agent/              # Engine, portfolio, risk, learner
    strategies/       # Trading strategies (extend BaseStrategy)
  polymarket/         # Polymarket API clients
  research/           # News, sentiment, LLM analysis
  data/               # Database, models, repositories
  utils/              # Math, logging, notifications
api/                  # FastAPI REST API + WebSocket
  routers/            # API route handlers
frontend/             # React 18 + TypeScript dashboard
tests/                # 2257+ pytest tests
deploy/               # Docker, nginx, server setup
```

## Questions?

Open an issue or start a discussion. We're happy to help!
