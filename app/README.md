# Movie Library Manager — Web App

## v28.3.12 - Mac service readiness

This build continues the existing Flask Movie Library web app for Mac. It is not a rebuild.

### New in this build

- Added an admin-only **Mac Service** page at `/mac-service`.
- Added macOS LaunchAgent helper scripts:
  - `run_movie_library_server.command`
  - `install_movie_library_launchd.command`
  - `uninstall_movie_library_launchd.command`
- The Mac Service page shows:
  - Movie Library port
  - local and remote URLs
  - LaunchAgent readiness
  - runner/installer file presence
  - shared Caddy process status
  - safe Caddy validate/reload commands
- The LaunchAgent starts only the Flask Movie Library server.
- It does **not** replace, rewrite, simplify, stop, or manage the shared Caddyfile.
- Viewer accounts remain restricted to **Movies** and **TV Shows** only.

## Current version

```text
UI v28.3.12 - Mac service readiness
```

Check the running version at:

```text
http://127.0.0.1:8765/_ui-version
```

## Important Mac paths

App root:

```text
/Volumes/D/Webserver/movie library/app
```

Main entry point:

```text
/Volumes/D/Webserver/movie library/app/web_app.py
```

Scanner:

```text
/Volumes/D/Webserver/movie library/app/scanner.py
```

Database:

```text
/Volumes/D/Webserver/movie library/app/library.db
```

Shared Caddyfile:

```text
/Volumes/D/Webserver/Caddyfile
```

## Start the web app manually on Mac

From Terminal:

```zsh
cd "/Volumes/D/Webserver/movie library/app"
chmod +x ./run_movie_library_server.command
./run_movie_library_server.command
```

This script creates/uses the local `.venv`, installs dependencies from `requirements.txt`, and runs:

```zsh
.venv/bin/python3 web_app.py
```

## Install automatic startup with launchd

Run this once on the Mac after copying the build into the real app folder:

```zsh
cd "/Volumes/D/Webserver/movie library/app"
chmod +x ./install_movie_library_launchd.command
./install_movie_library_launchd.command
```

This creates:

```text
~/Library/LaunchAgents/com.jay.movie-library.plist
```

It starts Movie Library at login for the signed-in Mac user.

## Remove automatic startup

```zsh
cd "/Volumes/D/Webserver/movie library/app"
chmod +x ./uninstall_movie_library_launchd.command
./uninstall_movie_library_launchd.command
```

## Shared Caddy safety

Caddy is shared with the Tutor app. Do not replace the shared Caddyfile with a single-app config.

Safe commands:

```zsh
caddy validate --config "/Volumes/D/Webserver/Caddyfile"
caddy reload --config "/Volumes/D/Webserver/Caddyfile"
```

Preserve both reverse proxy routes:

```text
mjeromem75.dyndns.org -> 127.0.0.1:8765
jeromem75.dyndns.org -> 127.0.0.1:8123
```

## Admin / viewer access

Admin can access Dashboard, Settings, Reports, Backups, Activity, Health, and Mac Service.

Viewer accounts can only browse Movies and TV Shows.

Default viewer:

```text
Username: Laila
Password: La1laz3b3st
```

Viewer usernames are matched case-insensitively.


## UI v28.3.14 - simplified server page

Adds an admin-only `/server-control` page showing Flask process status, port status, shared Caddy detection, scan state, local/remote URLs, recent server activity, recent errors, and recent log tails. Restart routes are admin-only. The controls preserve the shared Caddyfile and do not replace Tutor routing.


## v28.3.14

- Fixed the Server page template so it loads the normal app styling/navigation instead of showing an odd raw page.
- Simplified `/server-control` into a cleaner admin status page.
- Moved raw logs and technical paths behind a collapsible Technical details section.


## UI v28.3.15 - viewer home experience

- Added `/home` as a simple viewer landing page.
- Viewer accounts now land on Home after login instead of opening the full movie browser immediately.
- Viewer Home shows Movies, TV Shows, and Episodes counts plus quick cards for Movies and TV Shows.
- Viewer navigation remains simple: Home, Movies, TV Shows, Logout.
- Admin users still open Dashboard after login and keep all admin pages.


## UI v28.3.16 - simplified admin organisation

- Simplified admin navigation to Admin, Libraries, Reports, Server, Settings.
- Removed Backup from the visible interface and disabled backup actions by redirecting them to the dashboard.
- Consolidated duplicate scan/check actions into Dashboard, Libraries, and Reports.
- Kept advanced Activity, Health, and Mac Service pages available internally but no longer shown as main navigation items.
- Viewer accounts remain restricted to Home, Movies, and TV Shows.

## UI v28.3.19 - faster libraries admin page

- Top admin navigation now shows one Admin tab instead of separate Admin / Libraries / Reports / Server / Settings tabs.
- The Admin area is organised with internal sections: Overview, Libraries, Reports, Server, and Settings.
- Added an internal admin section switcher to the main admin pages.
- Dashboard renamed visually to Admin and acts as the main control centre.
- Viewer accounts remain simple and can only see Home, Movies, TV Shows, and Logout.
- Backup tools are still hidden from the normal interface.


## v28.3.19
- Libraries admin page now uses a fast cached/database view by default.
- Detailed folder walking is available with the Run detailed comparison button.
- This prevents the Libraries tab from being slow when large/external/network folders are configured.

## UI v28.3.21 - admin alerts inbox

- Reports now show issue cards with a primary View button and a secondary Export button.
- Added browser-based issue detail pages under `/reports/issues/<issue_key>`.
- Issue pages cover movie missing NFO/poster/fanart, movie duplicate groups, TV missing show NFO/poster/fanart, TV missing episode NFO, and TV duplicate groups.
- Reports do not walk monitored folders just to open the page. Use Check libraries to refresh scan data.
- Viewer accounts remain restricted to Home, Movies, and TV Shows.


## v28.3.21 - Admin alerts inbox

- Adds `/reports/alerts` as a single browser view for current movie and TV report issues.
- Alerts can be searched and filtered by library area and issue type.
- Exports remain available but are no longer required to review problems.
- The page reads from the current database/report rows and does not walk media folders just to load.


## UI v28.3.24 - final admin console cleanup
- Added a dedicated Admin > Scans section for manual checks, scheduled refresh status, scan history, and missing item review.
- Internal admin tabs are now organised as Overview, Libraries, Alerts, Scans, Server, and Settings.
- Reports/alerts are focused on issue review, while scan actions and history are consolidated into Scans.
- Scan History and Missing Items now use the same Admin tab styling and return to the Scans section.


## v28.3.23 - Admin users and settings split

- Added a dedicated Admin > Users page.
- Moved admin login, server port, and viewer account management out of the general Settings page.
- Settings now focuses on library folders, scheduled scans, reports, and version/about details.
- Viewer permissions remain unchanged: viewer accounts can only browse Movies and TV Shows.


## v28.3.24 - final admin console cleanup

- Keeps one top-level Admin tab.
- Organises admin work into Overview, Libraries, Alerts, Scans, Server, Users, and Settings.
- Hides older Activity, Health, Mac Service, and Backup tools from the normal admin console.
- Consolidates duplicate scan/restart actions so Check libraries is mainly in Overview/Scans/Libraries and restart controls are only in Server.
- Keeps viewer users restricted to Home, Movies, TV Shows, and Logout.


## v28.3.26
- Added admin alert notes.
- Added alert review queue.
- Alerts can now be filtered by review status.

## UI v28.3.27 - alert bulk actions and queue filter
- Fixed the Alerts queue filter so “Review queue only” and “Not in review” filter the cards correctly.
- Added checkbox selection on the Alerts page.
- Added bulk actions for selected visible alerts: mark for review, remove from review, ignore, and show again.
- Bulk actions save to the existing alert state in `config.json`.

## UI v28.3.27 - alert bulk actions and queue filter
- Fixed the Alerts queue filter so “Review queue only” and “Not in review” filter the cards correctly.
- Added checkbox selection on the Alerts page.
- Added bulk actions for selected visible alerts: mark for review, remove from review, ignore, and show again.
- Bulk actions save to the existing alert state in `config.json`.


## UI v28.3.28 - alert type filter and direct item links
- Alerts now include an issue-type dropdown so large alert lists can be filtered more precisely.
- Alert cards now include a direct Open item button where the affected movie or TV show can be identified.
- The old issue-type page remains available as View group.


## UI v28.3.30 - Mac app readiness API
- Added alert summary counts to the Admin overview.
- Admin overview now shows visible alerts and alerts marked for review alongside movie/TV counts.
- Attention panel now separates open, ignored, movie, and TV alert counts.
- Alerts card now links directly to the review queue.
- Alerts page hero now summarises visible, review, and ignored alert counts.


## v28.3.30 - Mac app readiness API

Added admin-only JSON endpoints for a future macOS server-control app:

- `/api/admin/status`
- `/api/admin/scan-status`
- `/api/admin/server-status`
- `/api/admin/recent-events`
- `/api/admin/run-scan` with actions `refresh-all`, `refresh-movies`, or `refresh-tv`

These endpoints require an admin session and do not expose server controls to viewer users.

## v28.4.0 - Basic macOS Control App

This build adds the first lightweight macOS companion app. It is not a replacement for the web interface and it does not change the shared Caddyfile.

Added files:

- `mac_control_app.py` — dependency-free Tkinter control app.
- `Movie Library Control.command` — launches the control app with `python3`.
- `Movie Library Control.app` — simple macOS app bundle wrapper for the same control app.

The control app can:

- show whether the Flask server is responding on port 8765
- show movie / TV / episode counts via the admin API
- show scan status and scheduled scan status
- open the local web library
- open the remote library URL
- open the Admin console
- trigger Check All Libraries / Check Movies / Check TV Shows
- start the Flask server using the existing `run_movie_library_server.command`
- request web app restart routes when the server is already running

Safety notes:

- It does not start or stop Caddy directly.
- It does not overwrite `/Volumes/D/Webserver/Caddyfile`.
- It avoids starting another Flask process when port 8765 is already responding.
- It reads the local admin credentials from `config.json` and uses the existing admin-only API routes.
