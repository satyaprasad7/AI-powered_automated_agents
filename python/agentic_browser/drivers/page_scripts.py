"""In-page scripts shared by every driver.

They are plain JS expression strings so they evaluate identically under Playwright
(page.evaluate) and Selenium (execute_script("return " + script)).

Interactive elements get a stable `data-agent-ref` attribute; the agent refers to
elements by that ref, and drivers turn it into a CSS selector.
"""
from __future__ import annotations

import json
import re

MAX_ELEMENTS = 150
MAX_TEXT_CHARS = 4000


def ref_selector(ref: str) -> str:
    if not re.fullmatch(r"\d+", ref):
        raise ValueError(f'Invalid element ref "{ref}" - use a ref number from the latest snapshot')
    return f'[data-agent-ref="{ref}"]'


HELPERS = r"""
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
"""


def _js(value: str) -> str:
    return json.dumps(value)


def snapshot_script() -> str:
    return (
        r"""(() => {
    __HELPERS__
    window.__agentRefSeq = window.__agentRefSeq || 0;
    const elements = [];
    for (const el of document.querySelectorAll(INTERACTIVE)) {
      if (!isVisible(el)) continue;
      if (!el.hasAttribute('data-agent-ref')) el.setAttribute('data-agent-ref', String(++window.__agentRefSeq));
      elements.push(infoOf(el));
      if (elements.length >= __MAX_ELEMENTS__) break;
    }
    const fullText = (document.body && document.body.innerText || '').replace(/\n{3,}/g, '\n\n');
    return {
      url: location.href,
      title: document.title,
      elements,
      text: fullText.slice(0, __MAX_TEXT__),
      truncated: fullText.length > __MAX_TEXT__ || elements.length >= __MAX_ELEMENTS__,
    };
  })()"""
        .replace("__HELPERS__", HELPERS)
        .replace("__MAX_ELEMENTS__", str(MAX_ELEMENTS))
        .replace("__MAX_TEXT__", str(MAX_TEXT_CHARS))
    )


def describe_script(ref: str) -> str:
    return f"(() => {{ {HELPERS} const el = byRef({_js(ref)}); return el ? infoOf(el) : null; }})()"


def get_text_script(ref: str) -> str:
    return (
        """(() => {
    __HELPERS__
    const ref = __REF__;
    const el = ref === 'page' ? document.body : byRef(ref);
    if (!el) throw new Error('No element with ref ' + ref);
    return (el.innerText || el.value || '').slice(0, 20000);
  })()"""
        .replace("__HELPERS__", HELPERS)
        .replace("__REF__", _js(ref))
    )


def extract_table_script(ref: str) -> str:
    return (
        """(() => {
    __HELPERS__
    const ref = __REF__;
    const root = ref === 'page' ? document : byRef(ref);
    if (!root) throw new Error('No element with ref ' + ref);
    const table = root.tagName === 'TABLE' ? root : root.querySelector('table');
    if (!table) throw new Error('No <table> found at ref ' + ref);
    return Array.from(table.rows).map((row) => Array.from(row.cells).map((c) => clean(c.innerText, 500)));
  })()"""
        .replace("__HELPERS__", HELPERS)
        .replace("__REF__", _js(ref))
    )


def resolve_option_script(ref: str, value_or_label: str) -> str:
    """Resolves an option value or visible label to the option's value (or null)."""
    return (
        """(() => {
    __HELPERS__
    const el = byRef(__REF__);
    if (!el || el.tagName !== 'SELECT') throw new Error('Ref ' + __REF__ + ' is not a <select>');
    const want = __WANT__.trim().toLowerCase();
    const opt = Array.from(el.options).find((o) => o.value.toLowerCase() === want || clean(o.text).toLowerCase() === want);
    return opt ? opt.value : null;
  })()"""
        .replace("__HELPERS__", HELPERS)
        .replace("__REF__", _js(ref))
        .replace("__WANT__", _js(value_or_label))
    )


def audit_script() -> str:
    """Deterministic page facts for accessibility / content / link checks."""
    return (
        """(() => {
    __HELPERS__
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
  })()"""
        .replace("__HELPERS__", HELPERS)
    )


def text_present_script(text: str) -> str:
    return f"document.body && document.body.innerText.includes({_js(text)})"
