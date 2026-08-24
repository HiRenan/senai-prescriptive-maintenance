import { readFileSync } from "node:fs";
import { join } from "node:path";

const SNAPSHOT = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "api",
  "openapi",
  "v1.json",
);

/**
 * The frozen contract snapshot, the only source of fixtures used by the web
 * tests. Its examples are entirely synthetic, so no original material can ever
 * reach the suite.
 */
export const snapshot = JSON.parse(readFileSync(SNAPSHOT, "utf-8"));

const operation = snapshot.paths["/analysis"].post;

export const requestExamples =
  operation.requestBody.content["application/json"].examples;

export const responseExamples =
  operation.responses["200"].content["application/json"].examples;

/**
 * @param {string} name
 * @returns {any}
 */
export function responseExample(name) {
  const example = responseExamples[name];
  if (example === undefined) {
    throw new Error(`O snapshot não declara o exemplo de resposta ${name}.`);
  }
  return structuredClone(example.value);
}

/**
 * @param {string} name
 * @returns {any}
 */
export function requestExample(name) {
  const example = requestExamples[name];
  if (example === undefined) {
    throw new Error(`O snapshot não declara o exemplo de requisição ${name}.`);
  }
  return structuredClone(example.value);
}

export const outcomeNames = Object.keys(responseExamples);

const documentItemOperation =
  snapshot.paths["/documents/{document_id}"].get;

export const documentResponseExamples =
  documentItemOperation.responses["200"].content["application/json"].examples;

export const documentStatusNames = Object.keys(documentResponseExamples);

/**
 * Read one entirely synthetic document example from the frozen contract.
 *
 * @param {string} name
 * @returns {any}
 */
export function documentExample(name) {
  const example = documentResponseExamples[name];
  if (example === undefined) {
    throw new Error(`O snapshot não declara o exemplo documental ${name}.`);
  }
  return structuredClone(example.value);
}
