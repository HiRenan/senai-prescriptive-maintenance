import { svg } from "./dom.js";

/**
 * One stroke-only mark per documental state, so the seven states stay distinct
 * for readers who do not perceive the tone colour. The text label always
 * accompanies the mark; nothing is signalled by colour alone.
 *
 * @type {Readonly<Record<string, readonly string[]>>}
 */
const PATHS = Object.freeze({
  received: ["M6 3h9l5 5v13H6z", "M15 3v5h5"],
  processing: ["M12 4a8 8 0 1 0 8 8", "M12 8v4l3 2"],
  pending_approval: ["M12 3a9 9 0 1 0 9 9", "M12 7v5l4 2", "M16.5 3.5 21 3l-.5 4.5"],
  approved: ["M6 3h9l5 5v13H6z", "M15 3v5h5", "M9 14l2.5 2.5L16 12"],
  rejected: ["M6 3h9l5 5v13H6z", "M15 3v5h5", "M9.5 12.5 16 19", "M16 12.5 9.5 19"],
  failed: ["M12 3 2.5 20h19z", "M12 10v4", "M12 17.5h.01"],
  superseded: ["M4 6h10l4 4v11H4z", "M14 6v4h4", "M20 3v6h-6"],
});

/**
 * @param {string} status
 * @returns {SVGElement}
 */
export function documentMark(status) {
  const paths = PATHS[status] ?? PATHS.failed;
  return svg(
    "svg",
    {
      class: "mark",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.75",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
      focusable: "false",
    },
    paths.map((definition) => svg("path", { d: definition })),
  );
}
