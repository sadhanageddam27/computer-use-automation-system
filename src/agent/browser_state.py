"""
Captures the current page as a compact, LLM-readable state.

Design choice: bias toward accessibility semantics (role + accessible name)
over raw DOM structure, since the target environment has no clean DOM and
no test IDs. Each element also gets an ephemeral XPath computed at scan
time purely so THIS agent process can act on it immediately - that XPath
is not what gets persisted into the artifact (see src/artifacts), which
uses the role/name pair as the primary locator with the XPath as a
fallback, per the design write-up.
"""

from __future__ import annotations

from dataclasses import dataclass


ELEMENT_SCAN_JS = """
() => {
  function isVisible(el) {
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();

    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
    }

    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText.trim()) return parentLabel.innerText.trim();

    if (el.tagName === 'INPUT' && el.type === 'submit') return el.value || 'Submit';
    if (el.placeholder) return el.placeholder.trim();
    if (el.innerText && el.innerText.trim()) return el.innerText.trim().slice(0, 80);
    if (el.value) return String(el.value).slice(0, 80);

    // fall back to preceding table cell text - common in nested-table legacy layouts
    const row = el.closest('tr');
    if (row) {
      const cells = Array.from(row.querySelectorAll('td'));
      const idx = cells.findIndex(td => td.contains(el));
      if (idx > 0 && cells[idx - 1].innerText.trim()) {
        return cells[idx - 1].innerText.trim();
      }
    }
    return '';
  }

  function roleOf(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'password') return 'textbox';
      return 'textbox';
    }
    if (tag === 'button') return 'button';
    return tag;
  }

  function xpathOf(el) {
    if (el.id) return `//*[@id="${el.id}"]`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let idx = 1;
      let sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName === node.tagName) idx++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(`${node.tagName.toLowerCase()}[${idx}]`);
      node = node.parentElement;
    }
    return '//' + parts.join('/');
  }

  const selector = 'a[href], button, input:not([type=hidden]), select, textarea';
  const nodes = Array.from(document.querySelectorAll(selector)).filter(isVisible);

  return nodes.map((el, i) => ({
    index: i,
    role: roleOf(el),
    name: accessibleName(el),
    tag: el.tagName.toLowerCase(),
    input_type: el.tagName === 'INPUT' ? (el.type || 'text') : null,
    current_value: 'value' in el ? String(el.value ?? '') : null,
    xpath: xpathOf(el),
  }));
}
"""


@dataclass
class PageElement:
    index: int
    role: str
    name: str
    tag: str
    input_type: str | None
    current_value: str | None
    xpath: str


@dataclass
class PageState:
    url: str
    title: str
    elements: list[PageElement]

    def to_prompt_text(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}", "Interactive elements:"]
        for el in self.elements:
            val = f" value=\"{el.current_value}\"" if el.current_value else ""
            lines.append(f"  [{el.index}] {el.role} \"{el.name}\"{val}")
        return "\n".join(lines)


def get_page_state(page) -> PageState:
    raw_elements = page.evaluate(ELEMENT_SCAN_JS)
    elements = [PageElement(**e) for e in raw_elements]
    return PageState(url=page.url, title=page.title(), elements=elements)
