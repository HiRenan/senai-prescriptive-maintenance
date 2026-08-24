import { StrictMode } from "react";
import type { CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import { FileSearch, RotateCcw } from "lucide-react";

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/primitives.css";
import "./styles/shell.css";

import { AppShell } from "./components/app/AppShell";
import { StatusProvider, useStatus } from "./components/ui/StatusToaster";
import { Badge } from "./components/ui/Badge";
import { Banner } from "./components/ui/Banner";
import { Button } from "./components/ui/Button";
import { Card } from "./components/ui/Card";
import { Disclosure } from "./components/ui/Disclosure";
import { EmptyState } from "./components/ui/EmptyState";
import { Field } from "./components/ui/Field";
import { Skeleton } from "./components/ui/Skeleton";
import { Tile, TileRow } from "./components/ui/Tile";
import { ToneMark } from "./components/ui/marks";
import { DocumentMark } from "./components/ui/document-marks";

const TONES = [
  { tone: "settled", label: "Normal" },
  { tone: "prescribed", label: "Falha documentada" },
  { tone: "withheld", label: "Falha não documentada" },
  { tone: "outside", label: "Fora de distribuição" },
  { tone: "degraded", label: "Degradado" },
  { tone: "failed", label: "Falha da análise" },
] as const;

const STATUSES = [
  { status: "received", label: "Recebido" },
  { status: "processing", label: "Processando" },
  { status: "pending_approval", label: "Aguardando aprovação" },
  { status: "approved", label: "Aprovado" },
  { status: "rejected", label: "Rejeitado" },
  { status: "failed", label: "Falhou" },
  { status: "superseded", label: "Substituído" },
] as const;

// Milestone 3 gate artifact: exercises every primitive in both themes and at
// every viewport. Replaced by the real analysis workspace in milestone 4.
function PrimitivesGallery() {
  const { announce } = useStatus();
  return (
    <div style={{ display: "grid", gap: "1.5rem" }}>
      <Card as="section" aria-label="Botões">
        <h2 style={{ marginBottom: "1rem" }}>Botões</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          <Button variant="primary">Executar análise</Button>
          <Button>Limpar</Button>
          <Button variant="ghost">Detalhes</Button>
          <Button variant="danger">Rejeitar</Button>
          <Button variant="primary" busy>
            Analisando
          </Button>
          <Button disabled>Indisponível</Button>
          <Button size="sm" iconStart={<RotateCcw size={14} aria-hidden />}>
            Reprocessar
          </Button>
        </div>
      </Card>

      <Card as="section" aria-label="Tons e estados">
        <h2 style={{ marginBottom: "1rem" }}>Tons do laudo</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {TONES.map(({ tone, label }) => (
            <Badge key={tone} tone={tone} mark={<ToneMark tone={tone} size={14} />}>
              {label}
            </Badge>
          ))}
        </div>
        <h2 style={{ margin: "1.5rem 0 1rem" }}>Estados documentais</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {STATUSES.map(({ status, label }) => (
            <Badge
              key={status}
              status={status}
              mark={<DocumentMark status={status} size={14} />}
            >
              {label}
            </Badge>
          ))}
        </div>
      </Card>

      <Card as="section" aria-label="Avisos">
        <h2 style={{ marginBottom: "1rem" }}>Avisos</h2>
        <div style={{ display: "grid", gap: "0.75rem" }}>
          <Banner tone="settled" title="Documento aprovado">
            <p>O documento passou a sustentar novas citações.</p>
          </Banner>
          <Banner
            tone="failed"
            title="A análise falhou"
            action={<Button size="sm">Tentar novamente</Button>}
          >
            <p>O laudo anterior permanece exibido abaixo.</p>
          </Banner>
        </div>
      </Card>

      <Card as="section" aria-label="Campos">
        <h2 style={{ marginBottom: "1rem" }}>Campos</h2>
        <div style={{ display: "grid", gap: "1rem", maxWidth: "24rem" }}>
          <Field id="g-topk" label="Vizinhos solicitados" hint="Entre 1 e 10.">
            {(aria) => (
              <input
                id={aria.inputId}
                className="input"
                type="number"
                inputMode="numeric"
                defaultValue={3}
                aria-describedby={aria.describedBy}
              />
            )}
          </Field>
          <Field
            id="g-rpm"
            label="Rotação"
            error="Informe um número finito."
          >
            {(aria) => (
              <span
                className="input-affix"
                style={{ "--affix-width": "2.5rem" } as CSSProperties}
              >
                <input
                  id={aria.inputId}
                  className="input mono"
                  aria-describedby={aria.describedBy}
                  aria-invalid={aria.invalid}
                  defaultValue="mil"
                />
                <span className="input-affix-unit">rpm</span>
              </span>
            )}
          </Field>
          <Field id="g-example" label="Exemplo sintético">
            {(aria) => (
              <select id={aria.inputId} className="select" aria-describedby={aria.describedBy}>
                <option>Selecione um exemplo</option>
                <option>Leitura normal</option>
              </select>
            )}
          </Field>
        </div>
      </Card>

      <Card as="section" aria-label="Camadas e carregamento">
        <h2 style={{ marginBottom: "1rem" }}>Camadas</h2>
        <TileRow label="Metadados do laudo">
          <Tile label="Contrato" value="v1" mono />
          <Tile label="Modelo" value="knn-prescritivo" mono />
          <Tile label="Vizinhos" value="3" mono />
        </TileRow>
        <div style={{ marginTop: "1rem" }}>
          <Disclosure summary="Como o laudo é montado">
            <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)" }}>
              A decisão segue o contrato público e nunca inventa um desfecho.
            </p>
          </Disclosure>
        </div>
        <div style={{ marginTop: "1rem", maxWidth: "20rem" }}>
          <Skeleton />
        </div>
      </Card>

      <Card as="section" aria-label="Estados vazios e avisos transitórios">
        <EmptyState
          mark={<FileSearch size={20} aria-hidden />}
          tone="neutral"
          title="Nenhum laudo ainda"
          description="Importe um exemplo sintético e execute a análise para ver o resultado aqui."
          action={<Button variant="primary">Executar análise</Button>}
        />
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.5rem",
            justifyContent: "center",
          }}
        >
          <Button
            size="sm"
            onClick={() => {
              announce("Análise concluída: leitura dentro do padrão.", {
                tone: "settled",
              });
            }}
          >
            Toast de sucesso
          </Button>
          <Button
            size="sm"
            onClick={() => {
              announce("A análise falhou. O laudo anterior foi preservado.", {
                tone: "failed",
              });
            }}
          >
            Toast de erro
          </Button>
          <Button
            size="sm"
            onClick={() => {
              announce("Processando a leitura enviada.");
            }}
          >
            Toast neutro
          </Button>
        </div>
      </Card>
    </div>
  );
}

function DocumentsPlaceholder() {
  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <header>
        <h2 id="documents-heading">Gestão documental</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>
          Sete estados do contrato, decisões humanas explícitas, vigência
          rastreável e falhas sanitizadas.
        </p>
      </header>
      <Card>
        <EmptyState
          mark={<DocumentMark status="received" size={20} />}
          tone="neutral"
          title="Painel em migração"
          description="A gestão documental chega no próximo marco da renovação."
        />
      </Card>
    </div>
  );
}

function App() {
  const offline =
    new URLSearchParams(window.location.search).get("mode") === "offline";
  return (
    <StatusProvider>
      <AppShell
        mode={offline ? "offline" : "online"}
        contractVersion="v1"
        documentContractVersion="v1"
        analysis={<PrimitivesGallery />}
        documents={<DocumentsPlaceholder />}
      />
    </StatusProvider>
  );
}

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Elemento raiz ausente no documento.");
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
