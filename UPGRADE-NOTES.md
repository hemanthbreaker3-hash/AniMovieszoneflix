# AniMoviesZoneflix UI / pagination upgrade

- Fixed the homepage catalog API path to `/api/catalog` in the template-based frontend.
- Added a categories fallback fetch from `/api/categories`.
- Added numbered catalog pagination (24 titles per page) to the homepage and Vercel-rendered homepage.
- Reworked episode/group browser pagination to 8 entries per page with page numbers.
- Added episode search and season filtering based on group labels (e.g. `S01E01`, `Season 1`).
- Added episode count and improved pagination styling.
- Refreshed the built-in player frame styling while keeping the existing playback engine and behavior.
- Updated Vercel catch-all rendering (`api/[...path].js`) and FastAPI/Jinja templates so both deployment paths receive the UI fixes.

Validation: Node syntax check and Python compile check passed.
