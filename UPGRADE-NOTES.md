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
