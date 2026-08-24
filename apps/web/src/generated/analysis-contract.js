// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export const API_CONTRACT_VERSION = "1.0.0";

export const ANALYSIS_PATH = "/analysis";

export const ANALYSIS_SUCCESS_STATUS = 200;

export const ANALYSIS_STATUSES = Object.freeze([200, 422, 503]);

export const SUPPORT_SCORE_NOTE = "Heurística agregada não calibrada; não representa probabilidade nem confiança estatística.";

export const NEIGHBOR_DISTANCE_NOTE = "Distância padronizada não negativa e sem limite superior.";

export const RESPONSE_SCHEMAS = Object.freeze({
  NormalAnalysisResult: Object.freeze({
    required: Object.freeze([
      "analysis_id",
      "outcome",
      "diagnosis",
      "support",
      "abstention",
      "model_id",
      "neighbors",
      "prescription",
      "citations",
      "warnings",
    ]),
    properties: Object.freeze({
      analysis_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^ana_[a-z0-9_]{3,64}$",
      }),
      outcome: Object.freeze({ kind: "const", value: "normal" }),
      diagnosis: Object.freeze({ kind: "object", schema: "Diagnosis" }),
      support: Object.freeze({ kind: "object", schema: "SufficientSupport" }),
      abstention: Object.freeze({ kind: "null" }),
      model_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^model_[a-z0-9_.-]{3,64}$",
      }),
      neighbors: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "OpaqueNeighbor" }),
      }),
      prescription: Object.freeze({ kind: "null" }),
      citations: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 0,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      warnings: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "AnalysisWarning" }),
      }),
    }),
  }),
  DocumentedFaultAnalysisResult: Object.freeze({
    required: Object.freeze([
      "analysis_id",
      "outcome",
      "diagnosis",
      "support",
      "abstention",
      "model_id",
      "neighbors",
      "prescription",
      "citations",
      "warnings",
    ]),
    properties: Object.freeze({
      analysis_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^ana_[a-z0-9_]{3,64}$",
      }),
      outcome: Object.freeze({ kind: "const", value: "documented_fault" }),
      diagnosis: Object.freeze({ kind: "object", schema: "Diagnosis" }),
      support: Object.freeze({ kind: "object", schema: "SufficientSupport" }),
      abstention: Object.freeze({ kind: "null" }),
      model_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^model_[a-z0-9_.-]{3,64}$",
      }),
      neighbors: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "OpaqueNeighbor" }),
      }),
      prescription: Object.freeze({ kind: "object", schema: "Prescription" }),
      citations: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      warnings: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "AnalysisWarning" }),
      }),
    }),
  }),
  UndocumentedFaultAnalysisResult: Object.freeze({
    required: Object.freeze([
      "analysis_id",
      "outcome",
      "diagnosis",
      "support",
      "abstention",
      "model_id",
      "neighbors",
      "prescription",
      "citations",
      "warnings",
    ]),
    properties: Object.freeze({
      analysis_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^ana_[a-z0-9_]{3,64}$",
      }),
      outcome: Object.freeze({ kind: "const", value: "undocumented_fault" }),
      diagnosis: Object.freeze({ kind: "object", schema: "Diagnosis" }),
      support: Object.freeze({ kind: "object", schema: "SufficientSupport" }),
      abstention: Object.freeze({ kind: "object", schema: "UndocumentedFaultAbstention" }),
      model_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^model_[a-z0-9_.-]{3,64}$",
      }),
      neighbors: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "OpaqueNeighbor" }),
      }),
      prescription: Object.freeze({ kind: "null" }),
      citations: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 0,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      warnings: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "AnalysisWarning" }),
      }),
    }),
  }),
  OutOfDistributionAnalysisResult: Object.freeze({
    required: Object.freeze([
      "analysis_id",
      "outcome",
      "diagnosis",
      "support",
      "abstention",
      "model_id",
      "neighbors",
      "prescription",
      "citations",
      "warnings",
    ]),
    properties: Object.freeze({
      analysis_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^ana_[a-z0-9_]{3,64}$",
      }),
      outcome: Object.freeze({ kind: "const", value: "out_of_distribution" }),
      diagnosis: Object.freeze({ kind: "null" }),
      support: Object.freeze({ kind: "object", schema: "InsufficientSupport" }),
      abstention: Object.freeze({ kind: "object", schema: "OutOfDistributionAbstention" }),
      model_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^model_[a-z0-9_.-]{3,64}$",
      }),
      neighbors: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "OpaqueNeighbor" }),
      }),
      prescription: Object.freeze({ kind: "null" }),
      citations: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 0,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      warnings: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "AnalysisWarning" }),
      }),
    }),
  }),
  DegradedAnalysisResult: Object.freeze({
    required: Object.freeze([
      "analysis_id",
      "outcome",
      "diagnosis",
      "support",
      "abstention",
      "model_id",
      "neighbors",
      "prescription",
      "citations",
      "warnings",
    ]),
    properties: Object.freeze({
      analysis_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^ana_[a-z0-9_]{3,64}$",
      }),
      outcome: Object.freeze({ kind: "const", value: "degraded" }),
      diagnosis: Object.freeze({ kind: "object", schema: "Diagnosis" }),
      support: Object.freeze({ kind: "object", schema: "SufficientSupport" }),
      abstention: Object.freeze({
        kind: "object",
        schema: "DependencyUnavailableAbstention",
      }),
      model_id: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^model_[a-z0-9_.-]{3,64}$",
      }),
      neighbors: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "OpaqueNeighbor" }),
      }),
      prescription: Object.freeze({ kind: "null" }),
      citations: Object.freeze({
        kind: "array",
        minItems: null,
        maxItems: 10,
        items: Object.freeze({ kind: "object", schema: "Citation" }),
      }),
      warnings: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: null,
        items: Object.freeze({ kind: "object", schema: "AnalysisWarning" }),
      }),
    }),
  }),
  Diagnosis: Object.freeze({
    required: Object.freeze([
      "code",
      "summary",
    ]),
    properties: Object.freeze({
      code: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 80,
        pattern: null,
      }),
      summary: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  SufficientSupport: Object.freeze({
    required: Object.freeze([
      "level",
      "support_score",
    ]),
    properties: Object.freeze({
      level: Object.freeze({ kind: "const", value: "sufficient" }),
      support_score: Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
    }),
  }),
  OpaqueNeighbor: Object.freeze({
    required: Object.freeze([
      "neighbor_ref",
      "rank",
      "fault_code",
      "distance",
    ]),
    properties: Object.freeze({
      neighbor_ref: Object.freeze({
        kind: "string",
        minLength: null,
        maxLength: null,
        pattern: "^neighbor_[a-z0-9_]{3,64}$",
      }),
      rank: Object.freeze({ kind: "integer", minimum: 1, maximum: 10 }),
      fault_code: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 80,
        pattern: "^[a-z0-9]+(?:_[a-z0-9]+)*$",
      }),
      distance: Object.freeze({ kind: "number", minimum: 0, maximum: null }),
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
  AnalysisWarning: Object.freeze({
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
  Prescription: Object.freeze({
    required: Object.freeze([
      "summary",
      "priority",
      "actions",
    ]),
    properties: Object.freeze({
      summary: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
      priority: Object.freeze({
        kind: "enum",
        values: ["routine", "scheduled", "urgent"],
      }),
      actions: Object.freeze({
        kind: "array",
        minItems: 1,
        maxItems: 5,
        items: Object.freeze({
          kind: "string",
          minLength: 1,
          maxLength: 300,
          pattern: null,
        }),
      }),
    }),
  }),
  UndocumentedFaultAbstention: Object.freeze({
    required: Object.freeze([
      "reason",
      "message",
    ]),
    properties: Object.freeze({
      reason: Object.freeze({ kind: "const", value: "undocumented_fault" }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  InsufficientSupport: Object.freeze({
    required: Object.freeze([
      "level",
      "support_score",
    ]),
    properties: Object.freeze({
      level: Object.freeze({ kind: "const", value: "insufficient" }),
      support_score: Object.freeze({ kind: "number", minimum: 0, maximum: 1 }),
    }),
  }),
  OutOfDistributionAbstention: Object.freeze({
    required: Object.freeze([
      "reason",
      "message",
    ]),
    properties: Object.freeze({
      reason: Object.freeze({ kind: "const", value: "out_of_distribution" }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
  DependencyUnavailableAbstention: Object.freeze({
    required: Object.freeze([
      "reason",
      "message",
    ]),
    properties: Object.freeze({
      reason: Object.freeze({ kind: "const", value: "dependency_unavailable" }),
      message: Object.freeze({
        kind: "string",
        minLength: 1,
        maxLength: 500,
        pattern: null,
      }),
    }),
  }),
});

export const FEATURE_FIELDS = Object.freeze([
  Object.freeze({
    name: "z_rms_velocity_mm_s",
    title: "Z Rms Velocity Mm S",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "temperature_c",
    title: "Temperature C",
    minimum: -273.15,
    maximum: null,
  }),
  Object.freeze({
    name: "x_rms_velocity_mm_s",
    title: "X Rms Velocity Mm S",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "z_peak_acceleration_g",
    title: "Z Peak Acceleration G",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "x_peak_acceleration_g",
    title: "X Peak Acceleration G",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "z_peak_vel_comp_freq_hz",
    title: "Z Peak Vel Comp Freq Hz",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "x_peak_vel_comp_freq_hz",
    title: "X Peak Vel Comp Freq Hz",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "z_rms_acceleration_g",
    title: "Z Rms Acceleration G",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "x_rms_acceleration_g",
    title: "X Rms Acceleration G",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "z_kurtosis",
    title: "Z Kurtosis",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "x_kurtosis",
    title: "X Kurtosis",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "z_crest_factor",
    title: "Z Crest Factor",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "x_crest_factor",
    title: "X Crest Factor",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "z_peak_velocity_mm_s",
    title: "Z Peak Velocity Mm S",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "x_peak_velocity_mm_s",
    title: "X Peak Velocity Mm S",
    minimum: null,
    maximum: null,
  }),
  Object.freeze({
    name: "z_high_freq_rms_accel_g",
    title: "Z High Freq Rms Accel G",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "x_high_freq_rms_accel_g",
    title: "X High Freq Rms Accel G",
    minimum: 0,
    maximum: null,
  }),
  Object.freeze({
    name: "rpm",
    title: "Rpm",
    minimum: null,
    maximum: null,
  }),
]);

export const TOP_K = Object.freeze({
  fallback: 5,
  minimum: 1,
  maximum: 10,
});

export const ANALYSIS_OUTCOMES = Object.freeze([
  Object.freeze({
    outcome: "normal",
    schema: "NormalAnalysisResult",
    hasDiagnosis: true,
    hasAbstention: false,
    abstentionReason: null,
    supportLevel: "sufficient",
    prescribes: false,
    maxCitations: 0,
  }),
  Object.freeze({
    outcome: "documented_fault",
    schema: "DocumentedFaultAnalysisResult",
    hasDiagnosis: true,
    hasAbstention: false,
    abstentionReason: null,
    supportLevel: "sufficient",
    prescribes: true,
    maxCitations: 10,
  }),
  Object.freeze({
    outcome: "undocumented_fault",
    schema: "UndocumentedFaultAnalysisResult",
    hasDiagnosis: true,
    hasAbstention: true,
    abstentionReason: "undocumented_fault",
    supportLevel: "sufficient",
    prescribes: false,
    maxCitations: 0,
  }),
  Object.freeze({
    outcome: "out_of_distribution",
    schema: "OutOfDistributionAnalysisResult",
    hasDiagnosis: false,
    hasAbstention: true,
    abstentionReason: "out_of_distribution",
    supportLevel: "insufficient",
    prescribes: false,
    maxCitations: 0,
  }),
  Object.freeze({
    outcome: "degraded",
    schema: "DegradedAnalysisResult",
    hasDiagnosis: true,
    hasAbstention: true,
    abstentionReason: "dependency_unavailable",
    supportLevel: "sufficient",
    prescribes: false,
    maxCitations: 10,
  }),
]);

export const PRESCRIPTION_PRIORITIES = Object.freeze([
  "routine",
  "scheduled",
  "urgent",
]);

export const SYNTHETIC_ANALYSIS_EXAMPLES = Object.freeze([
  Object.freeze({
    name: "normal",
    summary: "Entrada sintética normal",
    request: {
      "features": {
        "z_rms_velocity_mm_s": 1.2,
        "temperature_c": 42.0,
        "x_rms_velocity_mm_s": 1.1,
        "z_peak_acceleration_g": 0.3,
        "x_peak_acceleration_g": 0.25,
        "z_peak_vel_comp_freq_hz": 60.0,
        "x_peak_vel_comp_freq_hz": 58.0,
        "z_rms_acceleration_g": 0.08,
        "x_rms_acceleration_g": 0.07,
        "z_kurtosis": 3.1,
        "x_kurtosis": 3.0,
        "z_crest_factor": 1.8,
        "x_crest_factor": 1.7,
        "z_peak_velocity_mm_s": 2.4,
        "x_peak_velocity_mm_s": 2.2,
        "z_high_freq_rms_accel_g": 0.04,
        "x_high_freq_rms_accel_g": 0.03,
        "rpm": 1000.0
      },
      "top_k": 3
    },
    response: {
      "analysis_id": "ana_synthetic_normal",
      "outcome": "normal",
      "diagnosis": {
        "code": "synthetic_normal",
        "summary": "Condição sintética dentro da faixa esperada."
      },
      "support": {
        "level": "sufficient",
        "support_score": 0.98
      },
      "abstention": null,
      "model_id": "model_synthetic_v1",
      "neighbors": [
        {
          "neighbor_ref": "neighbor_synthetic_01",
          "rank": 1,
          "fault_code": "synthetic_normal",
          "distance": 0.4
        },
        {
          "neighbor_ref": "neighbor_synthetic_02",
          "rank": 2,
          "fault_code": "synthetic_normal",
          "distance": 1.3
        },
        {
          "neighbor_ref": "neighbor_synthetic_03",
          "rank": 3,
          "fault_code": "synthetic_normal",
          "distance": 2.2
        }
      ],
      "prescription": null,
      "citations": [],
      "warnings": []
    },
  }),
  Object.freeze({
    name: "documented_fault",
    summary: "Entrada sintética documented_fault",
    request: {
      "features": {
        "z_rms_velocity_mm_s": 1.2,
        "temperature_c": 42.0,
        "x_rms_velocity_mm_s": 1.1,
        "z_peak_acceleration_g": 0.3,
        "x_peak_acceleration_g": 0.25,
        "z_peak_vel_comp_freq_hz": 60.0,
        "x_peak_vel_comp_freq_hz": 58.0,
        "z_rms_acceleration_g": 0.08,
        "x_rms_acceleration_g": 0.07,
        "z_kurtosis": 3.1,
        "x_kurtosis": 3.0,
        "z_crest_factor": 1.8,
        "x_crest_factor": 1.7,
        "z_peak_velocity_mm_s": 2.4,
        "x_peak_velocity_mm_s": 2.2,
        "z_high_freq_rms_accel_g": 0.04,
        "x_high_freq_rms_accel_g": 0.03,
        "rpm": 1100.0
      },
      "top_k": 3
    },
    response: {
      "analysis_id": "ana_synthetic_documented_fault",
      "outcome": "documented_fault",
      "diagnosis": {
        "code": "synthetic_documented_fault",
        "summary": "Falha sintética com documentação aprovada."
      },
      "support": {
        "level": "sufficient",
        "support_score": 0.92
      },
      "abstention": null,
      "model_id": "model_synthetic_v1",
      "neighbors": [
        {
          "neighbor_ref": "neighbor_synthetic_01",
          "rank": 1,
          "fault_code": "synthetic_documented_fault",
          "distance": 0.4
        },
        {
          "neighbor_ref": "neighbor_synthetic_02",
          "rank": 2,
          "fault_code": "synthetic_documented_fault",
          "distance": 1.3
        },
        {
          "neighbor_ref": "neighbor_synthetic_03",
          "rank": 3,
          "fault_code": "synthetic_documented_fault",
          "distance": 2.2
        }
      ],
      "prescription": {
        "summary": "Programar inspeção sintética controlada.",
        "priority": "scheduled",
        "actions": [
          "Confirmar a condição em uma nova leitura sintética.",
          "Revisar o manual sintético citado."
        ]
      },
      "citations": [
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_01",
          "page_number": 1
        },
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_02",
          "page_number": 2
        },
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_03",
          "page_number": 3
        }
      ],
      "warnings": []
    },
  }),
  Object.freeze({
    name: "undocumented_fault",
    summary: "Entrada sintética undocumented_fault",
    request: {
      "features": {
        "z_rms_velocity_mm_s": 1.2,
        "temperature_c": 42.0,
        "x_rms_velocity_mm_s": 1.1,
        "z_peak_acceleration_g": 0.3,
        "x_peak_acceleration_g": 0.25,
        "z_peak_vel_comp_freq_hz": 60.0,
        "x_peak_vel_comp_freq_hz": 58.0,
        "z_rms_acceleration_g": 0.08,
        "x_rms_acceleration_g": 0.07,
        "z_kurtosis": 3.1,
        "x_kurtosis": 3.0,
        "z_crest_factor": 1.8,
        "x_crest_factor": 1.7,
        "z_peak_velocity_mm_s": 2.4,
        "x_peak_velocity_mm_s": 2.2,
        "z_high_freq_rms_accel_g": 0.04,
        "x_high_freq_rms_accel_g": 0.03,
        "rpm": 1200.0
      },
      "top_k": 3
    },
    response: {
      "analysis_id": "ana_synthetic_undocumented_fault",
      "outcome": "undocumented_fault",
      "diagnosis": {
        "code": "synthetic_undocumented_fault",
        "summary": "Falha sintética sem documentação aprovada."
      },
      "support": {
        "level": "sufficient",
        "support_score": 0.79
      },
      "abstention": {
        "reason": "undocumented_fault",
        "message": "Não há documentação suficiente para prescrever uma ação."
      },
      "model_id": "model_synthetic_v1",
      "neighbors": [
        {
          "neighbor_ref": "neighbor_synthetic_01",
          "rank": 1,
          "fault_code": "synthetic_undocumented_fault",
          "distance": 0.4
        },
        {
          "neighbor_ref": "neighbor_synthetic_02",
          "rank": 2,
          "fault_code": "synthetic_undocumented_fault",
          "distance": 1.3
        },
        {
          "neighbor_ref": "neighbor_synthetic_03",
          "rank": 3,
          "fault_code": "synthetic_undocumented_fault",
          "distance": 2.2
        }
      ],
      "prescription": null,
      "citations": [],
      "warnings": [
        {
          "code": "documentation_not_found",
          "message": "O diagnóstico não possui suporte documental aprovado."
        }
      ]
    },
  }),
  Object.freeze({
    name: "out_of_distribution",
    summary: "Entrada sintética out_of_distribution",
    request: {
      "features": {
        "z_rms_velocity_mm_s": 1.2,
        "temperature_c": 42.0,
        "x_rms_velocity_mm_s": 1.1,
        "z_peak_acceleration_g": 0.3,
        "x_peak_acceleration_g": 0.25,
        "z_peak_vel_comp_freq_hz": 60.0,
        "x_peak_vel_comp_freq_hz": 58.0,
        "z_rms_acceleration_g": 0.08,
        "x_rms_acceleration_g": 0.07,
        "z_kurtosis": 3.1,
        "x_kurtosis": 3.0,
        "z_crest_factor": 1.8,
        "x_crest_factor": 1.7,
        "z_peak_velocity_mm_s": 2.4,
        "x_peak_velocity_mm_s": 2.2,
        "z_high_freq_rms_accel_g": 0.04,
        "x_high_freq_rms_accel_g": 0.03,
        "rpm": 1300.0
      },
      "top_k": 3
    },
    response: {
      "analysis_id": "ana_synthetic_out_of_distribution",
      "outcome": "out_of_distribution",
      "diagnosis": null,
      "support": {
        "level": "insufficient",
        "support_score": 0.05
      },
      "abstention": {
        "reason": "out_of_distribution",
        "message": "A entrada sintética está fora da distribuição suportada."
      },
      "model_id": "model_synthetic_v1",
      "neighbors": [
        {
          "neighbor_ref": "neighbor_synthetic_01",
          "rank": 1,
          "fault_code": "synthetic_reference_fault",
          "distance": 1.4
        },
        {
          "neighbor_ref": "neighbor_synthetic_02",
          "rank": 2,
          "fault_code": "synthetic_reference_fault",
          "distance": 2.3
        },
        {
          "neighbor_ref": "neighbor_synthetic_03",
          "rank": 3,
          "fault_code": "synthetic_reference_fault",
          "distance": 3.2
        }
      ],
      "prescription": null,
      "citations": [],
      "warnings": [
        {
          "code": "out_of_distribution",
          "message": "Nenhuma prescrição foi produzida."
        }
      ]
    },
  }),
  Object.freeze({
    name: "degraded",
    summary: "Entrada sintética degraded",
    request: {
      "features": {
        "z_rms_velocity_mm_s": 1.2,
        "temperature_c": 42.0,
        "x_rms_velocity_mm_s": 1.1,
        "z_peak_acceleration_g": 0.3,
        "x_peak_acceleration_g": 0.25,
        "z_peak_vel_comp_freq_hz": 60.0,
        "x_peak_vel_comp_freq_hz": 58.0,
        "z_rms_acceleration_g": 0.08,
        "x_rms_acceleration_g": 0.07,
        "z_kurtosis": 3.1,
        "x_kurtosis": 3.0,
        "z_crest_factor": 1.8,
        "x_crest_factor": 1.7,
        "z_peak_velocity_mm_s": 2.4,
        "x_peak_velocity_mm_s": 2.2,
        "z_high_freq_rms_accel_g": 0.04,
        "x_high_freq_rms_accel_g": 0.03,
        "rpm": 1400.0
      },
      "top_k": 3
    },
    response: {
      "analysis_id": "ana_synthetic_degraded",
      "outcome": "degraded",
      "diagnosis": {
        "code": "synthetic_degraded",
        "summary": "Condição sintética com dependência indisponível."
      },
      "support": {
        "level": "sufficient",
        "support_score": 0.72
      },
      "abstention": {
        "reason": "dependency_unavailable",
        "message": "A análise parcial não permite uma prescrição segura."
      },
      "model_id": "model_synthetic_v1",
      "neighbors": [
        {
          "neighbor_ref": "neighbor_synthetic_01",
          "rank": 1,
          "fault_code": "synthetic_degraded",
          "distance": 0.4
        },
        {
          "neighbor_ref": "neighbor_synthetic_02",
          "rank": 2,
          "fault_code": "synthetic_degraded",
          "distance": 1.3
        },
        {
          "neighbor_ref": "neighbor_synthetic_03",
          "rank": 3,
          "fault_code": "synthetic_degraded",
          "distance": 2.2
        }
      ],
      "prescription": null,
      "citations": [
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_01",
          "page_number": 1
        },
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_02",
          "page_number": 2
        },
        {
          "document_id": "doc_synthetic_manual",
          "document_version": "docver_synthetic_manual_v1",
          "chunk": "chunk_synthetic_manual_03",
          "page_number": 3
        }
      ],
      "warnings": [
        {
          "code": "dependency_unavailable",
          "message": "Recuperação ou geração está temporariamente indisponível."
        }
      ]
    },
  }),
]);
