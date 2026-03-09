# /review

Review my staged changes like a senior engineer.

Use the review skill at `.claude/skills/review/SKILL.md`.

Output:
- High-risk issues first (security, data loss, auth)
- API correctness and backwards compatibility
- Missing tests (suggest exact test cases)
- Break down into: Must fix / Should fix / Nice to have
- End with a "merge readiness" verdict
