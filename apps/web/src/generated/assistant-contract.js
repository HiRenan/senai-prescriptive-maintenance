// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export const ASSISTANT_CONTRACT_VERSION = "1.0.0";

export const ASSISTANT_OPERATION = Object.freeze({
  operationId: "queryAssistant",
  method: "POST",
  path: "/assistant/query",
  successStatus: 200,
  statuses: Object.freeze([200, 422, 503]),
});

export const ASSISTANT_QUESTION = Object.freeze({
  minimum: 3,
  maximum: 500,
});

export const ASSISTANT_VARIANTS = Object.freeze([
  Object.freeze({
    status: "answered",
    schema: "AnsweredAssistantResult",
  }),
  Object.freeze({
    status: "insufficient_evidence",
    schema: "InsufficientEvidenceAssistantResult",
  }),
]);

export const ASSISTANT_SCHEMAS = Object.freeze({
  AnsweredAssistantResult: Object.freeze({
    required: Object.freeze([
      "status",
      "answer",
      "score",
      "threshold",
      "policy_version",
      "citations",
      "human_review_notice",
    ]),
    properties: Object.freeze({
      status: Object.freeze({ kind: "const", value: "answered" }),
      answer: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 2000,
        pattern: null,
      }),
      score: Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
      threshold: Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
      policy_version: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
      }),
      citations: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: 3,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      human_review_notice: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  InsufficientEvidenceAssistantResult: Object.freeze({
    required: Object.freeze([
      "status",
      "message",
      "max_score",
      "threshold",
      "policy_version",
      "citations",
    ]),
    properties: Object.freeze({
      status: Object.freeze({ kind: "const", value: "insufficient_evidence" }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
      max_score: Object.freeze({
        kind: "union",
        options: Object.freeze([
          Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
          Object.freeze({ kind: "null" }),
        ]),
      }),
      threshold: Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
      policy_version: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
      }),
      citations: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 0,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
    }),
  }),
  Citation: Object.freeze({
    required: Object.freeze([
      "document_id",
      "document_version",
      "chunk",
      "page_number",
    ]),
    properties: Object.freeze({
      document_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^doc_[a-z0-9_]{3,64}$",
      }),
      document_version: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^docver_[a-z0-9_]{3,64}$",
      }),
      chunk: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^chunk_[a-z0-9_]{3,64}$",
      }),
      page_number: Object.freeze({ kind: "integer", minimum: 1, maximum: null }),
    }),
  }),
});

export const SYNTHETIC_ASSISTANT_EXAMPLES = Object.freeze([
  Object.freeze({
    name: "answered",
    summary: "Pergunta coberta pelo corpus sintético",
    request: {
      "question": "Como verificar vibração radial elevada na bomba?"
    },
    response: {
      "status": "answered",
      "answer": "DEMONSTRAÇÃO SINTÉTICA — Para o ativo fictício Bomba Aurora-01, vibração radial elevada deve ser verificada por inspeção visual da fixação e por uma nova medição confirmatória antes de qualquer decisão de manutenção.",
      "score": 0.315637518658,
      "threshold": 0.25,
      "policy_version": "assistant-tfidf-cosine.v1",
      "citations": [
        {
          "document_id": "doc_5f4ae47bced2d80285cb4abee2792b5fcbc0fbf9e2788f97519597fadfa48f4e",
          "document_version": "docver_ea56c644ed894b441d165d30cfc0d7664fd77b481f04c2401a80098d20a58ef8",
          "chunk": "chunk_8176e27398e73caaa2459bf81e0fc404cb167e3cfb32ba5dbf2af83b9597f8b5",
          "page_number": 1
        }
      ],
      "human_review_notice": "Demonstração sintética: confirme a evidência e submeta qualquer decisão à revisão humana qualificada."
    },
  }),
  Object.freeze({
    name: "insufficient_evidence",
    summary: "Pergunta fora do corpus sintético",
    request: {
      "question": "Qual é a previsão do tempo para amanhã?"
    },
    response: {
      "status": "insufficient_evidence",
      "message": "Não há evidência aprovada e vigente suficiente para responder com segurança.",
      "max_score": 0.213528746852,
      "threshold": 0.25,
      "policy_version": "assistant-tfidf-cosine.v1",
      "citations": []
    },
  }),
]);
