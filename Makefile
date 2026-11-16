# Entry points for the repository's own checks. Recipe lines start with a tab.
.PHONY: lint-lessons
lint-lessons:
	bash scripts/check-encoded-lessons.sh
