// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export interface AssistantQueryRequest {
  readonly question: string;
}

export type AssistantResponse =
  | AnsweredAssistantResult
  | InsufficientEvidenceAssistantResult;

export interface ErrorResponse {
  readonly error: ErrorDetail;
}

export interface AnsweredAssistantResult {
  readonly status: "answered";
  readonly answer: string;
  readonly score: number;
  readonly threshold: number;
  readonly policy_version: string;
  readonly citations: readonly Citation[];
  readonly human_review_notice: string;
}

export interface InsufficientEvidenceAssistantResult {
  readonly status: "insufficient_evidence";
  readonly message: string;
  readonly max_score: number | null;
  readonly threshold: number;
  readonly policy_version: string;
  readonly citations: readonly Citation[];
}

export interface ErrorDetail {
  readonly code: string;
  readonly message: string;
  readonly issues: readonly ValidationIssue[];
}

export interface Citation {
  readonly document_id: string;
  readonly document_version: string;
  readonly chunk: string;
  readonly page_number: number;
}

export interface ValidationIssue {
  readonly field: string;
  readonly code: string;
}

export type AssistantStatus = AssistantResponse["status"];

export type SchemaNode =
  | { readonly kind: "null" }
  | { readonly kind: "const"; readonly value: string }
  | { readonly kind: "string"; readonly minLength: number | null; readonly maxLength: number | null; readonly pattern: string | null }
  | { readonly kind: "integer" | "number"; readonly minimum: number | null; readonly maximum: number | null }
  | { readonly kind: "array"; readonly items: SchemaNode; readonly minItems: number | null; readonly maxItems: number | null }
  | { readonly kind: "enum"; readonly values: readonly string[] }
  | { readonly kind: "object"; readonly schema: string }
  | { readonly kind: "union"; readonly options: readonly SchemaNode[] };

export interface ObjectSchema { readonly required: readonly string[]; readonly properties: Readonly<Record<string, SchemaNode>>; }

export interface AssistantVariant { readonly status: AssistantStatus; readonly schema: string; }

export interface AssistantOperation { readonly operationId: string; readonly method: "POST"; readonly path: string; readonly successStatus: number; readonly statuses: readonly number[]; }

export interface SyntheticAssistantExample { readonly name: AssistantStatus; readonly summary: string; readonly request: AssistantQueryRequest; readonly response: AssistantResponse; }

export declare const ASSISTANT_CONTRACT_VERSION: string;

export declare const ASSISTANT_OPERATION: AssistantOperation;

export declare const ASSISTANT_QUESTION: { readonly minimum: number; readonly maximum: number; };

export declare const ASSISTANT_VARIANTS: readonly AssistantVariant[];

export declare const ASSISTANT_SCHEMAS: Readonly<Record<string, ObjectSchema>>;

export declare const SYNTHETIC_ASSISTANT_EXAMPLES: readonly SyntheticAssistantExample[];
