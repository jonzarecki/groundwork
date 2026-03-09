# Code Review Skill

Review staged changes like a senior engineer.

## Trigger
Invoke via `/review` command or when asked to review changes.

## Process
1. Run `git diff --cached` to see staged changes
2. Analyze each changed file for:
   - **Security risks**: auth bypasses, data exposure, injection vulnerabilities
   - **Data integrity**: schema changes, migration safety, data loss potential
   - **API correctness**: backwards compatibility, typed responses, error handling
   - **Missing tests**: identify untested code paths, suggest specific test cases
3. Categorize findings into:
   - **Must fix**: blocking issues (security, data loss, broken API contracts)
   - **Should fix**: quality issues (missing error handling, unclear naming)
   - **Nice to have**: style improvements, minor optimizations

## Output Format

## Review Summary
- Files changed: N
- Risk level: LOW / MEDIUM / HIGH

## Must Fix
- [file:line] Description

## Should Fix
- [file:line] Description

## Nice to Have
- [file:line] Description

## Missing Tests
- [description of untested path] → suggested test case

## Merge Readiness: READY / NEEDS CHANGES / BLOCK
