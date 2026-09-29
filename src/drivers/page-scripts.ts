/**
 * In-page scripts shared by both drivers. They are plain JS strings rather
 * than TS functions so they serialize identically under Playwright and
 * Puppeteer (and are not affected by transpiler helpers such as `__name`).
 *
 * Interactive elements get a stable `data-agent-ref` attribute; the agent
 * refers to elements by that ref, and drivers turn it into a CSS selector.
 */

export const MAX_ELEMENTS = 150;
export const MAX_TEXT_CHARS = 4000;

export function refSelector(ref: string): string {
  if (!/^\d+$/.test(ref)) throw new Error(`Invalid element ref "${ref}" - use a ref number from the latest snapshot`);
  return `[data-agent-ref="${ref}"]`;
}

const HELPERS = String.raw`
  const INTERACTIVE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],' +
    '[role=option],[role=switch],[contenteditable=true],[onclick]';
  const clean = (s, n = 100) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map((id) => (document.getElementById(id) || {}).innerText || '').join(' ');
      if (t.trim()) return t;
    }
    if (el.labels && el.labels.length) return Array.from(el.labels).map((l) => l.innerText).join(' ');
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) return el.value;
    return el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') ||
      el.getAttribute('alt') || el.getAttribute('name') || '';
  };
  const errorOf = (el) => {
    if (el.getAttribute('aria-invalid') !== 'true') return undefined;
    const id = el.getAttribute('aria-errormessage') || el.getAttribute('aria-describedby');
    const msg = id ? clean((document.getElementById(id) || {}).innerText) : '';
    return msg || 'invalid';
  };
  const infoOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const info = { ref: el.getAttribute('data-agent-ref'), tag, name: clean(labelOf(el)) };
    const role = el.getAttribute('role');
    if (role) info.role = role;
    if (tag === 'input') info.type = el.type;
    if (['input', 'textarea', 'select'].includes(tag) && el.type !== 'password' && el.value) info.value = clean(el.value, 200);
    if (el.type === 'password' && el.value) info.value = '********';
    if (el.type === 'checkbox' || el.type === 'radio') info.checked = el.checked;
    if (el.disabled) info.disabled = true;
    if (el.required) info.required = true;
    const invalid = errorOf(el);
    if (invalid) info.invalid = invalid;
    if (tag === 'a') info.href = el.getAttribute('href');
    if (tag === 'select') info.options = Array.from(el.options).slice(0, 25).map((o) => clean(o.text, 60));
    if (!info.name && el.getAttribute('placeholder')) info.name = clean(el.getAttribute('placeholder'));
    return info;
  };
  const byRef = (ref) => document.querySelector('[data-agent-ref="' + ref + '"]');
`;

export function snapshotScript(): string {
  return `(() => {
    ${HELPERS}
    window.__agentRefSeq = window.__agentRefSeq || 0;
    const elements = [];
    for (const el of document.querySelectorAll(INTERACTIVE)) {
      if (!isVisible(el)) continue;
      if (!el.hasAttribute('data-agent-ref')) el.setAttribute('data-agent-ref', String(++window.__agentRefSeq));
      elements.push(infoOf(el));
      if (elements.length >= ${MAX_ELEMENTS}) break;
    }
    const fullText = (document.body && document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n');
    return {
      url: location.href,
      title: document.title,
      elements,
      text: fullText.slice(0, ${MAX_TEXT_CHARS}),
      truncated: fullText.length > ${MAX_TEXT_CHARS} || elements.length >= ${MAX_ELEMENTS},
    };
  })()`;
}

export function describeScript(ref: string): string {
  return `(() => { ${HELPERS} const el = byRef(${JSON.stringify(ref)}); return el ? infoOf(el) : null; })()`;
}

export function getTextScript(ref: string): string {
  return `(() => {
    ${HELPERS}
    const ref = ${JSON.stringify(ref)};
    const el = ref === 'page' ? document.body : byRef(ref);
    if (!el) throw new Error('No element with ref ' + ref);
    return (el.innerText || el.value || '').slice(0, 20000);
  })()`;
}

export function extractTableScript(ref: string): string {
  return `(() => {
    ${HELPERS}
    const ref = ${JSON.stringify(ref)};
    const root = ref === 'page' ? document : byRef(ref);
    if (!root) throw new Error('No element with ref ' + ref);
    const table = root.tagName === 'TABLE' ? root : root.querySelector('table');
    if (!table) throw new Error('No <table> found at ref ' + ref);
    return Array.from(table.rows).map((row) => Array.from(row.cells).map((c) => clean(c.innerText, 500)));
  })()`;
}

/** Resolves an option value or visible label to the option's value (or null). */
export function resolveOptionScript(ref: string, valueOrLabel: string): string {
  return `(() => {
    ${HELPERS}
    const el = byRef(${JSON.stringify(ref)});
    if (!el || el.tagName !== 'SELECT') throw new Error('Ref ${ref} is not a <select>');
    const want = ${JSON.stringify(valueOrLabel)}.trim().toLowerCase();
    const opt = Array.from(el.options).find((o) => o.value.toLowerCase() === want || clean(o.text).toLowerCase() === want);
    return opt ? opt.value : null;
  })()`;
}

/** Deterministic page facts for accessibility / content / link checks. */
export function auditScript(): string {
  return `(() => {
    ${HELPERS}
    const all = (s) => Array.from(document.querySelectorAll(s));
    const sample = (arr, n = 8) => arr.slice(0, n);
    const imgs = all('img');
    const controls = all('input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea').filter(isVisible);
    const ids = all('[id]').map((e) => e.id);
    const dupIds = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))];
    const links = all('a[href]');
    const internal = [...new Set(links.map((a) => a.href).filter((h) => h.startsWith(location.origin) && !h.includes('#')))];
    return {
      url: location.href,
      title: document.title,
      lang: document.documentElement.lang || null,
      metaDescription: (document.querySelector('meta[name=description]') || {}).content || null,
      hasViewportMeta: !!document.querySelector('meta[name=viewport]'),
      headings: sample(all('h1,h2,h3,h4,h5,h6').map((h) => h.tagName + ': ' + clean(h.innerText, 80)), 30),
      h1Count: all('h1').length,
      imagesTotal: imgs.length,
      imagesMissingAlt: sample(imgs.filter((i) => !i.hasAttribute('alt')).map((i) => i.currentSrc || i.src)),
      brokenImages: sample(imgs.filter((i) => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src)).map((i) => i.currentSrc || i.src)),
      formControlsWithoutLabel: sample(controls.filter((c) => !clean(labelOf(c)) || clean(labelOf(c)) === c.getAttribute('name')).map((c) => c.outerHTML.slice(0, 120))),
      linksWithoutName: sample(links.filter((a) => isVisible(a) && !clean(labelOf(a))).map((a) => a.getAttribute('href'))),
      buttonsWithoutName: sample(all('button,[role=button]').filter((b) => isVisible(b) && !clean(labelOf(b))).map((b) => b.outerHTML.slice(0, 120))),
      duplicateIds: sample(dupIds),
      forms: all('form').length,
      internalLinks: sample(internal, 40),
      externalLinkCount: links.filter((a) => a.href.startsWith('http') && !a.href.startsWith(location.origin)).length,
    };
  })()`;
}

export function textPresentScript(text: string): string {
  return `document.body && document.body.innerText.includes(${JSON.stringify(text)})`;
}
