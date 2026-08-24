import { useEffect, useRef, useState } from "react";
import { Check, Ellipsis } from "lucide-react";
import { Button } from "../ui/Button";
import { useTheme } from "./useTheme";

interface OverflowMenuProps {
  contractVersion: string;
  documentContractVersion: string;
  modeDescription: string;
  /** Extra caveat shown under the metadata (e.g. token revocation note). */
  note?: string;
}

/**
 * Demoted scaffolding lives here: contract metadata, the mode explanation,
 * operational caveats, and the "follow the system theme" escape hatch. A
 * non-modal popover: Escape closes and returns focus to the trigger; clicks
 * outside dismiss it.
 */
export function OverflowMenu({
  contractVersion,
  documentContractVersion,
  modeDescription,
  note,
}: OverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { source, useSystemTheme } = useTheme();

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      const wrapper = wrapperRef.current;
      if (wrapper !== null && !wrapper.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  return (
    <div
      className="overflow"
      ref={wrapperRef}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          setOpen(false);
          triggerRef.current?.focus();
        }
      }}
    >
      <Button
        ref={triggerRef}
        variant="ghost"
        iconOnly
        aria-label="Sobre a demonstração"
        aria-expanded={open}
        aria-controls="overflow-panel"
        onClick={() => {
          setOpen((current) => !current);
        }}
      >
        <Ellipsis size={18} aria-hidden />
      </Button>
      <div className="overflow-panel" id="overflow-panel" hidden={!open}>
        <p className="overflow-heading">Sobre a demonstração</p>
        <dl className="overflow-meta">
          <div>
            <dt>Contrato de análise</dt>
            <dd className="mono" id="contract-version">
              {contractVersion}
            </dd>
          </div>
          <div>
            <dt>Contrato documental</dt>
            <dd className="mono" id="document-contract-version">
              {documentContractVersion}
            </dd>
          </div>
          <div>
            <dt>Análise</dt>
            <dd className="mono">POST /analysis</dd>
          </div>
          <div>
            <dt>Documentos</dt>
            <dd className="mono">GET · POST /documents</dd>
          </div>
          <div>
            <dt>Registro</dt>
            <dd className="mono">metadados, sem bytes</dd>
          </div>
        </dl>
        <p className="overflow-description">{modeDescription}</p>
        {note !== undefined ? (
          <p className="overflow-note">{note}</p>
        ) : null}
        <div className="overflow-divider" role="presentation" />
        <Button
          variant="quiet"
          className="overflow-action"
          aria-pressed={source === "system"}
          iconStart={
            source === "system" ? <Check size={16} aria-hidden /> : undefined
          }
          onClick={() => {
            useSystemTheme();
          }}
        >
          Usar tema do sistema
        </Button>
      </div>
    </div>
  );
}
