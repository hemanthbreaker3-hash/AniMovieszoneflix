# Episodes / Seasons fix

- Season filters are built from parsed season and episode numbers, not group array order.
- Supports both flat episode groups (with multiple quality/source buttons) and season containers whose buttons are individual episodes, including nested button arrays.
- Season tabs are sorted numerically; episode lists are sorted numerically within each season.
- A season filter shows only that season. All Seasons shows the complete sorted episode list with season labels.
- Duplicate representations of the same season/episode are collapsed while retaining a playable URL.
- Original group/button indexes and source buttons are preserved so watch links and player source switching continue to work.
- Nested button URLs are also rewritten to the active backend/Cloudflare URL in both FastAPI and Vercel rendering paths.
- Updated the watch-page episode navigator to a premium dark/gold cinematic treatment.

Validation performed: Python compilation, Jinja template parsing, Node syntax check for the Vercel gateway, and automated season sorting/filter/dedup checks.
