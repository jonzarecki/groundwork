# Commit Changes Skill

Create a well-structured commit with full pre-commit validation.

## Trigger
Invoke when asked to commit, or after completing a task.

## Process
1. Run `git status` to review all changes
2. Run `git diff` to analyze what changed
3. Stage relevant files (exclude unrelated changes)
4. Generate a conventional commit message:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `refactor:` for code restructuring
   - `test:` for test additions
   - `docs:` for documentation
   - `chore:` for tooling/config
5. Run pre-commit checks (lint, typecheck, tests)
6. Commit only if all checks pass
7. Update `.context/progress.md` with the commit summary

## Commit Message Format
type(scope): concise description

- Detail 1
- Detail 2
