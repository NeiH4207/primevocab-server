# Contributing to AIFOREN API

We love your input! We want to make contributing to AIFOREN API as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features
- Becoming a maintainer

## Development Process

We use GitHub to host code, to track issues and feature requests, as well as accept pull requests.

### Code Changes Happen Through Pull Requests

Pull requests are the best way to propose changes to the codebase. We actively welcome your pull requests:

1. Fork the repo and create your branch from `main`.
2. If you've added code that should be tested, add tests.
3. If you've changed APIs, update the documentation.
4. Ensure the test suite passes.
5. Make sure your code lints.
6. Issue that pull request!

## Development Setup

### Prerequisites

- Python 3.12+
- MySQL 8.0+
- MongoDB 4.4+
- Git

### Local Development

1. **Clone your fork**
   ```bash
   git clone https://github.com/your-username/aiforen-api.git
   cd aiforen-api
   ```

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install pre-commit hooks**
   ```bash
   pre-commit install
   ```

5. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your local configuration
   ```

6. **Run the application**
   ```bash
   python main.py
   ```

## Code Quality Standards

### Pre-commit Hooks

This project uses pre-commit hooks to ensure code quality. The hooks will run automatically before each commit:

- **black**: Code formatting
- **isort**: Import sorting
- **flake8**: Code linting
- **autoflake**: Remove unused imports
- **trailing-whitespace**: Remove trailing whitespace
- **end-of-file-fixer**: Ensure files end with newline

### Code Style Guidelines

- Follow PEP 8 style guide
- Use type hints for all function parameters and return values
- Write docstrings for all public functions and classes
- Keep functions small and focused (max 20-30 lines)
- Use meaningful variable and function names
- Maximum line length: 88 characters (Black's default)

### Testing

- Write tests for all new features
- Maintain test coverage above 80%
- Use pytest for testing
- Follow the AAA pattern (Arrange, Act, Assert)

## Commit Message Guidelines

Use clear and meaningful commit messages:

```
type(scope): brief description

Longer description if needed

- Bullet points for multiple changes
- Reference issues: Fixes #123
```

### Commit Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Examples

```
feat(auth): add Google OAuth2 authentication

- Implement Google OAuth2 flow
- Add user profile creation from Google data
- Update authentication middleware

Fixes #45
```

## Branch Naming

Use descriptive branch names:

- `feature/add-payment-integration`
- `fix/authentication-bug`
- `docs/update-api-documentation`
- `refactor/improve-database-queries`

## Pull Request Process

1. **Create a descriptive PR title**
   ```
   feat: Add writing task submission endpoint
   ```

2. **Fill out the PR template**
   - Description of changes
   - Testing performed
   - Screenshots (if applicable)
   - Breaking changes (if any)

3. **Ensure all checks pass**
   - Pre-commit hooks
   - Tests
   - Linting

4. **Request review**
   - Assign reviewers
   - Wait for approval before merging

## Code Review Guidelines

### For Reviewers

- Be constructive and respectful in feedback
- Focus on code quality, not style (handled by tools)
- Check for:
  - Logic errors
  - Security vulnerabilities
  - Performance implications
  - API design consistency
  - Test coverage

### For Authors

- Respond to feedback promptly
- Make requested changes or explain reasoning
- Keep PRs focused and small
- Update documentation as needed

## Issue Guidelines

### Bug Reports

Use the bug report template and include:

- Clear, descriptive title
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment details
- Screenshots/logs if applicable

### Feature Requests

Use the feature request template and include:

- Clear, descriptive title
- Problem statement
- Proposed solution
- Alternatives considered
- Implementation considerations

## Documentation

- Update README.md for significant changes
- Add docstrings for all public APIs
- Update API documentation
- Include examples for new features

## Security

- Never commit sensitive information (API keys, passwords)
- Use environment variables for configuration
- Report security vulnerabilities privately
- Follow security best practices

## Getting Help

- Check existing issues and documentation
- Ask questions in GitHub Discussions
- Join our community chat
- Contact maintainers directly for urgent issues

## Recognition

Contributors will be recognized in:

- CONTRIBUTORS.md file
- Release notes
- Project documentation

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

## Code of Conduct

This project adheres to the Contributor Covenant Code of Conduct. By participating, you are expected to uphold this code.

## Questions?

Don't hesitate to ask! We're here to help:

- 📧 Email: developers@aiforen.com
- 💬 GitHub Discussions
- 🐛 GitHub Issues

Thank you for contributing to AIFOREN API! 🚀
