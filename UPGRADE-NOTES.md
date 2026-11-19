# AniMoviesZoneflix OTT UI Upgrade

## Visual refresh
- Premium dark slate UI with violet and aqua accent colours.
- Cleaner hero, title detail, catalog cards, and watch page surfaces.
- Refined responsive spacing, focus-visible accessibility, and reduced-motion support.
- Built-in player chrome and episode browser restyled consistently.

## Episode navigation
- Season shortcuts are shown alongside the season dropdown.
- Episode search supports labels such as S01E01.
- Pagination remains at 8 episodes per page, with numbered pages.
- The active episode is highlighted.

## Docker Compose legacy error (`KeyError: ContainerConfig`)
This is usually caused by legacy docker-compose v1.29.2 recreating a container after Docker image metadata changed. Prefer Compose V2 (`docker compose`). If V2 is installed, run:

```bash
docker compose up -d --build --force-recreate web
```

If only the legacy command is available, remove the old web container (not volumes) and recreate it:

```bash
docker-compose rm -sf web
docker-compose up -d --build web
```

Do not run `docker-compose down -v`; it deletes named volumes.


### One-command recovery helper
From the project directory, run `./fix-containerconfig.sh`. It prefers Docker Compose V2, removes only the old `web` service container, then rebuilds/recreates it. It does not remove Redis or volumes.

## Telegram `/del` command

Admins can now delete a catalog page directly from the private bot chat without a website confirmation:

- `/del https://your-site/title/page-slug`
- `/del page-slug`
- `/del Exact Page Title`

The command only accepts an exact slug or exact case-insensitive title match. Partial/fuzzy matches are not deleted, and ambiguous title matches are rejected. It removes the matching document from the shared MongoDB `catalog_pages` collection, so public catalog queries stop returning it.

## Automatic web-container replacement

Run `./fix-containerconfig.sh` from the extracted project directory. It detects Docker Compose V2 when available, removes only old Compose-labeled `web` containers for this project, rebuilds the web image, and starts the replacement. It does not remove Redis, named volumes, or database data. It prints container status and recent logs. If the project directory name differs from the Compose project name, set `COMPOSE_PROJECT_NAME` before running.


## Cinematic UI and season navigation refresh
- Refreshed the home hero, poster rails, navigation branding, typography and season selector with a cinematic dark/gold visual system.
- The site header continues to use the configured `BRANDING` and `LOGO_URL` values. Set these in `.env` to use the exported brand name and logo URL.
- Season tabs now recognize both episode labels (`S01E01`, `1x01`, `Season 1 Episode 1`) and season-level groups named `Season 1`, `S2`, etc. Only seasons present in the catalog are shown; existing groups and links are retained.
- Updated the Vercel watch-page fallback so its player switch control is icon-only instead of showing the “Buffering? Switch to basic player” label.
