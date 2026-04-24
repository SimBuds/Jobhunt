# Plan: project structure reorganization

## Context

`src/` currently has 15 files flat plus a `sources/` subfolder. The approved reorganization groups files by intent into `core/`, `apply/`, and `discover/` subdirectories. This is a pure rename/move refactor — no logic changes, no API changes, just path updates.

## Final directory layout

```
src/
├── core/                        # cross-cutting infra
│   ├── config.js                (was src/config.js)
│   ├── companies.js             (was src/companies.js)
│   ├── feedback.js              (was src/feedback.js)
│   ├── prompt.js                (was src/prompt.js)
│   ├── stream.js                (was src/_stream.js)
│   └── track.js                 (was src/track.js)
│
├── apply/                       # scrape→analyze→tailor→render→autofill pipeline
│   ├── scrape.js                (was src/scrape.js)
│   ├── analyze.js               (was src/analyze.js)
│   ├── tailor.js                (was src/tailor.js)
│   ├── coverletter.js           (was src/coverletter.js)
│   ├── render.js                (was src/render.js)
│   └── autofill.js              (was src/autofill.js)
│
├── discover/                    # scan + scoring + job sources
│   ├── scan.js                  (was src/scan.js)
│   ├── score.js                 (was src/score.js)
│   └── sources/                 (was src/sources/)
│       ├── browser-search.js
│       ├── civicjobs.js
│       ├── indeed.js
│       ├── jobbank.js
│       ├── linkedin.js
│       └── workopolis.js
│
├── convert.js                   (stays at src/convert.js — standalone command)
└── report.js                    (stays at src/report.js — standalone command)
```

## Complete import update map

Every file that needs an internal import path changed after the move:

### `cli.js` (root — paths all gain one `src/` segment now rooted differently)
```
./src/scrape.js          → ./src/apply/scrape.js
./src/analyze.js         → ./src/apply/analyze.js
./src/tailor.js          → ./src/apply/tailor.js
./src/coverletter.js     → ./src/apply/coverletter.js
./src/render.js          → ./src/apply/render.js
./src/autofill.js        → ./src/apply/autofill.js
./src/track.js           → ./src/core/track.js
./src/scan.js            → ./src/discover/scan.js
./src/report.js          → ./src/report.js  (unchanged)
./src/convert.js         → ./src/convert.js (unchanged)
./src/prompt.js          → ./src/core/prompt.js
./src/feedback.js        → ./src/core/feedback.js
```

### `src/apply/analyze.js`
```
./_stream.js  → ../core/stream.js
```

### `src/apply/tailor.js`
```
./_stream.js  → ../core/stream.js
RESUME_PATH:  new URL('../base-resume.json', ...)       → new URL('../../base-resume.json', ...)
LEGACY:       new URL('../data/base-resume.json', ...)  → new URL('../../data/base-resume.json', ...)
```

### `src/apply/coverletter.js`
```
./_stream.js  → ../core/stream.js
```

### `src/apply/autofill.js`
```
./tailor.js   → ./tailor.js   (same dir — no change)
./scrape.js   → ./scrape.js   (same dir — no change)
```

### `src/apply/scrape.js`
```
CACHE_DIR: join(__dirname, '..', 'data', ...)  → join(__dirname, '..', '..', 'data', ...)
```

### `src/apply/render.js`
```
OUTPUT_DIR: join(__dirname, '..', 'output')  → join(__dirname, '..', '..', 'output')
```

### `src/discover/scan.js`
```
./tailor.js       → ../apply/tailor.js
./companies.js    → ../core/companies.js
./score.js        → ./score.js          (same dir — no change)
./config.js       → ../core/config.js
./sources/...     → ./sources/...       (same relative — no change)
PIPELINE_DIR: join(__dirname, '..', 'applications')  → join(__dirname, '..', '..', 'applications')
```

### `src/discover/sources/browser-search.js`
```
PROFILE_DIR: join(..., '..', '..', 'data', 'browser-profile')
           → join(..., '..', '..', '..', 'data', 'browser-profile')
```
(moved one level deeper: was `src/sources/`, now `src/discover/sources/`)

### `src/convert.js` (stays in `src/` — paths from src/ root)
```
./_stream.js  → ./core/stream.js
./tailor.js   → ./apply/tailor.js
./prompt.js   → ./core/prompt.js
```

### `src/report.js` (stays in `src/` — paths from src/ root)
```
./scan.js      → ./discover/scan.js
./feedback.js  → ./core/feedback.js
./track.js     → ./core/track.js
```

### `src/core/config.js` (was `src/config.js`)
```
CONFIG_PATH: join(__dirname, '..', 'data', ...)  → join(__dirname, '..', '..', 'data', ...)
```

### `src/core/companies.js` (was `src/companies.js`)
```
COMPANIES_PATH: join(__dirname, '..', 'data', ...)  → join(__dirname, '..', '..', 'data', ...)
```

### `src/core/feedback.js` (was `src/feedback.js`)
```
FEEDBACK_PATH: join(dirname(...), '..', 'feedback.md')  → join(dirname(...), '..', '..', 'feedback.md')
```

### `src/core/track.js` (was `src/track.js`)
```
DB_PATH: join(__dirname, '..', 'data', ...)  → join(__dirname, '..', '..', 'data', ...)
```

### `src/core/stream.js` (was `src/_stream.js`)
No internal imports — rename only.

### `src/core/prompt.js` (was `src/prompt.js`)
No internal imports — rename only.

## Execution order

1. `git mv` all files to new locations (preserves git history as renames).
2. Update import strings inside each file (listed above).
3. Update path constants (the `join(...)` and `new URL(...)` depth changes).
4. Syntax-check every file with `node --check`.
5. Smoke-test: `node cli.js --help`, `npm run scan`, `node cli.js report`.
6. Update `agent-instructions.md` module map to reflect new paths.
7. Update `README.md` "Troubleshooting" links if any reference `src/` paths.

## Verification

```bash
node cli.js --help          # all commands present
node cli.js report          # reads pipeline + feedback + track — exercises core + discover + report
npm run scan                # exercises discover/* + core/* + apply/tailor (for loadBaseResume)
node cli.js list            # exercises core/track
node --check src/core/*.js src/apply/*.js src/discover/*.js src/discover/sources/*.js src/convert.js src/report.js
```

No behavior changes. All output, database paths, pipeline paths, and PDF output paths must remain identical.
