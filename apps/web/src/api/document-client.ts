import {
  DOCUMENT_ID_PATTERN,
  DOCUMENT_LIST_PROPERTY,
  DOCUMENT_OPERATIONS,
  DOCUMENT_SCHEMAS,
  DOCUMENT_VARIANTS,
} from "../generated/document-contract.js";
import type {
  DocumentOperation,
  DocumentResponse,
  RegisterDocumentRequest,
  ValidationIssue,
} from "../generated/document-contract.js";
import {
  createSchemaMatcher,
  isRecord,
  readErrorEnvelope,
} from "../core/contract-decode";

export interface DocumentFailure {
  kind:
    | "authentication"
    | "network"
    | "timeout"
    | "refused"
    | "validation"
    | "missing"
    | "conflict"
    | "unavailable"
    | "unexpected"
    | "malformed";
  status: number | null;
  detail: string | null;
  issues: readonly ValidationIssue[];
}

export type DocumentOutput<T> =
  | { ok: true; value: T }
  | { ok: false; failure: DocumentFailure };

export const DEFAULT_DOCUMENT_PREFIX = "/api";
export const DEFAULT_TIMEOUT_MS = 15000;

const documentIdExpression = new RegExp(DOCUMENT_ID_PATTERN);
const { matchesNode, matchesSchema } = createSchemaMatcher(DOCUMENT_SCHEMAS);

function failure(
  kind: DocumentFailure["kind"],
  status: number | null,
  detail: string | null,
  issues: readonly ValidationIssue[] = [],
): { ok: false; failure: DocumentFailure } {
  return { ok: false, failure: { kind, status, detail, issues } };
}

/**
 * Accept an identifier only in the frozen public shape the contract publishes.
 * Nothing else may ever reach a request path.
 */
export function isDocumentId(documentId: string): boolean {
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
 */
export function readDocument(body: unknown): DocumentResponse | null {
  if (!isRecord(body) || typeof body.status !== "string") {
    return null;
  }
  const variant = DOCUMENT_VARIANTS.find((entry) => entry.status === body.status);
  if (variant === undefined || !matchesSchema(body, variant.schema)) {
    return null;
  }
  return body as unknown as DocumentResponse;
}

/**
 * Accept a listing only as the closed envelope the contract publishes, with
 * every item decoded as strictly as a single document.
 */
export function readDocumentList(body: unknown): readonly DocumentResponse[] | null {
  if (!matchesSchema(body, "DocumentListResponse") || !isRecord(body)) {
    return null;
  }
  const items = body[DOCUMENT_LIST_PROPERTY];
  if (!Array.isArray(items)) {
    return null;
  }
  const documents = items.map(readDocument);
  return documents.every((entry) => entry !== null)
    ? Object.freeze(documents as DocumentResponse[])
    : null;
}

/**
 * Build the same-origin path of one operation from its published template.
 */
function endpointOf(
  prefix: string,
  operation: DocumentOperation,
  documentId: string | null,
): string | null {
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
 */
export function createDocumentClient(
  options: {
    fetchImpl?: typeof fetch;
    prefix?: string;
    timeoutMs?: number;
  } = {},
): {
  listDocuments: () => Promise<DocumentOutput<readonly DocumentResponse[]>>;
  getDocument: (documentId: string) => Promise<DocumentOutput<DocumentResponse>>;
  registerDocument: (
    request: RegisterDocumentRequest,
  ) => Promise<DocumentOutput<DocumentResponse>>;
  approveDocument: (
    documentId: string,
    note: string | null,
  ) => Promise<DocumentOutput<DocumentResponse>>;
  rejectDocument: (
    documentId: string,
    reason: string,
  ) => Promise<DocumentOutput<DocumentResponse>>;
  reprocessDocument: (documentId: string) => Promise<DocumentOutput<DocumentResponse>>;
} {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const prefix = options.prefix ?? DEFAULT_DOCUMENT_PREFIX;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  /**
   * Run one published operation and decode its answer against the contract.
   */
  async function run(
    operation: DocumentOperation,
    documentId: string | null,
    body: Record<string, unknown> | null,
  ): Promise<DocumentOutput<unknown>> {
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
      let httpResponse: Response;
      try {
        httpResponse = await fetchImpl(endpoint, {
          method: operation.method,
          headers:
            body === null
              ? { accept: "application/json" }
              : { "content-type": "application/json", accept: "application/json" },
          body: body === null ? undefined : JSON.stringify(body),
          credentials: "omit",
          redirect: "manual",
          signal: controller.signal,
        });
      } catch {
        return controller.signal.aborted
          ? failure("timeout", null, null)
          : failure("network", null, null);
      }

      let answer: unknown = null;
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
      if (httpResponse.status === 401 || httpResponse.status === 403) {
        return failure("authentication", httpResponse.status, null);
      }
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

  async function runForDocument(
    operation: DocumentOperation,
    documentId: string | null,
    body: Record<string, unknown> | null,
  ): Promise<DocumentOutput<DocumentResponse>> {
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
    getDocument(documentId: string) {
      return runForDocument(DOCUMENT_OPERATIONS.getDocument, documentId, null);
    },
    registerDocument(request: RegisterDocumentRequest) {
      return runForDocument(DOCUMENT_OPERATIONS.registerDocument, null, { ...request });
    },
    approveDocument(documentId: string, note: string | null) {
      // The contract publishes `note` as optional, so an absent note is sent as
      // an absent member, never as an invented text.
      return runForDocument(
        DOCUMENT_OPERATIONS.approveDocument,
        documentId,
        note === null ? {} : { note },
      );
    },
    rejectDocument(documentId: string, reason: string) {
      return runForDocument(DOCUMENT_OPERATIONS.rejectDocument, documentId, { reason });
    },
    reprocessDocument(documentId: string) {
      return runForDocument(DOCUMENT_OPERATIONS.reprocessDocument, documentId, null);
    },
  };
}
