import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";

import type { DocumentResponse } from "../../generated/document-contract.js";
import type { createDocumentClient } from "../../api/document-client";
import {
  buildRegisterRequest,
  buildRejectReason,
  syntheticRegisterValues,
} from "../../core/document-registration";
import {
  describeRegistration,
  presentDocument,
  presentDocumentFailure,
  presentDocumentList,
} from "../../core/document-presentation";
import type {
  DocumentAction,
  DocumentFailureView,
  DocumentView,
} from "../../core/document-presentation";
import type { ValidationIssue } from "../../core/features";
import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import type { AnnounceOptions } from "../../components/ui/StatusToaster";
import { DocumentCard } from "./DocumentCard";
import { RegisterForm } from "./RegisterForm";

type DocumentClient = ReturnType<typeof createDocumentClient>;

const LEDE =
  "A API v1 registra metadados de um PDF: nome, tipo, tamanho declarado e SHA-256 " +
  "declarado. Ela não recebe o arquivo, não guarda bytes e não confere o hash. " +
  "Registrar nunca aprova: a decisão é humana e aparece como estado do ciclo.";

interface Pending {
  documentId: string;
  action: DocumentAction;
}

interface Feedback {
  failure: DocumentFailureView | null;
  confirmation: string | null;
}

interface DocumentsPanelProps {
  client: DocumentClient;
  offline?: boolean;
  announce: (message: string, options?: AnnounceOptions) => void;
}

/**
 * Governed document management: registration of metadata, the seven published
 * states, per-document reads, and the commands each state accepts. Every
 * command is confirmed explicitly and no state is described from a receipt —
 * it is always read back from the API.
 */
export function DocumentsPanel({
  client,
  offline = false,
  announce,
}: DocumentsPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [documents, setDocuments] = useState<readonly DocumentResponse[]>([]);
  const [listFailure, setListFailure] = useState<DocumentFailureView | null>(
    null,
  );
  const [listLoading, setListLoading] = useState(!offline);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>({
    failure: null,
    confirmation: null,
  });
  const [registerValues, setRegisterValues] = useState<Record<string, string>>(
    {},
  );
  const [registerIssues, setRegisterIssues] = useState<
    readonly ValidationIssue[]
  >([]);
  const [exampleValue, setExampleValue] = useState("");
  const started = useRef(false);

  const disabled = busy || offline;

  const focusCard = useCallback((documentId: string) => {
    const root = rootRef.current;
    if (root === null) {
      return;
    }
    const heading = root.querySelector(
      `[data-card-heading="${documentId}"]`,
    );
    const fallback = root.querySelector("[data-refresh]");
    const target = heading ?? fallback;
    if (target instanceof HTMLElement) {
      target.focus();
    }
  }, []);

  const reportFailure = useCallback(
    (failed: DocumentFailureView) => {
      setFeedback({ failure: failed, confirmation: null });
      announce(`${failed.title}. ${failed.nextStep}`, { tone: "failed" });
    },
    [announce],
  );

  /**
   * Read the list. Returns the failure so callers can keep a confirmation
   * beside it: a command may succeed while the refresh that follows fails.
   */
  const loadDocuments = useCallback(
    async (
      settings: { silent?: boolean } = {},
    ): Promise<{ ok: true } | { ok: false; failure: DocumentFailureView }> => {
      setListLoading(true);
      if (settings.silent !== true) {
        announce("Lendo a lista de documentos.");
      }
      const output = await client.listDocuments();
      setListLoading(false);
      if (!output.ok) {
        const failed = presentDocumentFailure(output.failure);
        setListFailure(failed);
        return { ok: false, failure: failed };
      }
      setListFailure(null);
      setDocuments(output.value);
      if (settings.silent !== true) {
        announce(
          output.value.length === 0
            ? "A API não retornou nenhum documento."
            : `${String(output.value.length)} documento(s) lidos da API.`,
        );
      }
      return { ok: true };
    },
    [announce, client],
  );

  const refresh = useCallback(async () => {
    if (busy || offline) {
      if (offline) {
        announce("Modo offline: a lista documental não foi consultada.");
      }
      return;
    }
    setPending(null);
    setBusy(true);
    try {
      const result = await loadDocuments();
      if (!result.ok) {
        reportFailure(result.failure);
      }
    } finally {
      setBusy(false);
    }
  }, [announce, busy, loadDocuments, offline, reportFailure]);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;
    if (offline) {
      setListLoading(false);
      announce("Modo offline ativo. Nenhuma chamada foi feita à API documental.");
      return;
    }
    setBusy(true);
    void loadDocuments()
      .then((result) => {
        if (!result.ok) {
          reportFailure(result.failure);
        }
      })
      .finally(() => {
        setBusy(false);
      });
  }, [announce, loadDocuments, offline, reportFailure]);

  const cancelPending = (documentId: string) => {
    if (busy) {
      return;
    }
    setPending(null);
    setReasonError(null);
    announce("Ação cancelada. Nada foi enviado para a API.");
    focusCard(documentId);
  };

  const commit = async (
    view: DocumentView,
    action: DocumentAction,
    rawReason: string,
  ) => {
    if (offline) {
      announce("Modo offline: nenhum comando foi enviado à API documental.");
      return;
    }
    let reason = "";
    if (action.needsReason) {
      const parsed = buildRejectReason(rawReason);
      if (!parsed.ok) {
        setReasonError(parsed.issue.message);
        announce(`A rejeição não foi enviada: ${parsed.issue.message}`, {
          tone: "failed",
        });
        const input = rootRef.current?.querySelector(
          `[data-reason="${view.documentId}"]`,
        );
        if (input instanceof HTMLElement) {
          input.focus();
        }
        return;
      }
      reason = parsed.reason;
    }
    setReasonError(null);
    setBusy(true);
    announce(`Enviando ${action.label.toLowerCase()} para a API.`);
    try {
      const output =
        action.name === "approve"
          ? await client.approveDocument(view.documentId, null)
          : action.name === "reject"
            ? await client.rejectDocument(view.documentId, reason)
            : await client.reprocessDocument(view.documentId);
      setPending(null);
      if (!output.ok) {
        reportFailure(presentDocumentFailure(output.failure));
        return;
      }
      const updated = presentDocument(output.value);
      const confirmation =
        `${action.label} registrada. Estado confirmado pela API: ` +
        `${updated.statusLabel}.`;
      const refreshed = await loadDocuments({ silent: true });
      if (!refreshed.ok) {
        setFeedback({ failure: refreshed.failure, confirmation });
        announce(
          `${action.label} concluída, mas a lista não pôde ser atualizada. ` +
            refreshed.failure.nextStep,
          { tone: "withheld" },
        );
        return;
      }
      setFeedback({ failure: null, confirmation });
      announce(
        `${action.label} concluída. O estado atual de ${updated.filename} é ` +
          `${updated.statusLabel}.`,
        { tone: "settled" },
      );
    } finally {
      setBusy(false);
      focusCard(view.documentId);
    }
  };

  const readOne = async (view: DocumentView) => {
    if (offline) {
      announce("Modo offline: nenhum documento foi consultado na API.");
      return;
    }
    setBusy(true);
    announce(`Consultando o estado atual de ${view.filename}.`);
    try {
      const output = await client.getDocument(view.documentId);
      if (!output.ok) {
        reportFailure(presentDocumentFailure(output.failure));
        return;
      }
      const fresh = output.value;
      setDocuments((current) =>
        current.map((entry) =>
          entry.document_id === fresh.document_id ? fresh : entry,
        ),
      );
      const read = presentDocument(fresh);
      setFeedback({
        failure: null,
        confirmation: `Estado lido pela API: ${read.statusLabel}.`,
      });
      announce(
        `Estado atual de ${read.filename}: ${read.statusLabel}. ${read.currency.label}.`,
      );
    } finally {
      setBusy(false);
      focusCard(view.documentId);
    }
  };

  const register = async () => {
    if (offline) {
      announce("Modo offline: nenhum metadado foi enviado à API documental.");
      return;
    }
    const built = buildRegisterRequest(registerValues);
    if (!built.ok) {
      setRegisterIssues(built.issues);
      const first = built.issues[0];
      if (first !== undefined) {
        document.getElementById(`document-${first.field}`)?.focus();
      }
      announce(
        `O registro não foi enviado: ${String(built.issues.length)} campo(s) precisam de correção.`,
        { tone: "failed" },
      );
      return;
    }
    setRegisterIssues([]);
    setPending(null);
    setBusy(true);
    announce("Registrando os metadados na API.");
    let registeredDocumentId: string | null = null;
    try {
      const receipt = await client.registerDocument(built.request);
      if (!receipt.ok) {
        reportFailure(presentDocumentFailure(receipt.failure));
        return;
      }
      registeredDocumentId = receipt.value.document_id;
      // The receipt repeats the original command of this identity, so it does
      // not prove the current state. Read it back before describing that state.
      const current = await client.getDocument(receipt.value.document_id);
      if (!current.ok) {
        const failed = presentDocumentFailure(current.failure);
        const confirmation =
          "Registro de metadados confirmado pela API. O estado atual e a lista " +
          "ainda não puderam ser confirmados.";
        setFeedback({ failure: failed, confirmation });
        announce(
          `Registro confirmado, mas o estado atual não pôde ser lido. ${failed.nextStep}`,
          { tone: "withheld" },
        );
        return;
      }
      const message = describeRegistration(current.value);
      const refreshed = await loadDocuments({ silent: true });
      if (!refreshed.ok) {
        setFeedback({ failure: refreshed.failure, confirmation: message });
        announce(
          `Registro confirmado, mas a lista não pôde ser atualizada. ` +
            refreshed.failure.nextStep,
          { tone: "withheld" },
        );
        return;
      }
      setFeedback({ failure: null, confirmation: message });
      announce(message, { tone: "settled" });
    } finally {
      setBusy(false);
      if (registeredDocumentId !== null) {
        focusCard(registeredDocumentId);
      }
    }
  };

  const list = presentDocumentList(documents);
  const countLabel = offline
    ? "Modo offline: ciclo documental não consultado."
    : listLoading && documents.length === 0
      ? "Carregando documentos."
      : listFailure !== null && documents.length === 0
        ? "A lista não pôde ser lida."
        : listLoading
          ? `Atualizando ${String(list.total)} documento(s).`
          : listFailure !== null
            ? `Falha ao atualizar; ${String(list.total)} documento(s) anterior(es) preservado(s).`
            : list.empty
              ? "Nenhum documento registrado."
              : `${String(list.total)} documento(s) no ciclo.`;

  return (
    <div
      className="documents"
      ref={rootRef}
      aria-busy={busy || (listLoading && !offline) ? "true" : "false"}
    >
      <p className="documents-lede">{LEDE}</p>

      <div className="documents-grid">
        <RegisterForm
          values={registerValues}
          issues={registerIssues}
          disabled={disabled}
          exampleValue={exampleValue}
          onExampleChange={(name) => {
            setExampleValue(name);
            const values = syntheticRegisterValues(name);
            if (values === null) {
              return;
            }
            setRegisterValues({ ...values });
            setRegisterIssues([]);
            announce(
              "Exemplo sintético do contrato carregado nos campos de registro.",
            );
          }}
          onFieldChange={(name, value) => {
            setRegisterValues((current) => ({ ...current, [name]: value }));
            setRegisterIssues((current) =>
              current.filter((issue) => issue.field !== name),
            );
          }}
          onSubmit={() => {
            if (!busy) {
              void register();
            }
          }}
        />

        <div className="documents-main">
          <div className="documents-toolbar">
            <Button
              data-refresh=""
              disabled={disabled}
              iconStart={<RotateCcw size={16} aria-hidden />}
              onClick={() => {
                void refresh();
              }}
            >
              Atualizar lista
            </Button>
            <p className="documents-count">{countLabel}</p>
          </div>

          {feedback.failure === null && feedback.confirmation === null ? null : (
            <div
              className="document-feedback"
              data-tone={
                feedback.failure === null
                  ? "settled"
                  : feedback.confirmation === null
                    ? "failed"
                    : "withheld"
              }
            >
              {feedback.confirmation === null ? null : (
                <div className="document-feedback-confirmation">
                  <p className="document-feedback-title">Comando confirmado</p>
                  <p className="document-feedback-text">
                    {feedback.confirmation}
                  </p>
                </div>
              )}
              {feedback.failure === null ? null : (
                <div className="document-feedback-failure">
                  <p className="document-feedback-title">
                    {feedback.failure.title}
                  </p>
                  <p className="document-feedback-text">
                    {feedback.failure.statement}
                  </p>
                  {feedback.failure.status === null ? null : (
                    <p className="document-feedback-status mono">
                      {`Status HTTP ${String(feedback.failure.status)}`}
                    </p>
                  )}
                  {feedback.failure.detail === null ? null : (
                    <p className="document-feedback-detail">
                      {`Motivo informado pela API: ${feedback.failure.detail}`}
                    </p>
                  )}
                  {feedback.failure.issues.length === 0 ? null : (
                    <ul className="issues">
                      {feedback.failure.issues.map((issue) => (
                        <li className="issue" key={`${issue.label}:${issue.code}`}>
                          <span>{issue.label}</span>
                          <span className="issue-code mono">{issue.code}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="document-feedback-next">
                    {feedback.failure.nextStep}
                  </p>
                </div>
              )}
            </div>
          )}

          <div
            className="document-list"
            aria-busy={listLoading && !offline ? "true" : "false"}
          >
            {offline ? (
              <div className="documents-empty">
                <p className="loading-title">
                  Gestão documental indisponível offline
                </p>
                <p>
                  O modo offline demonstra somente os cinco outcomes de análise e
                  não simula decisões documentais.
                </p>
                <p className="documents-empty-next">
                  Próximo passo: volte ao modo API local para consultar ou
                  alterar o ciclo documental.
                </p>
              </div>
            ) : listLoading && documents.length === 0 ? (
              <div className="documents-loading">
                <p className="loading-kicker overline">Consultando a API</p>
                <p className="loading-title">Lendo o ciclo documental</p>
                <Skeleton lines={["85%", "100%", "55%"]} />
              </div>
            ) : listFailure !== null && documents.length === 0 ? (
              <div className="documents-empty">
                <p>A API não forneceu uma lista válida nesta tentativa.</p>
                <p className="documents-empty-next">
                  Próximo passo: use Atualizar lista depois de conferir a saúde
                  da API.
                </p>
              </div>
            ) : (
              <>
                {(listLoading || listFailure !== null) && !list.empty ? (
                  <Banner tone="withheld" className="documents-stale">
                    <p>
                      {listLoading
                        ? "Os documentos abaixo vêm da última leitura concluída enquanto a atualização está em andamento."
                        : "Os documentos abaixo vêm da última leitura concluída e podem estar desatualizados."}
                    </p>
                  </Banner>
                ) : null}
                {list.empty ? (
                  <p className="documents-empty">{list.emptyMessage}</p>
                ) : (
                  list.items.map((view) => (
                    <DocumentCard
                      key={view.documentId}
                      view={view}
                      confirming={
                        pending !== null && pending.documentId === view.documentId
                          ? pending.action
                          : null
                      }
                      disabled={disabled}
                      reasonError={reasonError}
                      onRead={() => {
                        if (!busy && !offline) {
                          void readOne(view);
                        }
                      }}
                      onStartAction={(action) => {
                        if (busy || offline) {
                          return;
                        }
                        setPending({ documentId: view.documentId, action });
                        setReasonError(null);
                        announce(
                          `${action.label}: confirme ou cancele para continuar.`,
                        );
                      }}
                      onConfirm={(reason) => {
                        if (!busy && !offline && pending !== null) {
                          void commit(view, pending.action, reason);
                        }
                      }}
                      onCancel={() => {
                        if (!busy && !offline) {
                          cancelPending(view.documentId);
                        }
                      }}
                      onReasonInput={() => {
                        setReasonError(null);
                      }}
                    />
                  ))
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
