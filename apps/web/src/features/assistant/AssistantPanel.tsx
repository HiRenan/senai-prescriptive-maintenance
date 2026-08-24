import { useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { BookOpenCheck, CornerDownLeft, RotateCcw, Send } from "lucide-react";

import type { AssistantOutput } from "../../api/assistant-client";
import { ASSISTANT_QUESTION } from "../../generated/assistant-contract.js";
import type { AssistantResponse } from "../../generated/assistant-contract.js";
import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";

interface AssistantClient {
  query: (request: { question: string }) => Promise<AssistantOutput>;
}

type Turn = {
  id: number;
  question: string;
  state: "pending" | "complete" | "failed";
  response: AssistantResponse | null;
};

function failureMessage(output: Extract<AssistantOutput, { ok: false }>): string {
  switch (output.failure.kind) {
    case "authentication":
      return "A sessão precisa ser autenticada novamente.";
    case "timeout":
      return "A consulta excedeu o tempo limite.";
    case "validation":
      return output.failure.detail ?? "A pergunta não atende ao contrato.";
    case "unavailable":
      return "O assistente está temporariamente indisponível.";
    case "malformed":
      return "A resposta não corresponde ao contrato publicado.";
    default:
      return "Não foi possível concluir a consulta.";
  }
}

export function AssistantPanel({
  client,
  offline,
  ready,
}: {
  client: AssistantClient;
  offline: boolean;
  ready: boolean;
}) {
  const [question, setQuestion] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);
  const [turns, setTurns] = useState<readonly Turn[]>([]);
  const [failureByTurn, setFailureByTurn] = useState<Readonly<Record<number, string>>>(
    {},
  );
  const nextId = useRef(1);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const busy = turns.some((turn) => turn.state === "pending");

  const run = async (id: number, submittedQuestion: string) => {
    const output = await client.query({ question: submittedQuestion });
    setTurns((current) =>
      current.map((turn) =>
        turn.id !== id
          ? turn
          : output.ok
            ? { ...turn, state: "complete", response: output.response }
            : { ...turn, state: "failed", response: null },
      ),
    );
    setFailureByTurn((current) => {
      const next = { ...current };
      if (output.ok) {
        delete next[id];
      } else {
        next[id] = failureMessage(output);
      }
      return next;
    });
    inputRef.current?.focus();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (offline || !ready || busy) {
      return;
    }
    const normalized = question.normalize("NFC").replace(/\s+/gu, " ").trim();
    if (
      normalized.length < ASSISTANT_QUESTION.minimum ||
      normalized.length > ASSISTANT_QUESTION.maximum
    ) {
      setInputError(
        `Escreva entre ${ASSISTANT_QUESTION.minimum} e ${ASSISTANT_QUESTION.maximum} caracteres.`,
      );
      inputRef.current?.focus();
      return;
    }
    setInputError(null);
    const id = nextId.current++;
    setTurns((current) => [
      ...current,
      { id, question: normalized, state: "pending", response: null },
    ]);
    setQuestion("");
    void run(id, normalized);
  };

  const retry = (turn: Turn) => {
    if (offline || !ready || busy) {
      return;
    }
    setTurns((current) =>
      current.map((item) =>
        item.id === turn.id
          ? { ...item, state: "pending", response: null }
          : item,
      ),
    );
    void run(turn.id, turn.question);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <div className="assistant-shell">
      <header className="assistant-intro">
        <p className="assistant-kicker">Consulta documental determinística</p>
        <h2 className="assistant-heading" id="assistant-heading">
          Assistente fundamentado
        </h2>
        <p className="assistant-intro-text">
          Recupera somente chunks sintéticos aprovados e vigentes. A resposta é
          um trecho literal da fonte; abaixo do limiar, o sistema se abstém.
        </p>
      </header>

      {offline ? (
        <Banner tone="withheld" title="Indisponível no modo offline">
          O assistente não usa resposta simulada. Volte à API local para consultar
          o corpus sintético governado; nenhuma chamada foi feita.
        </Banner>
      ) : (
        <Banner tone="info" title="Demonstração sintética, sem LLM">
          O score mede similaridade lexical TF-IDF, não acurácia nem confiança
          estatística. Toda decisão continua sujeita à revisão humana.
        </Banner>
      )}

      <section
        className="assistant-chat"
        aria-label="Histórico desta página"
        aria-live="polite"
        aria-busy={busy || undefined}
      >
        {turns.length === 0 ? (
          <div className="assistant-empty">
            <BookOpenCheck aria-hidden="true" size={22} />
            <div>
              <h3>Faça uma pergunta sobre o cenário sintético</h3>
              <p>
                Exemplo: “Como verificar vibração radial elevada na bomba?”
              </p>
            </div>
          </div>
        ) : null}

        {turns.map((turn) => (
          <article className="assistant-turn" key={turn.id}>
            <div className="assistant-message assistant-message-user">
              <p className="assistant-speaker">Você</p>
              <p>{turn.question}</p>
            </div>
            <div
              className="assistant-message assistant-message-system"
              data-state={turn.response?.status ?? turn.state}
            >
              <p className="assistant-speaker">Assistente</p>
              {turn.state === "pending" ? (
                <p role="status">Verificando evidências aprovadas…</p>
              ) : turn.state === "failed" ? (
                <>
                  <p>{failureByTurn[turn.id] ?? "A consulta falhou."}</p>
                  <Button
                    size="sm"
                    iconStart={<RotateCcw aria-hidden="true" size={15} />}
                    onClick={() => retry(turn)}
                  >
                    Tentar novamente
                  </Button>
                </>
              ) : turn.response?.status === "answered" ? (
                <>
                  <p className="assistant-answer">{turn.response.answer}</p>
                  <p className="assistant-score">
                    Similaridade {turn.response.score.toFixed(3)} · limiar{" "}
                    {turn.response.threshold.toFixed(2)} ·{" "}
                    <span className="mono">{turn.response.policy_version}</span>
                  </p>
                  <p className="assistant-review">{turn.response.human_review_notice}</p>
                  <div className="assistant-sources">
                    <h3>Fonte recuperável</h3>
                    <ol>
                      {turn.response.citations.map((citation) => (
                        <li key={`${citation.document_id}:${citation.chunk}`}>
                          <span className="mono">{citation.document_id}</span>
                          <span>
                            página {citation.page_number} · chunk{" "}
                            <span className="mono">{citation.chunk}</span>
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                </>
              ) : turn.response?.status === "insufficient_evidence" ? (
                <>
                  <p className="assistant-abstention">{turn.response.message}</p>
                  <p className="assistant-score">
                    {turn.response.max_score === null
                      ? "Nenhuma evidência elegível recebeu score."
                      : `Maior similaridade ${turn.response.max_score.toFixed(3)}.`}{" "}
                    Limiar {turn.response.threshold.toFixed(2)}; nenhuma orientação
                    ou citação foi produzida.
                  </p>
                </>
              ) : null}
            </div>
          </article>
        ))}
      </section>

      <form className="assistant-composer" onSubmit={submit}>
        <label htmlFor="assistant-question">Pergunta</label>
        <textarea
          ref={inputRef}
          className="textarea assistant-textarea"
          id="assistant-question"
          value={question}
          maxLength={ASSISTANT_QUESTION.maximum}
          rows={3}
          disabled={offline || !ready || busy}
          aria-invalid={inputError !== null || undefined}
          aria-describedby="assistant-question-help assistant-question-error"
          placeholder="Pergunte sobre vibração, temperatura ou óleo no cenário sintético…"
          onChange={(event) => {
            setQuestion(event.target.value);
            setInputError(null);
          }}
          onKeyDown={onKeyDown}
        />
        <div className="assistant-composer-meta">
          <p id="assistant-question-help">
            Enter envia; Shift+Enter cria uma nova linha. O histórico existe somente
            na memória desta página.
          </p>
          <span className="mono">
            {question.length}/{ASSISTANT_QUESTION.maximum}
          </span>
        </div>
        <p className="field-error" id="assistant-question-error">
          {inputError}
        </p>
        <Button
          className="assistant-send"
          type="submit"
          variant="primary"
          busy={busy}
          disabled={offline || !ready || question.trim().length === 0}
          iconStart={<Send aria-hidden="true" size={16} />}
        >
          Enviar pergunta
        </Button>
        <span className="assistant-enter-mark" aria-hidden="true">
          <CornerDownLeft size={14} />
        </span>
      </form>
    </div>
  );
}
