import { PDFDocument, StandardFonts, rgb } from 'pdf-lib';
import { writeFile, mkdir } from 'fs/promises';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = join(__dirname, '..', '..', 'output');

const MARGIN = 50;
const PAGE_WIDTH = 612; // US Letter
const PAGE_HEIGHT = 792;
const CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN;

function dateStamp() {
  return new Date().toISOString().slice(0, 10);
}

function sanitize(str) {
  return str.replace(/[^a-zA-Z0-9-_]/g, '_').toLowerCase();
}

async function drawWrappedText(page, text, x, y, font, size, maxWidth, lineHeight) {
  const words = text.split(/\s+/);
  let line = '';
  let curY = y;

  for (const word of words) {
    const test = line ? `${line} ${word}` : word;
    if (font.widthOfTextAtSize(test, size) > maxWidth && line) {
      page.drawText(line, { x, y: curY, size, font, color: rgb(0, 0, 0) });
      curY -= lineHeight;
      line = word;
    } else {
      line = test;
    }
  }
  if (line) {
    page.drawText(line, { x, y: curY, size, font, color: rgb(0, 0, 0) });
    curY -= lineHeight;
  }
  return curY;
}

async function renderResumePdf(resume, company) {
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  const fontBold = await doc.embedFont(StandardFonts.HelveticaBold);

  let page = doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
  let y = PAGE_HEIGHT - MARGIN;

  function ensureSpace(needed) {
    if (y - needed < MARGIN) {
      page = doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
      y = PAGE_HEIGHT - MARGIN;
    }
  }

  // Name
  if (resume.name) {
    page.drawText(resume.name, { x: MARGIN, y, size: 18, font: fontBold, color: rgb(0, 0, 0) });
    y -= 24;
  }

  // Contact line
  const contact = [resume.email, resume.phone, resume.linkedin, resume.github].filter(Boolean).join('  |  ');
  if (contact) {
    page.drawText(contact, { x: MARGIN, y, size: 9, font, color: rgb(0.3, 0.3, 0.3) });
    y -= 20;
  }

  // Summary
  if (resume.summary) {
    page.drawText('SUMMARY', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 14;
    y = await drawWrappedText(page, resume.summary, MARGIN, y, font, 10, CONTENT_WIDTH, 13);
    y -= 10;
  }

  // Experience
  if (resume.experience?.length) {
    ensureSpace(30);
    page.drawText('EXPERIENCE', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 16;

    for (const exp of resume.experience) {
      ensureSpace(40);
      const titleLine = `${exp.title || ''} — ${exp.company || ''}`;
      page.drawText(titleLine, { x: MARGIN, y, size: 10, font: fontBold, color: rgb(0, 0, 0) });
      y -= 13;
      if (exp.dates) {
        page.drawText(exp.dates, { x: MARGIN, y, size: 9, font, color: rgb(0.4, 0.4, 0.4) });
        y -= 13;
      }
      for (const bullet of (exp.bullets || [])) {
        ensureSpace(15);
        y = await drawWrappedText(page, `• ${bullet}`, MARGIN + 10, y, font, 9.5, CONTENT_WIDTH - 10, 12);
        y -= 2;
      }
      y -= 8;
    }
  }

  // Skills
  if (resume.skills?.length) {
    ensureSpace(30);
    page.drawText('SKILLS', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 14;
    y = await drawWrappedText(page, resume.skills.join(', '), MARGIN, y, font, 10, CONTENT_WIDTH, 13);
    y -= 10;
  }

  // Education
  if (resume.education?.length) {
    ensureSpace(30);
    page.drawText('EDUCATION', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 14;
    for (const edu of resume.education) {
      ensureSpace(20);
      page.drawText(`${edu.degree || ''} — ${edu.school || ''}`, { x: MARGIN, y, size: 10, font: fontBold, color: rgb(0, 0, 0) });
      y -= 13;
      if (edu.dates) {
        page.drawText(edu.dates, { x: MARGIN, y, size: 9, font, color: rgb(0.4, 0.4, 0.4) });
        y -= 13;
      }
    }
  }

  const bytes = await doc.save();
  const filename = `resume-${sanitize(company)}-${dateStamp()}.pdf`;
  const filepath = join(OUTPUT_DIR, filename);
  await mkdir(OUTPUT_DIR, { recursive: true });
  await writeFile(filepath, bytes);
  return filepath;
}

async function renderCoverLetterPdf(text, company) {
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);

  let page = doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
  let y = PAGE_HEIGHT - MARGIN;

  const paragraphs = text.split(/\n\n+/);
  for (const para of paragraphs) {
    y = await drawWrappedText(page, para.replace(/\n/g, ' '), MARGIN, y, font, 11, CONTENT_WIDTH, 15);
    y -= 12;
    if (y < MARGIN) {
      page = doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
      y = PAGE_HEIGHT - MARGIN;
    }
  }

  const bytes = await doc.save();
  const filename = `coverletter-${sanitize(company)}-${dateStamp()}.pdf`;
  const filepath = join(OUTPUT_DIR, filename);
  await mkdir(OUTPUT_DIR, { recursive: true });
  await writeFile(filepath, bytes);
  return filepath;
}

export async function render(tailoredResume, coverLetterText, companyName) {
  const resumePath = await renderResumePdf(tailoredResume, companyName);
  const coverLetterPath = await renderCoverLetterPdf(coverLetterText, companyName);
  return { resumePath, coverLetterPath };
}
