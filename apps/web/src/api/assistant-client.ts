import {
  ASSISTANT_OPERATION,
  ASSISTANT_SCHEMAS,
  ASSISTANT_VARIANTS,
} from "../generated/assistant-contract.js";
import type {
  AssistantQueryRequest,
  AssistantResponse,
  ValidationIssue,
} from "../generated/assistant-contract.js";
import {
  createSchemaMatcher,
  isRecord,
  readErrorEnvelope,
} from "../core/contract-decode";

export interface AssistantFailure {
  kind:
    | "authentication"
    | "network"
    | "timeout"
    | "validation"
    | "unavailable"
    | "unexpected"
    | "malformed";
  status: number | null;
  detail: string | null;
  issues: readonly ValidationIssue[];
}

export type AssistantOutput =
  | { ok: true; response: AssistantResponse }
  | { ok: false; failure: AssistantFailure };

export const DEFAULT_ASSISTANT_ENDPOINT = `/api${ASSISTANT_OPERATION.path}`;
const DEFAULT_TIMEOUT_MS = 15_000;
const { matchesSchema } = createSchemaMatcher(ASSISTANT_SCHEMAS);

function failure(
  kind: AssistantFailure["kind"],
  status: number | null,
  detail: string | null,
  issues: readonly ValidationIssue[] = [],
): AssistantOutput {
  return { ok: false, failure: { kind, status, detail, issues } };
}

export function readAssistantResponse(body: unknown): AssistantResponse | null {
  if (!isRecord(body) || typeof body.status !== "string") {
    return null;
  }
  const variant = ASSISTANT_VARIANTS.find((item) => item.status === body.status);
  if (variant === undefined || !matchesSchema(body, variant.schema)) {
    return null;
  }
  return body as unknown as AssistantResponse;
}

export function createAssistantClient(
  options: {
    fetchImpl?: typeof fetch;
    endpoint?: string;
    timeoutMs?: number;
  } = {},
): { query: (request: AssistantQueryRequest) => Promise<AssistantOutput> } {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const endpoint = options.endpoint ?? DEFAULT_ASSISTANT_ENDPOINT;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  async function query(request: AssistantQueryRequest): Promise<AssistantOutput> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      let response: Response;
      try {
        response = await fetchImpl(endpoint, {
          method: ASSISTANT_OPERATION.method,
          headers: {
            accept: "application/json",
            "content-type": "application/json",
          },
          body: JSON.stringify(request),
          credentials: "omit",
          redirect: "manual",
          signal: controller.signal,
        });
      } catch {
        return failure(controller.signal.aborted ? "timeout" : "network", null, null);
      }

      let body: unknown;
      try {
        body = await response.json();
      } catch {
        return failure(
          controller.signal.aborted ? "timeout" : "malformed",
          response.status,
          null,
        );
      }
      if (controller.signal.aborted) {
        return failure("timeout", null, null);
      }
      if (response.status === ASSISTANT_OPERATION.successStatus) {
        const decoded = readAssistantResponse(body);
        return decoded === null
          ? failure("malformed", response.status, null)
          : { ok: true, response: decoded };
      }
      if (response.status === 422) {
        const envelope = readErrorEnvelope(body);
        return failure("validation", 422, envelope.detail, envelope.issues);
      }
      if (response.status === 401 || response.status === 403) {
        return failure("authentication", response.status, null);
      }
      if (response.status === 503) {
        const envelope = readErrorEnvelope(body);
        return failure("unavailable", 503, envelope.detail, envelope.issues);
      }
      if (response.status === 502 || response.status === 504) {
        return failure("network", response.status, null);
      }
      return failure("unexpected", response.status, null);
    } finally {
      clearTimeout(timer);
    }
  }

  return { query };
}
