# Claude3 Video Analyzer Development Guidelines

## Commands
- Run: `python main.py`
- Install dependencies: `pip install -r requirements.txt`
- Copy example env: `cp .env.example .env`
- Format code: `pip install black && black .`
- Type checking: `pip install mypy && mypy .`

## Code Style
- Imports: standard library first, then third-party, then local
- Formatting: 4 space indentation, 88 character line length
- Types: Use type hints for all function parameters and return values
- Error handling: Use try/except blocks with specific exceptions
- Naming: snake_case for variables/functions, PascalCase for classes
- Comments: Docstrings for all functions and classes ("""triple quotes""")
- Language: Write user-facing comments in Japanese, code comments in English
- Variable names: Descriptive, avoid abbreviations when possible