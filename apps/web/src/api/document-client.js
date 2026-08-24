/**
 * @typedef {import("../generated/document-contract.js").DocumentResponse} DocumentResponse
 * @typedef {import("../generated/document-contract.js").DocumentOperation} DocumentOperation
 * @typedef {import("../generated/document-contract.js").RegisterDocumentRequest} RegisterDocumentRequest
 * @typedef {import("../generated/document-contract.js").ValidationIssue} ValidationIssue
 */

import {
  DOCUMENT_ID_PATTERN,
  DOCUMENT_LIST_PROPERTY,
  DOCUMENT_OPERATIONS,
  DOCUMENT_SCHEMAS,
  DOCUMENT_VARIANTS,
} from "../generated/document-contract.js";
import {
  createSchemaMatcher,
  isRecord,
  readErrorEnvelope,
} from "../core/contract-decode.js";

/**
 * @typedef {object} DocumentFailure
 * @property {"network" | "timeout" | "refused" | "validation" | "missing" | "conflict"
 *   | "unavailable" | "unexpected" | "malformed"} kind
 * @property {number | null} status
 * @property {string | null} detail
 * @property {readonly ValidationIssue[]} issues
 */

/**
 * @template T
 * @typedef {{ ok: true, value: T } | { ok: false, failure: DocumentFailure }} DocumentOutput
 */

export const DEFAULT_DOCUMENT_PREFIX = "/api";
export const DEFAULT_TIMEOUT_MS = 15000;

const documentIdExpression = new RegExp(DOCUMENT_ID_PATTERN);
const { matchesNode, matchesSchema } = createSchemaMatcher(DOCUMENT_SCHEMAS);

/**
 * @param {DocumentFailure["kind"]} kind
 * @param {number | null} status
 * @param {string | null} detail
 * @param {readonly ValidationIssue[]} [issues]
 * @returns {{ ok: false, failure: DocumentFailure }}
 */
function failure(kind, status, detail, issues = []) {
  return { ok: false, failure: { kind, status, detail, issues } };
}

/**
 * Accept an identifier only in the frozen public shape the contract publishes.
 * Nothing else may ever reach a request path.
 *
 * @param {string} documentId
 * @returns {boolean}
 */
export function isDocumentId(documentId) {
  const match = documentIdExpression.exec(documentId);
  return match !== null && match[0] === documentId;
}

/**
 * Accept a document only when it is exactly one variant of the published union.
 *
 * The status selects its own variant of the generated schema table, and the
 * body is then decoded against it: every declared member present, no member the
 * variant does not declare, constants, published patterns and bounds. A state
 * the contract cannot produce never reaches the interface.
 *
 * @param {unknown} body
 * @returns {DocumentResponse | null}
 */
export function readDocument(body) {
  if (!isRecord(body) || typeof body.status !== "string") {
    return null;
  }
  const variant = DOCUMENT_VARIANTS.find((entry) => entry.status === body.status);
  if (variant === undefined || !matchesSchema(body, variant.schema)) {
    return null;
  }
  return /** @type {DocumentResponse} */ (/** @type {unknown} */ (body));
}

/**
 * Accept a listing only as the closed envelope the contract publishes, with
 * every item decoded as strictly as a single document.
 *
 * @param {unknown} body
 * @returns {readonly DocumentResponse[] | null}
 */
export function readDocumentList(body) {
  if (!matchesSchema(body, "DocumentListResponse") || !isRecord(body)) {
    return null;
  }
  const items = body[DOCUMENT_LIST_PROPERTY];
  if (!Array.isArray(items)) {
    return null;
  }
  const documents = items.map(readDocument);
  return documents.every((entry) => entry !== null)
    ? Object.freeze(/** @type {readonly DocumentResponse[]} */ (documents))
    : null;
}

/**
 * Build the same-origin path of one operation from its published template.
 *
 * @param {string} prefix
 * @param {DocumentOperation} operation
 * @param {string | null} documentId
 * @returns {string | null}
 */
function endpointOf(prefix, operation, documentId) {
  if (operation.parameters.length === 0) {
    return documentId === null ? `${prefix}${operation.path}` : null;
  }
  if (documentId === null || !isDocumentId(documentId)) {
    return null;
  }
  return `${prefix}${operation.path.replace("{document_id}", documentId)}`;
}

/**
 * Create the client the panel uses for the documental cycle.
 *
 * @param {object} [options]
 * @param {typeof fetch} [options.fetchImpl]
 * @param {string} [options.prefix]
 * @param {number} [options.timeoutMs]
 * @returns {{
 *   listDocuments: () => Promise<DocumentOutput<readonly DocumentResponse[]>>,
 *   getDocument: (documentId: string) => Promise<DocumentOutput<DocumentResponse>>,
 *   registerDocument: (
 *     request: RegisterDocumentRequest,
 *   ) => Promise<DocumentOutput<DocumentResponse>>,
 *   approveDocument: (
 *     documentId: string,
 *     note: string | null,
 *   ) => Promise<DocumentOutput<DocumentResponse>>,
 *   rejectDocument: (
 *     documentId: string,
 *     reason: string,
 *   ) => Promise<DocumentOutput<DocumentResponse>>,
 *   reprocessDocument: (documentId: string) => Promise<DocumentOutput<DocumentResponse>>,
 * }}
 */
export function createDocumentClient(options = {}) {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const prefix = options.prefix ?? DEFAULT_DOCUMENT_PREFIX;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  /**
   * Run one published operation and decode its answer against the contract.
   *
   * @param {DocumentOperation} operation
   * @param {string | null} documentId
   * @param {Record<string, unknown> | null} body
   * @returns {Promise<DocumentOutput<unknown>>}
   */
  async function run(operation, documentId, body) {
    const endpoint = endpointOf(prefix, operation, documentId);
    if (endpoint === null) {
      // An identifier outside the published shape is refused here, before a
      // path is ever built from it.
      return failure("refused", null, null);
    }
    if (operation.requestSchema !== null) {
      if (body === null || !matchesSchema(body, operation.requestSchema)) {
        return failure("refused", null, null);
      }
    } else if (body !== null) {
      return failure("refused", null, null);
    }

    const controller = new AbortController();
    // One deadline covers the whole exchange, reading the body included: an
    // answer that starts and never ends would hang the panel just as silence.
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      /** @type {Response} */
      let httpResponse;
      try {
        httpResponse = await fetchImpl(endpoint, {
          method: operation.method,
          headers:
            body === null
              ? { accept: "application/json" }
              : { "content-type": "application/json", accept: "application/json" },
          body: body === null ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
      } catch {
        return controller.signal.aborted
          ? failure("timeout", null, null)
          : failure("network", null, null);
      }

      /** @type {unknown} */
      let answer = null;
      let parsed = true;
      try {
        answer = await httpResponse.json();
      } catch {
        if (controller.signal.aborted) {
          return failure("timeout", null, null);
        }
        parsed = false;
      }

      if (httpResponse.status === operation.successStatus) {
        return parsed && matchesNode(answer, operation.success)
          ? { ok: true, value: answer }
          : failure("malformed", httpResponse.status, null);
      }
      const envelope = readErrorEnvelope(answer);
      if (httpResponse.status === 404 && operation.statuses.includes(404)) {
        return failure("missing", 404, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 409 && operation.statuses.includes(409)) {
        return failure("conflict", 409, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 422 && operation.statuses.includes(422)) {
        return failure("validation", 422, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 503 && operation.statuses.includes(503)) {
        return failure("unavailable", 503, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 502 || httpResponse.status === 504) {
        // The page and the API share an origin through the web process, so a
        // gateway status means the API itself was never reached.
        return failure("network", httpResponse.status, null);
      }
      // Every other status is outside the operation, `2xx` included.
      return failure("unexpected", httpResponse.status, null);
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * @param {DocumentOperation} operation
   * @param {string | null} documentId
   * @param {Record<string, unknown> | null} body
   * @returns {Promise<DocumentOutput<DocumentResponse>>}
   */
  async function runForDocument(operation, documentId, body) {
    const output = await run(operation, documentId, body);
    if (!output.ok) {
      return output;
    }
    const document = readDocument(output.value);
    return document === null
      ? failure("malformed", operation.successStatus, null)
      : { ok: true, value: document };
  }

  return {
    async listDocuments() {
      const output = await run(DOCUMENT_OPERATIONS.listDocuments, null, null);
      if (!output.ok) {
        return output;
      }
      const documents = readDocumentList(output.value);
      return documents === null
        ? failure("malformed", DOCUMENT_OPERATIONS.listDocuments.successStatus, null)
        : { ok: true, value: documents };
    },
    getDocument(documentId) {
      return runForDocument(DOCUMENT_OPERATIONS.getDocument, documentId, null);
    },
    registerDocument(request) {
      return runForDocument(DOCUMENT_OPERATIONS.registerDocument, null, { ...request });
    },
    approveDocument(documentId, note) {
      // The contract publishes `note` as optional, so an absent note is sent as
      // an absent member, never as an invented text.
      return runForDocument(
        DOCUMENT_OPERATIONS.approveDocument,
        documentId,
        note === null ? {} : { note },
      );
    },
    rejectDocument(documentId, reason) {
      return runForDocument(DOCUMENT_OPERATIONS.rejectDocument, documentId, { reason });
    },
    reprocessDocument(documentId) {
      return runForDocument(DOCUMENT_OPERATIONS.reprocessDocument, documentId, null);
    },
  };
}
