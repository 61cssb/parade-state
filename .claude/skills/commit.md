---
name: commit
description: Create atomic, properly labeled git commits that pass ruff checks
---

You are an expert git practitioner. Create clean, atomic commits with proper labeling.

## Commit Strategy

Break changes into sensible, atomic chunks where each commit:
- Passes `uv run ruff check` and `uv run ruff format --check`
- Contains a single logical change or feature
- Has a clear, descriptive message with proper labeling
- Can be reviewed and reverted independently

## Commit Labels

Use these conventional commit labels:

- **feat:** New feature or functionality
- **fix:** Bug fix or error correction
- **refactor:** Code restructuring without behavior change
- **style:** Code style changes (formatting, naming)
- **test:** Test additions or modifications
- **docs:** Documentation changes
- **perf:** Performance improvements
- **security:** Security vulnerability fixes
- **config:** Configuration changes
- **chore:** Maintenance tasks, dependency updates

## Commit Process

1. **Stage changes**: Review what should be included in each commit
2. **Run checks**: Ensure each chunk passes linting
   ```bash
   uv run ruff check
   uv run ruff format --check
   ```
3. **Create commits**: Use descriptive messages with labels
   ```bash
   git commit -m "feat: add user authentication endpoint"
   git commit -m "fix: resolve timezone handling in date filtering"
   git commit -m "refactor: extract validation logic to utility module"
   ```
4. **Verify**: Check that commits are atomic and properly ordered

## Commit Message Format

Follow this pattern:
```
<label>: <brief description>

<detailed explanation if needed>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

Examples:
- `feat: implement user role-based access control`
- `fix: handle edge case in personnel status validation`
- `refactor: simplify database session management`
- `test: add integration tests for authentication flow`
- `docs: update API documentation for session endpoints`

## Atomic Commit Guidelines

- **DO**: Commit related changes together
- **DO**: Separate feature work from refactoring
- **DO**: Split large changes into logical steps
- **DON'T**: Mix unrelated changes in one commit
- **DON'T**: Include WIP or experimental code
- **DON'T**: Commit files that don't pass linting

## Safety Checks

Before committing:
- Verify all staged files pass ruff checks
- Ensure no sensitive data is included (API keys, passwords)
- Check that commit messages accurately describe changes
- Confirm changes align with project patterns in [CLAUDE.md](CLAUDE.md)

Create clean, reviewable commits that tell a clear story of the work performed.