const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

/**
 * @typedef {Node | string | null} Renderable
 */

/**
 * Build one element with attributes and children.
 *
 * Values are always assigned as attributes or text, never as markup, so no
 * response field can reach the DOM as HTML.
 *
 * @param {string} tag
 * @param {Readonly<Record<string, string | number | boolean | null>>} [attributes]
 * @param {readonly Renderable[]} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  applyAttributes(node, attributes);
  appendChildren(node, children);
  return node;
}

/**
 * @param {string} tag
 * @param {Readonly<Record<string, string | number | boolean | null>>} [attributes]
 * @param {readonly Renderable[]} [children]
 * @returns {SVGElement}
 */
export function svg(tag, attributes = {}, children = []) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  applyAttributes(node, attributes);
  appendChildren(node, children);
  return node;
}

/**
 * @param {Element} node
 * @param {Readonly<Record<string, string | number | boolean | null>>} attributes
 * @returns {void}
 */
function applyAttributes(node, attributes) {
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === false) {
      continue;
    }
    if (value === true) {
      node.setAttribute(name, "");
      continue;
    }
    node.setAttribute(name, String(value));
  }
}

/**
 * @param {Element} node
 * @param {readonly Renderable[]} children
 * @returns {void}
 */
function appendChildren(node, children) {
  for (const child of children) {
    if (child === null) {
      continue;
    }
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

/**
 * @param {Element} node
 * @returns {void}
 */
export function clear(node) {
  node.replaceChildren();
}

/**
 * Set a custom property through the CSSOM so no inline style attribute is
 * parsed, which keeps the strict content security policy satisfied.
 *
 * @param {HTMLElement} node
 * @param {string} property
 * @param {string} value
 * @returns {void}
 */
export function setCustomProperty(node, property, value) {
  node.style.setProperty(property, value);
}

/**
 * @param {string} id
 * @returns {HTMLElement}
 */
export function requireElement(id) {
  const node = document.getElementById(id);
  if (node === null) {
    throw new Error(`O elemento #${id} não existe no documento.`);
  }
  return node;
}
