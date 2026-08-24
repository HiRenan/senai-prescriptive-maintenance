// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export const DOCUMENT_CONTRACT_VERSION = "1.0.0";

export const DOCUMENT_ID_PATTERN = "^doc_[a-z0-9_]{3,64}$";

export const DOCUMENT_LIST_PROPERTY = "items";

export const DOCUMENT_STATUSES = Object.freeze(["received", "processing", "pending_approval", "approved", "rejected", "failed", "superseded"]);

export const DOCUMENT_VARIANTS = Object.freeze([
  Object.freeze({
    status: "received",
    schema: "ReceivedDocument",
    hasDecisionNote: false,
    requiresDecisionNote: false,
    hasFailure: false,
    supersedes: false,
  }),
  Object.freeze({
    status: "processing",
    schema: "ProcessingDocument",
    hasDecisionNote: false,
    requiresDecisionNote: false,
    hasFailure: false,
    supersedes: false,
  }),
  Object.freeze({
    status: "pending_approval",
    schema: "PendingApprovalDocument",
    hasDecisionNote: false,
    requiresDecisionNote: false,
    hasFailure: false,
    supersedes: false,
  }),
  Object.freeze({
    status: "approved",
    schema: "ApprovedDocument",
    hasDecisionNote: true,
    requiresDecisionNote: false,
    hasFailure: false,
    supersedes: false,
  }),
  Object.freeze({
    status: "rejected",
    schema: "RejectedDocument",
    hasDecisionNote: true,
    requiresDecisionNote: true,
    hasFailure: false,
    supersedes: false,
  }),
  Object.freeze({
    status: "failed",
    schema: "FailedDocument",
    hasDecisionNote: false,
    requiresDecisionNote: false,
    hasFailure: true,
    supersedes: false,
  }),
  Object.freeze({
    status: "superseded",
    schema: "SupersededDocument",
    hasDecisionNote: false,
    requiresDecisionNote: false,
    hasFailure: false,
    supersedes: true,
  }),
]);

export const DOCUMENT_OPERATIONS = Object.freeze({
  listDocuments: Object.freeze({
    operationId: "listDocuments",
    method: "GET",
    path: "/documents",
    parameters: Object.freeze([]),
    requestSchema: null,
    successStatus: 200,
    statuses: Object.freeze([200, 503]),
    success: Object.freeze({ kind: "object", schema: "DocumentListResponse" }),
  }),
  registerDocument: Object.freeze({
    operationId: "registerDocument",
    method: "POST",
    path: "/documents",
    parameters: Object.freeze([]),
    requestSchema: "RegisterDocumentRequest",
    successStatus: 201,
    statuses: Object.freeze([201, 409, 422, 503]),
    success: Object.freeze({ kind: "object", schema: "ReceivedDocument" }),
  }),
  getDocument: Object.freeze({
    operationId: "getDocument",
    method: "GET",
    path: "/documents/{document_id}",
    parameters: Object.freeze(["document_id"]),
    requestSchema: null,
    successStatus: 200,
    statuses: Object.freeze([200, 404, 422, 503]),
    success: Object.freeze({
      kind: "union",
      options: Object.freeze([
        Object.freeze({ kind: "object", schema: "ReceivedDocument" }),
        Object.freeze({ kind: "object", schema: "ProcessingDocument" }),
        Object.freeze({ kind: "object", schema: "PendingApprovalDocument" }),
        Object.freeze({ kind: "object", schema: "ApprovedDocument" }),
        Object.freeze({ kind: "object", schema: "RejectedDocument" }),
        Object.freeze({ kind: "object", schema: "FailedDocument" }),
        Object.freeze({ kind: "object", schema: "SupersededDocument" }),
      ]),
    }),
  }),
  approveDocument: Object.freeze({
    operationId: "approveDocument",
    method: "POST",
    path: "/documents/{document_id}/approve",
    parameters: Object.freeze(["document_id"]),
    requestSchema: "ApproveDocumentRequest",
    successStatus: 200,
    statuses: Object.freeze([200, 404, 409, 422, 503]),
    success: Object.freeze({ kind: "object", schema: "ApprovedDocument" }),
  }),
  rejectDocument: Object.freeze({
    operationId: "rejectDocument",
    method: "POST",
    path: "/documents/{document_id}/reject",
    parameters: Object.freeze(["document_id"]),
    requestSchema: "RejectDocumentRequest",
    successStatus: 200,
    statuses: Object.freeze([200, 404, 409, 422, 503]),
    success: Object.freeze({ kind: "object", schema: "RejectedDocument" }),
  }),
  reprocessDocument: Object.freeze({
    operationId: "reprocessDocument",
    method: "POST",
    path: "/documents/{document_id}/reprocess",
    parameters: Object.freeze(["document_id"]),
    requestSchema: null,
    successStatus: 200,
    statuses: Object.freeze([200, 404, 409, 422, 503]),
    success: Object.freeze({ kind: "object", schema: "ProcessingDocument" }),
  }),
});

export const REGISTER_FIELDS = Object.freeze([
  Object.freeze({
    name: "filename",
    title: "Filename",
    required: true,
    node: Object.freeze({
      kind: "string",
      minLength: null,
      maxLength: null,
      pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
    }),
  }),
  Object.freeze({
    name: "media_type",
    title: "Media Type",
    required: true,
    node: Object.freeze({ kind: "const", value: "application/pdf" }),
  }),
  Object.freeze({
    name: "size_bytes",
    title: "Size Bytes",
    required: true,
    node: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
  }),
  Object.freeze({
    name: "sha256",
    title: "Sha256",
    required: true,
    node: Object.freeze({
      kind: "string",
      minLength: null,
      maxLength: null,
      pattern: "^[0-9a-f]{64}$",
    }),
  }),
]);

export const APPROVE_FIELDS = Object.freeze([
  Object.freeze({
    name: "note",
    title: "Note",
    required: false,
    node: Object.freeze({
      kind: "union",
      options: Object.freeze([
        Object.freeze({
          kind: "string",
          minLength: 1,
          maxLength: 500,
          pattern: null,
        }),
        Object.freeze({ kind: "null" }),
      ]),
    }),
  }),
]);

export const REJECT_FIELDS = Object.freeze([
  Object.freeze({
    name: "reason",
    title: "Reason",
    required: true,
    node: Object.freeze({
      kind: "string",
      minLength: 1,
      maxLength: 500,
      pattern: null,
    }),
  }),
]);

export const DOCUMENT_SCHEMAS = Object.freeze({
  DocumentListResponse: Object.freeze({
    required: Object.freeze([
      "items",
    ]),
    properties: Object.freeze({
      items: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: null,
        items: Object.freeze({
          kind: "union",
          options: Object.freeze([
            Object.freeze({ kind: "object", schema: "ReceivedDocument" }),
            Object.freeze({ kind: "object", schema: "ProcessingDocument" }),
            Object.freeze({
              kind: "object",
              schema: "PendingApprovalDocument",
            }),
            Object.freeze({ kind: "object", schema: "ApprovedDocument" }),
            Object.freeze({ kind: "object", schema: "RejectedDocument" }),
            Object.freeze({ kind: "object", schema: "FailedDocument" }),
            Object.freeze({ kind: "object", schema: "SupersededDocument" }),
          ]),
        }),
      }),
    }),
  }),
  RegisterDocumentRequest: Object.freeze({
    required: Object.freeze([
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
    ]),
    properties: Object.freeze({
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
    }),
  }),
  ReceivedDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "received" }),
      decision_note: Object.freeze({ kind: "null" }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  ProcessingDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "processing" }),
      decision_note: Object.freeze({ kind: "null" }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  PendingApprovalDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "pending_approval" }),
      decision_note: Object.freeze({ kind: "null" }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  ApprovedDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "approved" }),
      decision_note: Object.freeze({
        kind: "union",
        options: Object.freeze([
          Object.freeze({
            kind: "string",
            minLength: 1,
            maxLength: 500,
            pattern: null,
          }),
          Object.freeze({ kind: "null" }),
        ]),
      }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  RejectedDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "rejected" }),
      decision_note: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  FailedDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "failed" }),
      decision_note: Object.freeze({ kind: "null" }),
      failure: Object.freeze({ kind: "object", schema: "DocumentFailure" }),
      superseded_by_document_id: Object.freeze({ kind: "null" }),
    }),
  }),
  SupersededDocument: Object.freeze({
    required: Object.freeze([
      "document_id",
      "filename",
      "media_type",
      "size_bytes",
      "sha256",
      "created_at",
      "updated_at",
      "status",
      "decision_note",
      "failure",
      "superseded_by_document_id",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      filename: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\\.[Pp][Dd][Ff]$",
      }),
      media_type: Object.freeze({ kind: "const", value: "application/pdf" }),
      size_bytes: Object.freeze({ kind: "integer", minimum: 1, maximum: 25000000 }),
      sha256: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[0-9a-f]{64}$",
      }),
      created_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      updated_at: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: null,
      }),
      status: Object.freeze({ kind: "const", value: "superseded" }),
      decision_note: Object.freeze({ kind: "null" }),
      failure: Object.freeze({ kind: "null" }),
      superseded_by_document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
    }),
  }),
  ApproveDocumentRequest: Object.freeze({
    required: Object.freeze([
    ]),
    properties: Object.freeze({
      note: Object.freeze({
        kind: "union",
        options: Object.freeze([
          Object.freeze({
            kind: "string",
            minLength: 1,
            maxLength: 500,
            pattern: null,
          }),
          Object.freeze({ kind: "null" }),
        ]),
      }),
    }),
  }),
  RejectDocumentRequest: Object.freeze({
    required: Object.freeze([
      "reason",
    ]),
    properties: Object.freeze({
      reason: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  ErrorResponse: Object.freeze({
    required: Object.freeze([
      "error",
    ]),
    properties: Object.freeze({
      error: Object.freeze({ kind: "object", schema: "ErrorDetail" }),
    }),
  }),
  DocumentFailure: Object.freeze({
    required: Object.freeze([
      "code",
      "message",
    ]),
    properties: Object.freeze({
      code: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 80,
        pattern: null,
      }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  ErrorDetail: Object.freeze({
    required: Object.freeze([
      "code",
      "message",
      "issues",
    ]),
    properties: Object.freeze({
      code: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 80,
        pattern: null,
      }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
      issues: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "ValidationIssue" }),
      }),
    }),
  }),
  ValidationIssue: Object.freeze({
    required: Object.freeze([
      "field",
      "code",
    ]),
    properties: Object.freeze({
      field: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 200,
        pattern: null,
      }),
      code: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 100,
        pattern: null,
      }),
    }),
  }),
});

export const SYNTHETIC_DOCUMENT_EXAMPLES = Object.freeze([
  Object.freeze({
    name: "received",
    summary: "Documento sintético received",
    document: {
      "document_id": "doc_synthetic_received",
      "filename": "received.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "received",
      "decision_note": null,
      "failure": null,
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "processing",
    summary: "Documento sintético processing",
    document: {
      "document_id": "doc_synthetic_processing",
      "filename": "processing.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "processing",
      "decision_note": null,
      "failure": null,
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "pending_approval",
    summary: "Documento sintético pending_approval",
    document: {
      "document_id": "doc_synthetic_pending",
      "filename": "pending.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "pending_approval",
      "decision_note": null,
      "failure": null,
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "approved",
    summary: "Documento sintético approved",
    document: {
      "document_id": "doc_synthetic_manual",
      "filename": "manual.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "approved",
      "decision_note": "Conteúdo inteiramente sintético aprovado para o fake.",
      "failure": null,
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "rejected",
    summary: "Documento sintético rejected",
    document: {
      "document_id": "doc_synthetic_rejected",
      "filename": "rejected.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "rejected",
      "decision_note": "Metadados sintéticos incompletos.",
      "failure": null,
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "failed",
    summary: "Documento sintético failed",
    document: {
      "document_id": "doc_synthetic_failed",
      "filename": "failed.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "failed",
      "decision_note": null,
      "failure": {
        "code": "synthetic_processing_failure",
        "message": "Falha controlada do fake sintético."
      },
      "superseded_by_document_id": null
    },
  }),
  Object.freeze({
    name: "superseded",
    summary: "Documento sintético superseded",
    document: {
      "document_id": "doc_synthetic_superseded",
      "filename": "old.synthetic.pdf",
      "media_type": "application/pdf",
      "size_bytes": 1024,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "created_at": "2030-01-02T03:04:05Z",
      "updated_at": "2030-01-02T03:04:05Z",
      "status": "superseded",
      "decision_note": null,
      "failure": null,
      "superseded_by_document_id": "doc_synthetic_manual"
    },
  }),
]);
