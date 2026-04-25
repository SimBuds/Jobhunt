import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { validateTailoredBullets } from './tailor.js';

// Originals drawn from base-resume.json Custom Jewelry Brand entry
const ORIGINALS = [
  'Built and maintained a 14+ page Shopify storefront with 200+ product SKUs serving 500+ monthly visitors, migrating from WordPress through three project phases over 2+ years',
  'Designed and shipped an interactive ring builder that lets customers configure stone, band, and size options directly, replacing the legacy manual quote request workflow',
  'Developed custom Liquid templates, integrated Stripe payments, and own all ongoing feature development, bug fixes, and client communication as sole developer',
];

describe('validateTailoredBullets', () => {
  it('accepts a legitimate reorder (high similarity to original)', () => {
    // Same bullet, slightly reworded — should pass
    const proposed = [
      'Built and maintained a 14+ page Shopify storefront with 200+ product SKUs serving 500+ monthly visitors, migrated from WordPress across three project phases over 2+ years',
      'Designed and shipped an interactive ring builder allowing customers to configure stone, band, and size options, replacing the legacy manual quote workflow',
      'Developed custom Liquid templates, integrated Stripe payments, and own all ongoing feature development, bug fixes, and client communication',
    ];
    const { accepted, rejected } = validateTailoredBullets(ORIGINALS, proposed);
    assert.equal(rejected.length, 0, `Expected 0 rejections, got: ${JSON.stringify(rejected)}`);
    assert.equal(accepted.length, 3);
  });

  it('rejects a hallucinated bullet with low similarity to all originals', () => {
    const proposed = [
      ...ORIGINALS,
      'Led cross-functional engineering teams to drive agile velocity and deliver synergistic stakeholder value', // invented
    ];
    const { accepted, rejected } = validateTailoredBullets(ORIGINALS, proposed);
    assert.equal(rejected.length, 1, `Expected 1 rejection, got: ${JSON.stringify(rejected)}`);
    assert.ok(rejected[0].includes('synergistic'), 'Rejected bullet should be the hallucinated one');
  });

  it('rejects a heavily rewritten bullet that diverges well below 0.6 Jaccard', () => {
    // A bullet that swaps most of the content — different domain, different action, different outcome.
    // Note: Jaccard at sentence level cannot detect single-number inflation (e.g. 500 -> 5,000)
    // because the surrounding 30+ words still match. This is a documented limitation.
    const proposed = [
      ORIGINALS[0],
      ORIGINALS[1],
      // Third bullet rewritten with mostly different vocabulary
      'Architected scalable microservices using React and GraphQL to optimize distributed API workflows for enterprise clients, reducing latency by 60% and boosting conversion KPIs',
    ];
    const { accepted, rejected } = validateTailoredBullets(ORIGINALS, proposed);
    assert.equal(rejected.length, 1, `Expected 1 rejection, got: ${JSON.stringify(rejected)}`);
    assert.ok(rejected[0].includes('microservices'), 'Rejected bullet should be the heavily rewritten one');
  });
});
