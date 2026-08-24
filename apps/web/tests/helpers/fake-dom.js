class FakeText {
  /** @param {string} value */
  constructor(value) {
    this.value = value;
    this.parentElement = null;
  }

  get textContent() {
    return this.value;
  }

  set textContent(value) {
    this.value = value;
  }
}

class FakeElement {
  /** @param {string} localName */
  constructor(localName) {
    this.localName = localName;
    this.parentElement = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.value = "";
    this.style = { setProperty() {} };
  }

  get children() {
    return this.childNodes.filter((child) => child instanceof FakeElement);
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.replaceChildren(value === "" ? [] : new FakeText(value));
  }

  /** @param {string} name @param {string} value */
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  /** @param {string} name */
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  /** @param {string} name */
  hasAttribute(name) {
    return this.attributes.has(name);
  }

  /** @param {string} name */
  removeAttribute(name) {
    this.attributes.delete(name);
  }

  /** @param {string} name @param {boolean} force */
  toggleAttribute(name, force) {
    if (force) {
      this.setAttribute(name, "");
      return true;
    }
    this.removeAttribute(name);
    return false;
  }

  /** @param {...any} nodes */
  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? new FakeText(node) : node;
      child.parentElement = this;
      this.childNodes.push(child);
    }
  }

  /** @param {...any} nodes */
  replaceChildren(...nodes) {
    for (const child of this.childNodes) {
      child.parentElement = null;
    }
    this.childNodes = [];
    this.append(...nodes.flat());
  }

  /** @param {string} type @param {(event: any) => void} listener */
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  /** @param {any} event */
  dispatchEvent(event) {
    event.target ??= this;
    event.currentTarget = this;
    event.defaultPrevented ??= false;
    event.preventDefault ??= () => {
      event.defaultPrevented = true;
    };
    for (const listener of this.listeners.get(event.type) ?? []) {
      listener(event);
    }
    return !event.defaultPrevented;
  }

  click() {
    if (!this.hasAttribute("disabled")) {
      this.dispatchEvent({ type: "click" });
    }
  }

  focus() {
    if (!this.hasAttribute("disabled")) {
      globalThis.document.activeElement = this;
    }
  }
}

class FakeDocument {
  constructor() {
    this.activeElement = null;
  }

  /** @param {string} tag */
  createElement(tag) {
    return new FakeElement(tag);
  }

  /** @param {string} _namespace @param {string} tag */
  createElementNS(_namespace, tag) {
    return new FakeElement(tag);
  }

  /** @param {string} value */
  createTextNode(value) {
    return new FakeText(value);
  }
}

/**
 * Install the small DOM surface needed by the document panel and return a
 * cleanup function so tests do not leak globals into other modules.
 */
export function installFakeDom() {
  const previous = {
    document: globalThis.document,
    HTMLElement: globalThis.HTMLElement,
    HTMLInputElement: globalThis.HTMLInputElement,
    HTMLButtonElement: globalThis.HTMLButtonElement,
    HTMLSelectElement: globalThis.HTMLSelectElement,
    HTMLTextAreaElement: globalThis.HTMLTextAreaElement,
    HTMLFormElement: globalThis.HTMLFormElement,
    SVGElement: globalThis.SVGElement,
  };
  globalThis.document = new FakeDocument();
  globalThis.HTMLElement = FakeElement;
  globalThis.HTMLInputElement = FakeElement;
  globalThis.HTMLButtonElement = FakeElement;
  globalThis.HTMLSelectElement = FakeElement;
  globalThis.HTMLTextAreaElement = FakeElement;
  globalThis.HTMLFormElement = FakeElement;
  globalThis.SVGElement = FakeElement;
  return () => {
    Object.assign(globalThis, previous);
  };
}

/** @param {FakeElement} root */
export function descendants(root) {
  const found = [];
  for (const child of root.children) {
    found.push(child, ...descendants(child));
  }
  return found;
}

/**
 * @param {FakeElement} root
 * @param {string} name
 * @param {string} [value]
 */
export function findByAttribute(root, name, value) {
  return (
    descendants(root).find((element) => {
      const actual = element.getAttribute(name);
      return actual !== null && (value === undefined || actual === value);
    }) ?? null
  );
}

/** @param {FakeElement} root @param {string} localName */
export function elementsByName(root, localName) {
  return descendants(root).filter((element) => element.localName === localName);
}

export function deferred() {
  /** @type {(value: any) => void} */
  let resolve;
  const promise = new Promise((fulfil) => {
    resolve = fulfil;
  });
  return { promise, resolve };
}

/** @param {() => boolean} predicate */
export async function waitFor(predicate) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) {
      return;
    }
    await new Promise((fulfil) => setImmediate(fulfil));
  }
  throw new Error("A condição esperada não foi alcançada pelo painel.");
}

export { FakeElement };
