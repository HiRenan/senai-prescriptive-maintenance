// Generated from apps/api/openapi/v1.json by scripts/generate_web_contract.py.
// Do not edit by hand; run the generator and commit the result.

export interface AnalysisRequest {
  readonly features: AnalysisFeatures;
  readonly top_k?: number;
}

export type AnalysisResponse =
  | NormalAnalysisResult
  | DocumentedFaultAnalysisResult
  | UndocumentedFaultAnalysisResult
  | OutOfDistributionAnalysisResult
  | DegradedAnalysisResult;

export interface ErrorResponse {
  readonly error: ErrorDetail;
}

export interface AnalysisFeatures {
  readonly z_rms_velocity_mm_s: number;
  readonly temperature_c: number;
  readonly x_rms_velocity_mm_s: number;
  readonly z_peak_acceleration_g: number;
  readonly x_peak_acceleration_g: number;
  readonly z_peak_vel_comp_freq_hz: number;
  readonly x_peak_vel_comp_freq_hz: number;
  readonly z_rms_acceleration_g: number;
  readonly x_rms_acceleration_g: number;
  readonly z_kurtosis: number;
  readonly x_kurtosis: number;
  readonly z_crest_factor: number;
  readonly x_crest_factor: number;
  readonly z_peak_velocity_mm_s: number;
  readonly x_peak_velocity_mm_s: number;
  readonly z_high_freq_rms_accel_g: number;
  readonly x_high_freq_rms_accel_g: number;
  readonly rpm: number;
}

export interface NormalAnalysisResult {
  readonly analysis_id: string;
  readonly outcome: "normal";
  readonly diagnosis: Diagnosis;
  readonly support: SufficientSupport;
  readonly abstention: null;
  readonly model_id: string;
  readonly neighbors: readonly OpaqueNeighbor[];
  readonly prescription: null;
  readonly citations: readonly Citation[];
  readonly warnings: readonly AnalysisWarning[];
}

export interface DocumentedFaultAnalysisResult {
  readonly analysis_id: string;
  readonly outcome: "documented_fault";
  readonly diagnosis: Diagnosis;
  readonly support: SufficientSupport;
  readonly abstention: null;
  readonly model_id: string;
  readonly neighbors: readonly OpaqueNeighbor[];
  readonly prescription: Prescription;
  readonly citations: readonly Citation[];
  readonly warnings: readonly AnalysisWarning[];
}

export interface UndocumentedFaultAnalysisResult {
  readonly analysis_id: string;
  readonly outcome: "undocumented_fault";
  readonly diagnosis: Diagnosis;
  readonly support: SufficientSupport;
  readonly abstention: UndocumentedFaultAbstention;
  readonly model_id: string;
  readonly neighbors: readonly OpaqueNeighbor[];
  readonly prescription: null;
  readonly citations: readonly Citation[];
  readonly warnings: readonly AnalysisWarning[];
}

export interface OutOfDistributionAnalysisResult {
  readonly analysis_id: string;
  readonly outcome: "out_of_distribution";
  readonly diagnosis: null;
  readonly support: InsufficientSupport;
  readonly abstention: OutOfDistributionAbstention;
  readonly model_id: string;
  readonly neighbors: readonly OpaqueNeighbor[];
  readonly prescription: null;
  readonly citations: readonly Citation[];
  readonly warnings: readonly AnalysisWarning[];
}

export interface DegradedAnalysisResult {
  readonly analysis_id: string;
  readonly outcome: "degraded";
  readonly diagnosis: Diagnosis;
  readonly support: SufficientSupport;
  readonly abstention: DependencyUnavailableAbstention;
  readonly model_id: string;
  readonly neighbors: readonly OpaqueNeighbor[];
  readonly prescription: null;
  readonly citations: readonly Citation[];
  readonly warnings: readonly AnalysisWarning[];
}

export interface ErrorDetail {
  readonly code: string;
  readonly message: string;
  readonly issues: readonly ValidationIssue[];
}

export interface Diagnosis {
  readonly code: string;
  readonly summary: string;
}

export interface SufficientSupport {
  readonly level: "sufficient";
  readonly support_score: number;
}

export interface OpaqueNeighbor {
  readonly neighbor_ref: string;
  readonly rank: number;
  readonly fault_code: string;
  readonly distance: number;
}

export interface Citation {
  readonly document_id: string;
  readonly document_version: string;
  readonly chunk: string;
  readonly page_number: number;
}

export interface AnalysisWarning {
  readonly code: string;
  readonly message: string;
}

export interface Prescription {
  readonly summary: string;
  readonly priority: PrescriptionPriority;
  readonly actions: readonly string[];
}

export interface UndocumentedFaultAbstention {
  readonly reason: "undocumented_fault";
  readonly message: string;
}

export interface InsufficientSupport {
  readonly level: "insufficient";
  readonly support_score: number;
}

export interface OutOfDistributionAbstention {
  readonly reason: "out_of_distribution";
  readonly message: string;
}

export interface DependencyUnavailableAbstention {
  readonly reason: "dependency_unavailable";
  readonly message: string;
}

export interface ValidationIssue {
  readonly field: string;
  readonly code: string;
}

export type PrescriptionPriority = "routine" | "scheduled" | "urgent";

export type AnalysisOutcome = AnalysisResponse["outcome"];

export interface FeatureField {
  readonly name: keyof AnalysisFeatures;
  readonly title: string;
  readonly minimum: number | null;
  readonly maximum: number | null;
}

export interface OutcomeContract {
  readonly outcome: AnalysisOutcome;
  readonly schema: string;
  readonly hasDiagnosis: boolean;
  readonly hasAbstention: boolean;
  readonly abstentionReason: string | null;
  readonly supportLevel: string;
  readonly prescribes: boolean;
  readonly maxCitations: number;
}

export interface SyntheticAnalysisExample {
  readonly name: string;
  readonly summary: string;
  readonly request: AnalysisRequest;
  readonly response: AnalysisResponse;
}

export declare const API_CONTRACT_VERSION: string;

export declare const ANALYSIS_PATH: string;

export declare const ANALYSIS_SUCCESS_STATUS: number;

export declare const ANALYSIS_STATUSES: readonly number[];

export declare const SUPPORT_SCORE_NOTE: string;

export declare const NEIGHBOR_DISTANCE_NOTE: string;

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
  | { readonly kind: "object"; readonly schema: string };

export interface ObjectSchema {
  readonly required: readonly string[];
  readonly properties: Readonly<Record<string, SchemaNode>>;
}

export declare const RESPONSE_SCHEMAS: Readonly<
  Record<string, ObjectSchema>
>;

export declare const FEATURE_FIELDS: readonly FeatureField[];

export declare const TOP_K: {
  readonly fallback: number;
  readonly minimum: number;
  readonly maximum: number;
};

export declare const ANALYSIS_OUTCOMES: readonly OutcomeContract[];

export declare const PRESCRIPTION_PRIORITIES: readonly PrescriptionPriority[];

export declare const SYNTHETIC_ANALYSIS_EXAMPLES: readonly SyntheticAnalysisExample[];
