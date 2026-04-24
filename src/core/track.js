import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = join(__dirname, '..', '..', 'data', 'applications.db');

let db;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.exec(`
      CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        role TEXT NOT NULL,
        url TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL DEFAULT 'applied',
        resume_path TEXT,
        coverletter_path TEXT,
        resume_hash TEXT,
        notes TEXT
      )
    `);
    // Migration guard for existing DBs created before resume_hash was added
    try { db.exec('ALTER TABLE applications ADD COLUMN resume_hash TEXT'); } catch {}
  }
  return db;
}

export function logApplication(opts) {
  const {
    company,
    role,
    url,
    resumePath,
    resume_path,
    coverletterPath,
    coverletter_path,
    resumeHash,
    notes,
  } = opts;
  const resume = resumePath ?? resume_path ?? null;
  const cover = coverletterPath ?? coverletter_path ?? null;
  const hash = resumeHash ?? null;
  const d = getDb();
  const existing = d.prepare('SELECT id FROM applications WHERE url = ?').get(url);
  if (existing) {
    d.prepare(`
      UPDATE applications
      SET company = ?, role = ?, resume_path = ?, coverletter_path = ?, resume_hash = ?, applied_at = datetime('now')
      WHERE id = ?
    `).run(company, role, resume, cover, hash, existing.id);
    return existing.id;
  }
  const stmt = d.prepare(`
    INSERT INTO applications (company, role, url, resume_path, coverletter_path, resume_hash, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  const result = stmt.run(company, role, url, resume, cover, hash, notes || null);
  return result.lastInsertRowid;
}

export function listApplications() {
  return getDb().prepare('SELECT * FROM applications ORDER BY applied_at DESC').all();
}

export function updateStatus(id, status) {
  const d = getDb();
  const result = d.prepare('UPDATE applications SET status = ? WHERE id = ?').run(status, id);
  if (result.changes === 0) throw new Error(`No application found with id ${id}`);
  return d.prepare('SELECT * FROM applications WHERE id = ?').get(id);
}
