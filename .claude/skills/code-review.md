---
name: code-review
description: Perform comprehensive code review including type checking, linting, and best practices analysis
---

You are an expert code reviewer. Perform a comprehensive code review of the changes:

## Type Checking with Pyright

Run pyright to check type safety:
```bash
uv run pyright
```

If pyright is not installed, install it first:
```bash
uv add --dev pyright
```

## Code Quality Analysis

1. **Type Safety**: Review pyright results for any type errors
2. **Code Style**: Ensure code follows [docs/CODE_STYLE.md](docs/CODE_STYLE.md) patterns
3. **Import Conventions**: Check for proper utility module usage (no direct datetime/os/uuid imports)
4. **Error Handling**: Verify explicit error handling with specific HTTP status codes
5. **Type Annotations**: Ensure complete type annotations on all functions
6. **Async Patterns**: Verify async/await is used correctly for database operations
7. **Security**: Check for potential security issues (injection, XSS, etc.)

## Generate Review Report

Provide a structured review with:
- **Critical Issues** (must fix): Type errors, security vulnerabilities, broken imports
- **Warnings** (should fix): Missing type annotations, code style violations, unused code
- **Suggestions** (nice to have): Performance improvements, code simplification opportunities
- **Positive Notes**: What's done well

For each issue, include:
- File location and line numbers
- Severity level
- Specific explanation of the problem
- Recommended fix with code example

## Project-Specific Checks

- Verify no direct built-in imports (datetime, os, uuid, etc.)
- Check that utility modules are used properly
- Ensure FastAPI dependency injection patterns
- Verify async database operations with AsyncSession
- Check for proper UUID handling (strings in DB, UUID objects for validation)

Focus on actionable feedback that improves code quality, maintainability, and follows the project's established patterns.