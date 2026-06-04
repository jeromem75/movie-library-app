import os
import re
import sqlite3
from pathlib import Path
from typing import List, Optional
import xml.etree.ElementTree as ET

VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v',
    '.ts', '.mpg', '.mpeg', '.iso', '.vob', '.m2ts', '.mts',
    '.webm', '.divx', '.xvid', '.flv', '.ogm', '.ogv', '.3gp'
}
MOVIE_YEAR_PATTERNS = [
    # Standard Kodi-style naming: Movie Name (2014)
    re.compile(r'^(?P<title>.+?)\s*\(\s*(?P<year>\d{4})\s*\)\s*$'),
    # Common typo: Movie Name 2014)
    re.compile(r'^(?P<title>.+?)\s+(?P<year>\d{4})\s*\)\s*$'),
    # Common variants: Movie Name - 2014 / Movie Name 2014
    re.compile(r'^(?P<title>.+?)\s*(?:-|–|—)?\s+(?P<year>\d{4})\s*$'),
]




def _path_is_within(path_text: str, root: Path) -> bool:
    """Return True when a stored path belongs to the source root being scanned."""
    if not path_text:
        return False
    try:
        Path(path_text).resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False

def _rows_under_root(conn: sqlite3.Connection, table: str, column: str, root: Path) -> set:
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
    return {row[0] for row in rows if _path_is_within(row[0], root)}

def _delete_rows_under_root(conn: sqlite3.Connection, table: str, column: str, root: Path) -> int:
    paths = sorted(_rows_under_root(conn, table, column, root))
    if paths:
        conn.executemany(f"DELETE FROM {table} WHERE {column}=?", [(p,) for p in paths])
    return len(paths)

def _normalised_video_suffix(file_path: Path) -> str:
    """Return a forgiving lowercase suffix for video detection.

    Some media files have accidental spaces before or inside the extension,
    for example `Film (2020) .mkv` or `Film (2020). mkv`. Path.suffix
    can then include that whitespace, so strip it before comparing.
    """
    suffix = (file_path.suffix or '').lower().strip()
    if not suffix:
        return ''
    return '.' + suffix.lstrip('.').strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    sort_title TEXT,
    year INTEGER,
    country TEXT,
    movie_set TEXT,
    plot TEXT,
    genres TEXT,
    director TEXT,
    actors TEXT,
    movie_path TEXT UNIQUE,
    folder_path TEXT,
    poster_path TEXT,
    fanart_path TEXT,
    nfo_path TEXT,
    video_width INTEGER,
    video_height INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    audio_channels INTEGER,
    file_size_gb REAL,
    favorite INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    missing_since TEXT
);
CREATE INDEX IF NOT EXISTS idx_movies_title ON movies(title);
CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year);
CREATE INDEX IF NOT EXISTS idx_movies_country ON movies(country);
CREATE INDEX IF NOT EXISTS idx_movies_movie_set ON movies(movie_set);
CREATE TABLE IF NOT EXISTS scan_folders (
    folder_path TEXT PRIMARY KEY,
    folder_signature TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tv_shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    sort_title TEXT,
    year INTEGER,
    country TEXT,
    plot TEXT,
    genres TEXT,
    studio TEXT,
    status TEXT,
    premiered TEXT,
    show_path TEXT UNIQUE,
    poster_path TEXT,
    fanart_path TEXT,
    nfo_path TEXT,
    missing INTEGER DEFAULT 0,
    missing_since TEXT
);
CREATE INDEX IF NOT EXISTS idx_tv_shows_title ON tv_shows(title);
CREATE INDEX IF NOT EXISTS idx_tv_shows_year ON tv_shows(year);
CREATE TABLE IF NOT EXISTS tv_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    season_number INTEGER,
    episode_number INTEGER,
    title TEXT,
    plot TEXT,
    air_date TEXT,
    file_path TEXT UNIQUE,
    folder_path TEXT,
    nfo_path TEXT,
    video_width INTEGER,
    video_height INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    audio_channels INTEGER,
    file_size_gb REAL,
    missing INTEGER DEFAULT 0,
    missing_since TEXT,
    FOREIGN KEY(show_id) REFERENCES tv_shows(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tv_episodes_show ON tv_episodes(show_id);
CREATE INDEX IF NOT EXISTS idx_tv_episodes_season_episode ON tv_episodes(show_id, season_number, episode_number);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _ensure_columns(conn)
    _ensure_tv_columns(conn)


def _ensure_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(movies)")}
    wanted = {
        "sort_title": "TEXT",
        "country": "TEXT",
        "movie_set": "TEXT",
        "plot": "TEXT",
        "genres": "TEXT",
        "director": "TEXT",
        "actors": "TEXT",
        "folder_path": "TEXT",
        "poster_path": "TEXT",
        "fanart_path": "TEXT",
        "nfo_path": "TEXT",
        "video_width": "INTEGER",
        "video_height": "INTEGER",
        "video_codec": "TEXT",
        "audio_codec": "TEXT",
        "audio_channels": "INTEGER",
        "file_size_gb": "REAL",
        "favorite": "INTEGER DEFAULT 0",
        "missing": "INTEGER DEFAULT 0",
        "missing_since": "TEXT",
    }
    for name, typ in wanted.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE movies ADD COLUMN {name} {typ}")
    conn.commit()


def _ensure_tv_columns(conn):
    show_cols = {r[1] for r in conn.execute("PRAGMA table_info(tv_shows)")}
    show_wanted = {
        "sort_title": "TEXT",
        "country": "TEXT",
        "plot": "TEXT",
        "genres": "TEXT",
        "studio": "TEXT",
        "status": "TEXT",
        "premiered": "TEXT",
        "show_path": "TEXT",
        "poster_path": "TEXT",
        "fanart_path": "TEXT",
        "nfo_path": "TEXT",
        "missing": "INTEGER DEFAULT 0",
        "missing_since": "TEXT",
    }
    for name, typ in show_wanted.items():
        if name not in show_cols:
            conn.execute(f"ALTER TABLE tv_shows ADD COLUMN {name} {typ}")

    ep_cols = {r[1] for r in conn.execute("PRAGMA table_info(tv_episodes)")}
    ep_wanted = {
        "season_number": "INTEGER",
        "episode_number": "INTEGER",
        "title": "TEXT",
        "plot": "TEXT",
        "air_date": "TEXT",
        "file_path": "TEXT",
        "folder_path": "TEXT",
        "nfo_path": "TEXT",
        "video_width": "INTEGER",
        "video_height": "INTEGER",
        "video_codec": "TEXT",
        "audio_codec": "TEXT",
        "audio_channels": "INTEGER",
        "file_size_gb": "REAL",
        "missing": "INTEGER DEFAULT 0",
        "missing_since": "TEXT",
    }
    for name, typ in ep_wanted.items():
        if name not in ep_cols:
            conn.execute(f"ALTER TABLE tv_episodes ADD COLUMN {name} {typ}")
    conn.commit()


def _clean_movie_title_from_filename(value: str) -> str:
    """Normalise harmless filename spacing without changing the displayed title too much."""
    text = (value or '').strip()
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' -–—_')


def parse_movie_stem(stem: str):
    """Parse real-world movie filenames.

    Preferred format is still `Movie Name (Year)`, but the scanner is now
    forgiving for common library issues found during migration, such as:
    - `Movie Name (2014 )`
    - `Movie Name 2007)`
    - `Movie Name - 2015`
    - `Movie Name.mkv` when no year is present
    """
    stem = (stem or '').strip()
    if not stem:
        return None

    for pattern in MOVIE_YEAR_PATTERNS:
        m = pattern.match(stem)
        if m:
            title = _clean_movie_title_from_filename(m.group('title'))
            year = int(m.group('year'))
            if title:
                return title, year

    # Last-resort fallback: keep title-only files instead of skipping them.
    # NFO metadata can still provide the year/title where present.
    title = _clean_movie_title_from_filename(stem)
    if title:
        return title, None
    return None


def text_or_none(node, path):
    v = node.findtext(path)
    if v is None:
        return None
    v = v.strip()
    return v or None


def parse_nfo(nfo_path: Path):
    data = {
        "title": None,
        "sort_title": None,
        "year": None,
        "country": None,
        "movie_set": None,
        "plot": None,
        "genres": None,
        "director": None,
        "actors": None,
        "video_width": None,
        "video_height": None,
        "video_codec": None,
        "audio_codec": None,
        "audio_channels": None,
    }
    if not nfo_path.exists():
        return data
    try:
        root = ET.parse(nfo_path).getroot()
    except Exception:
        return data

    data["title"] = text_or_none(root, "title")
    data["sort_title"] = text_or_none(root, "sorttitle")
    year = text_or_none(root, "year") or text_or_none(root, "premiered") or text_or_none(root, "aired")
    data["year"] = _parse_year_from_text(year)

    countries = []
    for c in root.findall("country"):
        if c.text and c.text.strip():
            countries.append(c.text.strip())
    data["country"] = ", ".join(dict.fromkeys(countries)) if countries else None

    data["movie_set"] = text_or_none(root, "set/name")
    data["plot"] = text_or_none(root, "plot")

    genres = []
    for g in root.findall("genre"):
        if g.text and g.text.strip():
            genres.append(g.text.strip())
    data["genres"] = ", ".join(dict.fromkeys(genres)) if genres else None

    directors = []
    for d in root.findall("director"):
        if d.text and d.text.strip():
            directors.append(d.text.strip())
    data["director"] = ", ".join(dict.fromkeys(directors)) if directors else None

    actors = []
    for a in root.findall("actor"):
        name = text_or_none(a, "name")
        if name:
            actors.append(name)
    data["actors"] = ", ".join(actors) if actors else None

    v = root.find("./fileinfo/streamdetails/video")
    if v is None:
        v = root.find(".//streamdetails/video")

    if v is not None:
        data["video_codec"] = text_or_none(v, "codec")
        width = text_or_none(v, "width")
        height = text_or_none(v, "height")
        try:
            data["video_width"] = int(float(width)) if width else None
        except Exception:
            data["video_width"] = None
        try:
            data["video_height"] = int(float(height)) if height else None
        except Exception:
            data["video_height"] = None

    a = root.find("./fileinfo/streamdetails/audio")
    if a is None:
        a = root.find(".//streamdetails/audio")

    if a is not None:
        data["audio_codec"] = text_or_none(a, "codec")
        ch = text_or_none(a, "channels")
        try:
            data["audio_channels"] = int(float(ch)) if ch else None
        except Exception:
            data["audio_channels"] = None

    return data


def upsert_movie(conn, movie: dict):
    conn.execute(
        """
        INSERT INTO movies (
            title, sort_title, year, country, movie_set, plot, genres, director, actors,
            movie_path, folder_path, poster_path, fanart_path, nfo_path,
            video_width, video_height, video_codec, audio_codec, audio_channels, file_size_gb
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(movie_path) DO UPDATE SET
            title=excluded.title,
            sort_title=excluded.sort_title,
            year=excluded.year,
            country=excluded.country,
            movie_set=excluded.movie_set,
            plot=excluded.plot,
            genres=excluded.genres,
            director=excluded.director,
            actors=excluded.actors,
            folder_path=excluded.folder_path,
            poster_path=excluded.poster_path,
            fanart_path=excluded.fanart_path,
            nfo_path=excluded.nfo_path,
            video_width=excluded.video_width,
            video_height=excluded.video_height,
            video_codec=excluded.video_codec,
            audio_codec=excluded.audio_codec,
            audio_channels=excluded.audio_channels,
            file_size_gb=excluded.file_size_gb,
            missing=0,
            missing_since=NULL
        """,
        (
            movie["title"], movie["sort_title"], movie["year"], movie["country"], movie["movie_set"], movie["plot"],
            movie["genres"], movie["director"], movie["actors"], movie["movie_path"], movie["folder_path"],
            movie["poster_path"], movie["fanart_path"], movie["nfo_path"], movie["video_width"], movie["video_height"],
            movie["video_codec"], movie["audio_codec"], movie["audio_channels"], movie["file_size_gb"]
        )
    )


def _iter_video_files(folder: Path, recursive: bool = True):
    files = folder.rglob("*") if recursive else folder.glob("*")
    for file_path in files:
        if file_path.is_file() and _normalised_video_suffix(file_path) in VIDEO_EXTENSIONS:
            yield file_path


def _build_folder_signature(folder_path: Path) -> str:
    parts = []
    for child in sorted(folder_path.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_file():
            continue
        try:
            stat = child.stat()
        except Exception:
            continue
        parts.append(f"{child.name.lower()}|{int(stat.st_mtime_ns)}|{stat.st_size}")
    return f"{len(parts)}#" + "#".join(parts)


def _normalise_duplicate_text(value) -> str:
    """Create a stable, forgiving key for duplicate detection/reporting."""
    text = str(value or "").strip().lower()
    text = re.sub(r"\(\d{4}\)", " ", text)
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _movie_duplicate_key(movie: dict):
    title = movie.get("sort_title") or movie.get("title") or ""
    year = movie.get("year")
    return (_normalise_duplicate_text(title), year)


def _tv_duplicate_key(show: dict):
    title = show.get("sort_title") or show.get("title") or ""
    year = show.get("year")
    return (_normalise_duplicate_text(title), year)


def _path_sample(paths, limit=12):
    return [str(p) for p in list(paths)[:limit]]


def _quality_summary_from_report(report: dict) -> str:
    attention = []
    for label, key in (
        ("missing NFO", "missing_nfo"),
        ("missing poster", "missing_poster"),
        ("missing fanart", "missing_fanart"),
        ("unparsed video names", "unparsed_video_files"),
        ("duplicate groups", "duplicate_groups"),
    ):
        value = int(report.get(key) or 0)
        if value:
            attention.append(f"{value} {label}")
    if not attention:
        return "No quality issues detected in the scanned folders."
    return "Needs attention: " + ", ".join(attention) + "."


def _choose_best_duplicate(candidates):
    def score(item):
        return (
            1 if item.get("nfo_path") else 0,
            1 if item.get("poster_path") else 0,
            1 if item.get("fanart_path") else 0,
            1 if item.get("plot") else 0,
            float(item.get("file_size_gb") or 0),
            str(item.get("movie_path") or "").lower(),
        )
    return sorted(candidates, key=score, reverse=True)[0]


def _cleanup_duplicate_movies(conn):
    cursor = conn.execute(
        """
        SELECT id, title, sort_title, year, movie_path, nfo_path, poster_path, fanart_path, file_size_gb
        FROM movies
        WHERE TRIM(COALESCE(title, '')) <> '' AND year IS NOT NULL
        ORDER BY LOWER(COALESCE(sort_title, title)), year, id
        """
    )
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    groups = {}
    for movie in rows:
        key = _movie_duplicate_key(movie)
        if key[0]:
            groups.setdefault(key, []).append(movie)

    duplicate_groups = 0
    duplicate_entries_removed = 0
    kept_paths = []
    delete_ids = []

    for movies in groups.values():
        if len(movies) < 2:
            continue
        winner = _choose_best_duplicate(movies)
        duplicate_groups += 1
        kept_paths.append(winner.get("movie_path") or "")
        for movie in movies:
            if movie["id"] == winner["id"]:
                continue
            delete_ids.append((movie["id"],))
            duplicate_entries_removed += 1

    if delete_ids:
        conn.executemany("DELETE FROM movies WHERE id=?", delete_ids)

    return {
        "duplicate_groups": duplicate_groups,
        "duplicate_entries_removed": duplicate_entries_removed,
        "duplicate_kept_paths": [p for p in kept_paths if p],
    }


def scan_movies_folder(folder: Path, conn: sqlite3.Connection, clean: bool = False, recursive: bool = True, return_report: bool = False):
    init_db(conn)
    folder = folder.resolve()
    existing_paths_before = _rows_under_root(conn, "movies", "movie_path", folder)
    cached_folder_signatures = {
        row[0]: row[1]
        for row in conn.execute("SELECT folder_path, folder_signature FROM scan_folders")
        if _path_is_within(row[0], folder)
    }

    if clean:
        _delete_rows_under_root(conn, "movies", "movie_path", folder)
        _delete_rows_under_root(conn, "scan_folders", "folder_path", folder)
        conn.commit()
        existing_paths_before = set()
        cached_folder_signatures = {}

    video_files_by_folder = {}
    for file_path in _iter_video_files(folder, recursive=recursive):
        video_files_by_folder.setdefault(file_path.parent.resolve(), []).append(file_path)

    scanned = 0
    saved = 0
    inserted = 0
    updated = 0
    deleted = 0
    missing_nfo = 0
    missing_poster = 0
    missing_fanart = 0
    folders_seen = 0
    folders_scanned = 0
    folders_skipped = 0
    folders_scanned_samples = []
    folders_skipped_samples = []
    unparsed_video_files = 0
    unparsed_samples = []
    duplicate_candidate_groups = 0
    seen_paths = set()
    folder_signature_updates = []

    for folder_path, video_files in sorted(video_files_by_folder.items(), key=lambda item: str(item[0]).lower()):
        folders_seen += 1
        current_signature = _build_folder_signature(folder_path)
        previous_signature = cached_folder_signatures.get(str(folder_path))

        if not clean and previous_signature == current_signature:
            folders_skipped += 1
            if len(folders_skipped_samples) < 12:
                folders_skipped_samples.append(str(folder_path))
            existing_rows = conn.execute("SELECT movie_path FROM movies WHERE folder_path=?", (str(folder_path),)).fetchall()
            seen_paths.update(row[0] for row in existing_rows)
            continue

        folders_scanned += 1
        if len(folders_scanned_samples) < 12:
            folders_scanned_samples.append(str(folder_path))
        folder_signature_updates.append((str(folder_path), current_signature))

        # Remove any existing rows for this folder before re-adding winners from the new scan.
        existing_folder_rows = conn.execute("SELECT movie_path FROM movies WHERE folder_path=?", (str(folder_path),)).fetchall()
        for row in existing_folder_rows:
            existing_paths_before.add(row[0])
        conn.execute("DELETE FROM movies WHERE folder_path=?", (str(folder_path),))

        duplicate_candidates = {}

        for file_path in sorted(video_files, key=lambda p: p.name.lower()):
            parsed = parse_movie_stem(file_path.stem)
            if not parsed:
                unparsed_video_files += 1
                if len(unparsed_samples) < 12:
                    unparsed_samples.append(str(file_path.resolve()))
                continue

            scanned += 1
            parsed_title, parsed_year = parsed

            candidate_bases = []
            raw_stem = (file_path.stem or '').strip()
            if parsed_year:
                candidate_bases.append(f"{parsed_title} ({parsed_year})")
            if raw_stem:
                candidate_bases.append(raw_stem)
            candidate_bases.append(parsed_title)
            candidate_bases = list(dict.fromkeys(base for base in candidate_bases if base))

            nfo_path = next((file_path.with_name(f"{base}.nfo") for base in candidate_bases if file_path.with_name(f"{base}.nfo").exists()), file_path.with_name(f"{candidate_bases[0]}.nfo"))
            poster_path = next((file_path.with_name(f"{base}-poster.jpg") for base in candidate_bases if file_path.with_name(f"{base}-poster.jpg").exists()), file_path.with_name(f"{candidate_bases[0]}-poster.jpg"))
            fanart_path = next((file_path.with_name(f"{base}-fanart.jpg") for base in candidate_bases if file_path.with_name(f"{base}-fanart.jpg").exists()), file_path.with_name(f"{candidate_bases[0]}-fanart.jpg"))

            if not nfo_path.exists():
                missing_nfo += 1
            if not poster_path.exists():
                missing_poster += 1
            if not fanart_path.exists():
                missing_fanart += 1

            nfo = parse_nfo(nfo_path)

            try:
                file_size_gb = round(file_path.stat().st_size / (1024 ** 3), 2)
            except Exception:
                file_size_gb = None

            resolved_movie_path = str(file_path.resolve())
            seen_paths.add(resolved_movie_path)

            movie = {
                "title": nfo["title"] or parsed_title,
                "sort_title": nfo["sort_title"] or nfo["title"] or parsed_title,
                "year": nfo["year"] or parsed_year,
                "country": nfo["country"],
                "movie_set": nfo["movie_set"],
                "plot": nfo["plot"],
                "genres": nfo["genres"],
                "director": nfo["director"],
                "actors": nfo["actors"],
                "movie_path": resolved_movie_path,
                "folder_path": str(file_path.parent.resolve()),
                "poster_path": str(poster_path.resolve()) if poster_path.exists() else None,
                "fanart_path": str(fanart_path.resolve()) if fanart_path.exists() else None,
                "nfo_path": str(nfo_path.resolve()) if nfo_path.exists() else None,
                "video_width": nfo["video_width"],
                "video_height": nfo["video_height"],
                "video_codec": nfo["video_codec"],
                "audio_codec": nfo["audio_codec"],
                "audio_channels": nfo["audio_channels"],
                "file_size_gb": file_size_gb,
            }

            key = _movie_duplicate_key(movie)
            if key[0]:
                duplicate_candidates.setdefault(key, []).append(movie)

        for movies in duplicate_candidates.values():
            if len(movies) > 1:
                duplicate_candidate_groups += 1
            winner = _choose_best_duplicate(movies)
            upsert_movie(conn, winner)
            saved += 1
            if winner["movie_path"] in existing_paths_before:
                updated += 1
            else:
                inserted += 1

    if folder_signature_updates:
        conn.executemany(
            "INSERT INTO scan_folders(folder_path, folder_signature, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(folder_path) DO UPDATE SET folder_signature=excluded.folder_signature, updated_at=CURRENT_TIMESTAMP",
            folder_signature_updates,
        )

    if clean:
        deleted = len(existing_paths_before)
    else:
        stale_paths = existing_paths_before - seen_paths
        deleted = len(stale_paths)
        if seen_paths:
            conn.executemany("UPDATE movies SET missing=0, missing_since=NULL WHERE movie_path=?", [(p,) for p in seen_paths])
        if stale_paths:
            conn.executemany(
                "UPDATE movies SET missing=1, missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP) WHERE movie_path=?",
                [(p,) for p in stale_paths],
            )

    existing_folder_paths = {str(p) for p in video_files_by_folder.keys()}
    if existing_folder_paths:
        stale_folder_rows = conn.execute("SELECT folder_path FROM scan_folders").fetchall()
        stale_folder_paths = [
            (row[0],)
            for row in stale_folder_rows
            if _path_is_within(row[0], folder) and row[0] not in existing_folder_paths
        ]
        if stale_folder_paths:
            conn.executemany("DELETE FROM scan_folders WHERE folder_path=?", stale_folder_paths)

    duplicate_report = _cleanup_duplicate_movies(conn)
    conn.commit()

    unchanged_percent = round((folders_skipped / folders_seen) * 100, 1) if folders_seen else 0.0
    report = {
        "folder": str(folder),
        "clean": bool(clean),
        "scanned": scanned,
        "saved": saved,
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "missing_nfo": missing_nfo,
        "missing_poster": missing_poster,
        "missing_fanart": missing_fanart,
        "folders_seen": folders_seen,
        "folders_scanned": folders_scanned,
        "folders_skipped": folders_skipped,
        "folders_unchanged_percent": unchanged_percent,
        "folders_scanned_samples": folders_scanned_samples,
        "folders_skipped_samples": folders_skipped_samples,
        "unparsed_video_files": unparsed_video_files,
        "unparsed_samples": unparsed_samples,
        "duplicate_candidate_groups": duplicate_candidate_groups,
        "duplicate_groups": duplicate_report["duplicate_groups"],
        "duplicate_entries_removed": duplicate_report["duplicate_entries_removed"],
        "duplicate_kept_paths": duplicate_report["duplicate_kept_paths"],
    }
    report["quality_summary"] = _quality_summary_from_report(report)

    if return_report:
        return scanned, saved, report
    return scanned, saved


TV_EPISODE_PATTERN = re.compile(r'(?i)(?:^|[^a-z0-9])s(?P<season>\d{1,2})e(?P<episode>\d{1,3})(?P<extras>(?:e\d{1,3})*)(?:[^a-z0-9]|$)')
TV_1X_PATTERN = re.compile(r'(?i)(?:^|[^a-z0-9])(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?:[^a-z0-9]|$)')
SEASON_FOLDER_PATTERN = re.compile(r'(?i)^(?:season|series)\s*(?P<season>\d{1,2})$')
SPECIALS_FOLDER_PATTERN = re.compile(r'(?i)^specials?$')


def _text_list(root, tag_name):
    values = []
    for node in root.findall(tag_name):
        if node.text and node.text.strip():
            values.append(node.text.strip())
    return ", ".join(dict.fromkeys(values)) if values else None


def _parse_year_from_text(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    m = re.search(r"(19|20)\d{2}", value)
    return int(m.group(0)) if m else None


def _episode_nfo_candidates(file_path: Path) -> List[Path]:
    candidates = [file_path.with_suffix('.nfo')]
    stem = file_path.stem
    clean_stem = (stem or '').strip()
    parent = file_path.parent
    if clean_stem and clean_stem != stem:
        candidates.append(parent / f"{clean_stem}.nfo")
    lower_stem = stem.lower()
    if lower_stem.endswith('.en') or lower_stem.endswith('.eng'):
        candidates.append(parent / (stem.rsplit('.', 1)[0] + '.nfo'))
    unique = []
    seen = set()
    for item in candidates:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _pick_existing_path(candidates: List[Path]) -> Optional[Path]:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _tvshow_nfo_candidates(show_folder: Path) -> List[Path]:
    folder_name = show_folder.name
    return [
        show_folder / 'tvshow.nfo',
        show_folder / f'{folder_name}.nfo',
        show_folder / 'series.nfo',
    ]


def _show_artwork_candidates(show_folder: Path, kind: str) -> List[Path]:
    folder_name = show_folder.name
    if kind == 'poster':
        names = ['poster.jpg', 'folder.jpg', f'{folder_name}-poster.jpg', f'{folder_name}.jpg']
    else:
        names = ['fanart.jpg', 'backdrop.jpg', f'{folder_name}-fanart.jpg']
    return [show_folder / name for name in names]


def _is_season_like_folder(folder_name: str) -> bool:
    if not folder_name:
        return False
    return bool(SEASON_FOLDER_PATTERN.match(folder_name) or SPECIALS_FOLDER_PATTERN.match(folder_name))


def _infer_season_from_folder(folder_name: str):
    if not folder_name:
        return None
    folder_name = folder_name.strip()
    if SPECIALS_FOLDER_PATTERN.match(folder_name):
        return 0
    m = SEASON_FOLDER_PATTERN.match(folder_name)
    if m:
        try:
            return int(m.group('season'))
        except Exception:
            return None
    return None


def _parse_episode_info(stem: str):
    stem = (stem or '').strip()
    m = TV_EPISODE_PATTERN.search(stem)
    if m:
        season = int(m.group('season'))
        episode = int(m.group('episode'))
        extras = [int(x) for x in re.findall(r'e(\d{1,3})', m.group('extras') or '', flags=re.I)]
        return season, episode, extras
    m = TV_1X_PATTERN.search(stem)
    if m:
        return int(m.group('season')), int(m.group('episode')), []
    return None, None, []


def parse_tvshow_nfo(nfo_path: Path):
    data = {
        "title": None,
        "sort_title": None,
        "year": None,
        "country": None,
        "plot": None,
        "genres": None,
        "studio": None,
        "status": None,
        "premiered": None,
    }
    if not nfo_path.exists():
        return data
    try:
        root = ET.parse(nfo_path).getroot()
    except Exception:
        return data
    data["title"] = text_or_none(root, "title")
    data["sort_title"] = text_or_none(root, "sorttitle")
    year = text_or_none(root, "year") or text_or_none(root, "premiered") or text_or_none(root, "aired")
    data["year"] = _parse_year_from_text(year)
    data["country"] = _text_list(root, "country")
    data["plot"] = text_or_none(root, "plot")
    data["genres"] = _text_list(root, "genre")
    data["studio"] = _text_list(root, "studio")
    data["status"] = text_or_none(root, "status")
    data["premiered"] = text_or_none(root, "premiered") or text_or_none(root, "aired")
    return data


def parse_episode_nfo(nfo_path: Path):
    data = {
        "title": None,
        "plot": None,
        "season_number": None,
        "episode_number": None,
        "air_date": None,
        "video_width": None,
        "video_height": None,
        "video_codec": None,
        "audio_codec": None,
        "audio_channels": None,
    }
    if not nfo_path.exists():
        return data
    try:
        root = ET.parse(nfo_path).getroot()
    except Exception:
        return data
    data["title"] = text_or_none(root, "title")
    data["plot"] = text_or_none(root, "plot")
    season = text_or_none(root, "season")
    episode = text_or_none(root, "episode")
    data["season_number"] = int(season) if season and season.isdigit() else None
    data["episode_number"] = int(episode) if episode and episode.isdigit() else None
    data["air_date"] = text_or_none(root, "aired") or text_or_none(root, "premiered")
    v = root.find("./fileinfo/streamdetails/video") or root.find(".//streamdetails/video")
    if v is not None:
        data["video_codec"] = text_or_none(v, "codec")
        width = text_or_none(v, "width")
        height = text_or_none(v, "height")
        try:
            data["video_width"] = int(float(width)) if width else None
        except Exception:
            data["video_width"] = None
        try:
            data["video_height"] = int(float(height)) if height else None
        except Exception:
            data["video_height"] = None
    a = root.find("./fileinfo/streamdetails/audio") or root.find(".//streamdetails/audio")
    if a is not None:
        data["audio_codec"] = text_or_none(a, "codec")
        ch = text_or_none(a, "channels")
        try:
            data["audio_channels"] = int(float(ch)) if ch else None
        except Exception:
            data["audio_channels"] = None
    return data


def _parse_season_episode(stem: str):
    season, episode, _extras = _parse_episode_info(stem)
    return season, episode


def _find_show_folder(root_folder: Path, file_path: Path):
    current = file_path.parent
    root_folder = root_folder.resolve()
    while True:
        if _pick_existing_path(_tvshow_nfo_candidates(current)) is not None:
            return current
        if current == root_folder:
            break
        if current.parent == current:
            break
        current = current.parent
    if _is_season_like_folder(file_path.parent.name) and file_path.parent.parent.exists():
        return file_path.parent.parent
    return file_path.parent


def upsert_tv_show(conn, show: dict):
    conn.execute(
        """
        INSERT INTO tv_shows (
            title, sort_title, year, country, plot, genres, studio, status, premiered,
            show_path, poster_path, fanart_path, nfo_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(show_path) DO UPDATE SET
            title=excluded.title,
            sort_title=excluded.sort_title,
            year=excluded.year,
            country=excluded.country,
            plot=excluded.plot,
            genres=excluded.genres,
            studio=excluded.studio,
            status=excluded.status,
            premiered=excluded.premiered,
            poster_path=excluded.poster_path,
            fanart_path=excluded.fanart_path,
            nfo_path=excluded.nfo_path,
            missing=0,
            missing_since=NULL
        """,
        (
            show["title"], show["sort_title"], show["year"], show["country"], show["plot"], show["genres"],
            show["studio"], show["status"], show["premiered"], show["show_path"], show["poster_path"],
            show["fanart_path"], show["nfo_path"],
        ),
    )


def upsert_tv_episode(conn, episode: dict):
    conn.execute(
        """
        INSERT INTO tv_episodes (
            show_id, season_number, episode_number, title, plot, air_date,
            file_path, folder_path, nfo_path, video_width, video_height,
            video_codec, audio_codec, audio_channels, file_size_gb
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            show_id=excluded.show_id,
            season_number=excluded.season_number,
            episode_number=excluded.episode_number,
            title=excluded.title,
            plot=excluded.plot,
            air_date=excluded.air_date,
            folder_path=excluded.folder_path,
            nfo_path=excluded.nfo_path,
            video_width=excluded.video_width,
            video_height=excluded.video_height,
            video_codec=excluded.video_codec,
            audio_codec=excluded.audio_codec,
            audio_channels=excluded.audio_channels,
            file_size_gb=excluded.file_size_gb,
            missing=0,
            missing_since=NULL
        """,
        (
            episode["show_id"], episode["season_number"], episode["episode_number"], episode["title"], episode["plot"], episode["air_date"],
            episode["file_path"], episode["folder_path"], episode["nfo_path"], episode["video_width"], episode["video_height"],
            episode["video_codec"], episode["audio_codec"], episode["audio_channels"], episode["file_size_gb"],
        ),
    )


def scan_tv_folder(folder: Path, conn: sqlite3.Connection, clean: bool = False, recursive: bool = True, return_report: bool = False):
    init_db(conn)
    folder = folder.resolve()

    cached_folder_signatures = {
        row[0]: row[1]
        for row in conn.execute("SELECT folder_path, folder_signature FROM scan_folders")
        if _path_is_within(row[0], folder)
    }

    if clean:
        _delete_rows_under_root(conn, "tv_episodes", "file_path", folder)
        _delete_rows_under_root(conn, "tv_shows", "show_path", folder)
        _delete_rows_under_root(conn, "scan_folders", "folder_path", folder)
        conn.commit()
        cached_folder_signatures = {}

    existing_episode_paths_before = _rows_under_root(conn, "tv_episodes", "file_path", folder)
    existing_show_paths_before = _rows_under_root(conn, "tv_shows", "show_path", folder)

    video_files_by_folder = {}
    for file_path in _iter_video_files(folder, recursive=recursive):
        video_files_by_folder.setdefault(file_path.parent.resolve(), []).append(file_path)

    scanned = saved = inserted_episodes = updated_episodes = 0
    inserted_shows = updated_shows = 0
    show_missing_nfo = show_missing_poster = show_missing_fanart = episode_missing_nfo = 0
    seen_episode_paths = set()
    seen_show_paths = set()
    show_id_cache = {}
    show_folder_samples = []
    unparsed_episode_files = 0
    unparsed_episode_samples = []
    folders_seen = folders_scanned = folders_skipped = 0
    folders_scanned_samples = []
    folders_skipped_samples = []
    folder_signature_updates = []

    def mark_cached_folder_seen(folder_path: Path):
        rows = conn.execute(
            """
            SELECT e.file_path, s.show_path
            FROM tv_episodes e
            LEFT JOIN tv_shows s ON s.id=e.show_id
            WHERE e.folder_path=?
            """,
            (str(folder_path),),
        ).fetchall()
        for row in rows:
            if row[0]:
                seen_episode_paths.add(row[0])
            if row[1]:
                seen_show_paths.add(row[1])

    for folder_path, folder_files in sorted(video_files_by_folder.items(), key=lambda item: str(item[0]).lower()):
        folders_seen += 1
        current_signature = _build_folder_signature(folder_path)
        previous_signature = cached_folder_signatures.get(str(folder_path))

        if not clean and previous_signature == current_signature:
            folders_skipped += 1
            if len(folders_skipped_samples) < 12:
                folders_skipped_samples.append(str(folder_path))
            mark_cached_folder_seen(folder_path)
            continue

        folders_scanned += 1
        if len(folders_scanned_samples) < 12:
            folders_scanned_samples.append(str(folder_path))
        folder_signature_updates.append((str(folder_path), current_signature))

        for file_path in sorted(folder_files, key=lambda p: p.name.lower()):
            show_folder = _find_show_folder(folder, file_path)
            show_folder_resolved = str(show_folder.resolve())
            if len(show_folder_samples) < 12 and show_folder_resolved not in show_id_cache:
                show_folder_samples.append(show_folder_resolved)
            if show_folder_resolved not in show_id_cache:
                tvshow_nfo = _pick_existing_path(_tvshow_nfo_candidates(show_folder))
                poster_path = _pick_existing_path(_show_artwork_candidates(show_folder, "poster"))
                fanart_path = _pick_existing_path(_show_artwork_candidates(show_folder, "fanart"))
                if tvshow_nfo is None:
                    show_missing_nfo += 1
                if poster_path is None:
                    show_missing_poster += 1
                if fanart_path is None:
                    show_missing_fanart += 1
                tvshow = parse_tvshow_nfo(tvshow_nfo) if tvshow_nfo is not None else parse_tvshow_nfo(show_folder / "tvshow.nfo")
                folder_name = show_folder.name
                title_guess = folder_name
                m = re.match(r'^(?P<title>.+?)\s*\((?P<year>\d{4})\)$', folder_name)
                if m:
                    title_guess = m.group('title').strip()
                inferred_year = _parse_year_from_text(folder_name)
                show = {
                    "title": tvshow["title"] or title_guess,
                    "sort_title": tvshow["sort_title"] or tvshow["title"] or title_guess,
                    "year": tvshow["year"] or inferred_year,
                    "country": tvshow["country"],
                    "plot": tvshow["plot"],
                    "genres": tvshow["genres"],
                    "studio": tvshow["studio"],
                    "status": tvshow["status"],
                    "premiered": tvshow["premiered"],
                    "show_path": show_folder_resolved,
                    "poster_path": str(poster_path.resolve()) if poster_path is not None else None,
                    "fanart_path": str(fanart_path.resolve()) if fanart_path is not None else None,
                    "nfo_path": str(tvshow_nfo.resolve()) if tvshow_nfo is not None else None,
                }
                was_existing_show = show_folder_resolved in existing_show_paths_before
                upsert_tv_show(conn, show)
                if was_existing_show:
                    updated_shows += 1
                else:
                    inserted_shows += 1
                row = conn.execute("SELECT id FROM tv_shows WHERE show_path=?", (show_folder_resolved,)).fetchone()
                show_id_cache[show_folder_resolved] = row[0]
                seen_show_paths.add(show_folder_resolved)

            show_id = show_id_cache[show_folder_resolved]
            episode_nfo = _pick_existing_path(_episode_nfo_candidates(file_path))
            if episode_nfo is None:
                episode_missing_nfo += 1
            ep_nfo = parse_episode_nfo(episode_nfo) if episode_nfo is not None else parse_episode_nfo(file_path.with_suffix('.nfo'))
            season_number, episode_number, extra_episodes = _parse_episode_info(file_path.stem)
            if season_number is None:
                season_number = _infer_season_from_folder(file_path.parent.name)
            if season_number is None or episode_number is None:
                unparsed_episode_files += 1
                if len(unparsed_episode_samples) < 12:
                    unparsed_episode_samples.append(str(file_path.resolve()))
            if ep_nfo.get("season_number") is not None:
                season_number = ep_nfo["season_number"]
            if ep_nfo.get("episode_number") is not None:
                episode_number = ep_nfo["episode_number"]
            try:
                file_size_gb = round(file_path.stat().st_size / (1024 ** 3), 2)
            except Exception:
                file_size_gb = None
            resolved_file_path = str(file_path.resolve())
            seen_episode_paths.add(resolved_file_path)
            episode = {
                "show_id": show_id,
                "season_number": season_number,
                "episode_number": episode_number,
                "title": ep_nfo["title"] or (f"{file_path.stem.strip()} (E{episode_number:02d}-E{extra_episodes[-1]:02d})" if episode_number is not None and extra_episodes else file_path.stem.strip()),
                "plot": ep_nfo["plot"],
                "air_date": ep_nfo["air_date"],
                "file_path": resolved_file_path,
                "folder_path": str(file_path.parent.resolve()),
                "nfo_path": str(episode_nfo.resolve()) if episode_nfo is not None else None,
                "video_width": ep_nfo["video_width"],
                "video_height": ep_nfo["video_height"],
                "video_codec": ep_nfo["video_codec"],
                "audio_codec": ep_nfo["audio_codec"],
                "audio_channels": ep_nfo["audio_channels"],
                "file_size_gb": file_size_gb,
            }
            upsert_tv_episode(conn, episode)
            scanned += 1
            saved += 1
            if resolved_file_path in existing_episode_paths_before:
                updated_episodes += 1
            else:
                inserted_episodes += 1

    if folder_signature_updates:
        conn.executemany(
            "INSERT INTO scan_folders(folder_path, folder_signature, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(folder_path) DO UPDATE SET folder_signature=excluded.folder_signature, updated_at=CURRENT_TIMESTAMP",
            folder_signature_updates,
        )

    existing_folder_paths = {str(p) for p in video_files_by_folder.keys()}
    if existing_folder_paths:
        stale_folder_rows = conn.execute("SELECT folder_path FROM scan_folders").fetchall()
        stale_folder_paths = [
            (row[0],)
            for row in stale_folder_rows
            if _path_is_within(row[0], folder) and row[0] not in existing_folder_paths
        ]
        if stale_folder_paths:
            conn.executemany("DELETE FROM scan_folders WHERE folder_path=?", stale_folder_paths)

    if clean:
        deleted_episodes = len(existing_episode_paths_before)
        deleted_shows = len(existing_show_paths_before)
    else:
        stale_episode_paths = existing_episode_paths_before - seen_episode_paths
        deleted_episodes = len(stale_episode_paths)
        if seen_episode_paths:
            conn.executemany("UPDATE tv_episodes SET missing=0, missing_since=NULL WHERE file_path=?", [(p,) for p in seen_episode_paths])
        if stale_episode_paths:
            conn.executemany(
                "UPDATE tv_episodes SET missing=1, missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP) WHERE file_path=?",
                [(p,) for p in stale_episode_paths],
            )
        stale_show_paths = existing_show_paths_before - seen_show_paths
        deleted_shows = len(stale_show_paths)
        if seen_show_paths:
            conn.executemany("UPDATE tv_shows SET missing=0, missing_since=NULL WHERE show_path=?", [(p,) for p in seen_show_paths])
        if stale_show_paths:
            conn.executemany(
                "UPDATE tv_shows SET missing=1, missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP) WHERE show_path=?",
                [(p,) for p in stale_show_paths],
            )
    conn.execute("DELETE FROM tv_shows WHERE id NOT IN (SELECT DISTINCT show_id FROM tv_episodes) AND COALESCE(missing,0)=0")

    cursor = conn.execute("SELECT id, title, sort_title, year, show_path FROM tv_shows")
    columns = [col[0] for col in cursor.description]
    show_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    show_duplicate_groups = {}
    for show in show_rows:
        key = _tv_duplicate_key(show)
        if key[0]:
            show_duplicate_groups.setdefault(key, []).append(show)
    duplicate_show_groups = sum(1 for group in show_duplicate_groups.values() if len(group) > 1)

    episode_duplicate_groups = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT show_id, season_number, episode_number, COUNT(*) AS n
            FROM tv_episodes
            WHERE season_number IS NOT NULL AND episode_number IS NOT NULL
            GROUP BY show_id, season_number, episode_number
            HAVING n > 1
        )
        """
    ).fetchone()[0]

    conn.commit()
    unchanged_percent = round((folders_skipped / folders_seen) * 100, 1) if folders_seen else 0.0
    report = {
        "folder": str(folder),
        "clean": bool(clean),
        "scanned": scanned,
        "saved": saved,
        "inserted": inserted_episodes,
        "updated": updated_episodes,
        "inserted_episodes": inserted_episodes,
        "updated_episodes": updated_episodes,
        "inserted_shows": inserted_shows,
        "updated_shows": updated_shows,
        "deleted_episodes": deleted_episodes,
        "deleted_shows": deleted_shows,
        "deleted": deleted_episodes + deleted_shows,
        "missing_show_nfo": show_missing_nfo,
        "missing_poster": show_missing_poster,
        "missing_fanart": show_missing_fanart,
        "missing_episode_nfo": episode_missing_nfo,
        "folders_seen": folders_seen,
        "folders_scanned": folders_scanned,
        "folders_skipped": folders_skipped,
        "folders_unchanged_percent": unchanged_percent,
        "folders_scanned_samples": folders_scanned_samples,
        "folders_skipped_samples": folders_skipped_samples,
        "show_folder_samples": show_folder_samples,
        "unparsed_episode_files": unparsed_episode_files,
        "unparsed_episode_samples": unparsed_episode_samples,
        "duplicate_show_groups": duplicate_show_groups,
        "duplicate_episode_groups": int(episode_duplicate_groups or 0),
        "shows": conn.execute("SELECT COUNT(*) FROM tv_shows").fetchone()[0],
        "episodes": conn.execute("SELECT COUNT(*) FROM tv_episodes").fetchone()[0],
    }
    tv_attention = []
    for label, value in (
        ("missing show NFO", show_missing_nfo),
        ("missing episode NFO", episode_missing_nfo),
        ("missing poster", show_missing_poster),
        ("missing fanart", show_missing_fanart),
        ("unparsed episode names", unparsed_episode_files),
        ("duplicate show groups", duplicate_show_groups),
        ("duplicate episode groups", int(episode_duplicate_groups or 0)),
    ):
        if value:
            tv_attention.append(f"{value} {label}")
    report["quality_summary"] = "No TV quality issues detected." if not tv_attention else "Needs attention: " + ", ".join(tv_attention) + "."
    if return_report:
        return scanned, saved, report
    return scanned, saved


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        init_db(conn)
        scanned, saved, report = scan_movies_folder(Path(args.folder), conn, clean=args.clean, return_report=True)
        print(f"Scanned video files: {scanned}")
        print(f"Database entries inserted/updated: {saved}")
        print(f"Inserted: {report['inserted']}")
        print(f"Updated: {report['updated']}")
        print(f"Deleted stale entries: {report['deleted']}")
        print(f"Folders seen: {report['folders_seen']}")
        print(f"Folders scanned: {report['folders_scanned']}")
        print(f"Folders skipped: {report['folders_skipped']}")
        print(f"Duplicate groups: {report['duplicate_groups']}")
        print(f"Duplicate entries removed: {report['duplicate_entries_removed']}")
        print(f"Missing NFO: {report['missing_nfo']}")
        print(f"Missing poster: {report['missing_poster']}")
        print(f"Missing fanart: {report['missing_fanart']}")
    finally:
        conn.close()
