import { PDFDocument, StandardFonts, PDFName, PDFString, rgb } from 'pdf-lib';
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

  // Contact line — visible text with clickable URI annotations for links
  const contactFields = [
    resume.email ? { text: resume.email, url: `mailto:${resume.email}` } : null,
    resume.phone ? { text: resume.phone, url: null } : null,
    resume.linkedin ? { text: resume.linkedin.replace(/^https?:\/\//, ''), url: resume.linkedin } : null,
    resume.github ? { text: resume.github.replace(/^https?:\/\//, ''), url: resume.github } : null,
    resume.website ? { text: resume.website.replace(/^https?:\/\//, ''), url: resume.website } : null,
  ].filter(Boolean);

  if (contactFields.length) {
    const separator = '  |  ';
    let cx = MARGIN;
    const contactY = y;
    const annotRefs = [];
    for (let i = 0; i < contactFields.length; i++) {
      const { text, url } = contactFields[i];
      const textWidth = font.widthOfTextAtSize(text, 9);
      page.drawText(text, { x: cx, y: contactY, size: 9, font, color: url ? rgb(0.1, 0.1, 0.6) : rgb(0.3, 0.3, 0.3) });
      if (url) {
        const annotRef = doc.context.register(
          doc.context.obj({
            Type: 'Annot',
            Subtype: 'Link',
            Rect: [cx, contactY - 2, cx + textWidth, contactY + 9],
            Border: [0, 0, 0],
            A: { Type: 'Action', S: 'URI', URI: PDFString.of(url) },
          })
        );
        annotRefs.push(annotRef);
      }
      cx += textWidth;
      if (i < contactFields.length - 1) {
        page.drawText(separator, { x: cx, y: contactY, size: 9, font, color: rgb(0.3, 0.3, 0.3) });
        cx += font.widthOfTextAtSize(separator, 9);
      }
    }
    if (annotRefs.length) {
      const existing = page.node.get(PDFName.of('Annots'));
      if (existing) {
        for (const ref of annotRefs) existing.push(ref);
      } else {
        page.node.set(PDFName.of('Annots'), doc.context.obj(annotRefs));
      }
    }
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

  // Projects
  if (resume.projects?.length) {
    ensureSpace(30);
    page.drawText('PROJECTS', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 16;

    for (const proj of resume.projects) {
      ensureSpace(40);
      const nameWidth = fontBold.widthOfTextAtSize(proj.name || '', 10);
      page.drawText(proj.name || '', { x: MARGIN, y, size: 10, font: fontBold, color: rgb(0, 0, 0) });
      if (proj.url) {
        const urlText = `  ${proj.url}`;
        page.drawText(urlText, { x: MARGIN + nameWidth, y, size: 9, font, color: rgb(0.1, 0.1, 0.6) });
      }
      y -= 13;
      if (proj.summary) {
        y = await drawWrappedText(page, proj.summary, MARGIN, y, font, 9.5, CONTENT_WIDTH, 12);
        y -= 2;
      }
      for (const highlight of (proj.highlights || [])) {
        ensureSpace(15);
        y = await drawWrappedText(page, `• ${highlight}`, MARGIN + 10, y, font, 9.5, CONTENT_WIDTH - 10, 12);
        y -= 2;
      }
      y -= 8;
    }
  }

  // Skills — each entry is a category string (e.g. "Languages: JS, TS")
  if (resume.skills?.length) {
    ensureSpace(30);
    page.drawText('SKILLS', { x: MARGIN, y, size: 11, font: fontBold, color: rgb(0, 0, 0) });
    y -= 14;
    for (const skill of resume.skills) {
      ensureSpace(14);
      y = await drawWrappedText(page, skill, MARGIN, y, font, 9.5, CONTENT_WIDTH, 12);
      y -= 2;
    }
    y -= 8;
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
