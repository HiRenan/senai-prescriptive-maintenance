// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export interface RegisterDocumentRequest {
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
}

export interface ApproveDocumentRequest {
  readonly note?: string | null;
}

export interface RejectDocumentRequest {
  readonly reason: string;
}

export type DocumentResponse =
  | ReceivedDocument
  | ProcessingDocument
  | PendingApprovalDocument
  | ApprovedDocument
  | RejectedDocument
  | FailedDocument
  | SupersededDocument;

export interface DocumentListResponse {
  readonly items: readonly (ReceivedDocument | ProcessingDocument | PendingApprovalDocument | ApprovedDocument | RejectedDocument | FailedDocument | SupersededDocument)[];
}

export interface ErrorResponse {
  readonly error: ErrorDetail;
}

export interface ReceivedDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "received";
  readonly decision_note: null;
  readonly failure: null;
  readonly superseded_by_document_id: null;
}

export interface ProcessingDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "processing";
  readonly decision_note: null;
  readonly failure: null;
  readonly superseded_by_document_id: null;
}

export interface PendingApprovalDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "pending_approval";
  readonly decision_note: null;
  readonly failure: null;
  readonly superseded_by_document_id: null;
}

export interface ApprovedDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "approved";
  readonly decision_note: string | null;
  readonly failure: null;
  readonly superseded_by_document_id: null;
}

export interface RejectedDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "rejected";
  readonly decision_note: string;
  readonly failure: null;
  readonly superseded_by_document_id: null;
}

export interface FailedDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "failed";
  readonly decision_note: null;
  readonly failure: DocumentFailure;
  readonly superseded_by_document_id: null;
}

export interface SupersededDocument {
  readonly document_id: string;
  readonly filename: string;
  readonly media_type: "application/pdf";
  readonly size_bytes: number;
  readonly sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly status: "superseded";
  readonly decision_note: null;
  readonly failure: null;
  readonly superseded_by_document_id: string;
}

export interface ErrorDetail {
  readonly code: string;
  readonly message: string;
  readonly issues: readonly ValidationIssue[];
}

export interface DocumentFailure {
  readonly code: string;
  readonly message: string;
}

export interface ValidationIssue {
  readonly field: string;
  readonly code: string;
}

export type DocumentStatus = DocumentResponse["status"];

export interface DocumentVariant {
  readonly status: DocumentStatus;
  readonly schema: string;
  readonly hasDecisionNote: boolean;
  readonly requiresDecisionNote: boolean;
  readonly hasFailure: boolean;
  readonly supersedes: boolean;
}

export type SchemaNode =
  | { readonly kind: "null" }
  | { readonly kind: "const"; readonly value: string }
  | {
      readonly kind: "string";
      readonly minLength: number | null;
      readonly maxLength: number | null;
      readonly pattern: string | null;
    }
  | {
      readonly kind: "integer" | "number";
      readonly minimum: number | null;
      readonly maximum: number | null;
    }
  | {
      readonly kind: "array";
      readonly items: SchemaNode;
      readonly minItems: number | null;
      readonly maxItems: number | null;
    }
  | { readonly kind: "enum"; readonly values: readonly string[] }
  | { readonly kind: "object"; readonly schema: string }
  | { readonly kind: "union"; readonly options: readonly SchemaNode[] };

export interface ObjectSchema {
  readonly required: readonly string[];
  readonly properties: Readonly<Record<string, SchemaNode>>;
}

export interface DocumentOperation {
  readonly operationId: string;
  readonly method: "GET" | "POST";
  readonly path: string;
  readonly parameters: readonly string[];
  readonly requestSchema: string | null;
  readonly successStatus: number;
  readonly statuses: readonly number[];
  readonly success: SchemaNode;
}

export interface DocumentOperations {
  readonly listDocuments: DocumentOperation;
  readonly registerDocument: DocumentOperation;
  readonly getDocument: DocumentOperation;
  readonly approveDocument: DocumentOperation;
  readonly rejectDocument: DocumentOperation;
  readonly reprocessDocument: DocumentOperation;
}

export interface DocumentRequestField {
  readonly name: string;
  readonly title: string;
  readonly required: boolean;
  readonly node: SchemaNode;
}

export interface SyntheticDocumentExample {
  readonly name: DocumentStatus;
  readonly summary: string;
  readonly document: DocumentResponse;
}

export declare const DOCUMENT_CONTRACT_VERSION: string;

export declare const DOCUMENT_ID_PATTERN: string;

export declare const DOCUMENT_LIST_PROPERTY: string;

export declare const DOCUMENT_STATUSES: readonly DocumentStatus[];

export declare const DOCUMENT_VARIANTS: readonly DocumentVariant[];

export declare const DOCUMENT_OPERATIONS: DocumentOperations;

export declare const REGISTER_FIELDS: readonly DocumentRequestField[];

export declare const APPROVE_FIELDS: readonly DocumentRequestField[];

export declare const REJECT_FIELDS: readonly DocumentRequestField[];

export declare const DOCUMENT_SCHEMAS: Readonly<
  Record<string, ObjectSchema>
>;

export declare const SYNTHETIC_DOCUMENT_EXAMPLES: readonly SyntheticDocumentExample[];
