const DEFAULT_NOTE =
  "Entradas e metadados de exemplo vêm do contrato público e são " +
  "inteiramente sintéticos; nenhum PDF é lido ou enviado pelo painel.";

/**
 * One-line colophon. Operational caveats beyond the synthetic-data note live
 * in the overflow panel, not here.
 */
export function Footer({ note = DEFAULT_NOTE }: { note?: string }) {
  return (
    <footer className="app-footer">
      <p className="app-footer-note">{note}</p>
      <p className="app-footer-credit">Demonstração SENAI · Renan Mocelin</p>
    </footer>
  );
}
