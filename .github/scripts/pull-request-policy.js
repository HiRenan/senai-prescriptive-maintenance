"use strict";

const { readFileSync } = require("node:fs");

const CONVENTIONAL_TITLE =
  /^(build|chore|ci|docs|feat|fix|perf|refactor|revert|test)(\([a-z0-9][a-z0-9._/-]*\))?!?: ([a-z0-9][\x20-\x7e]*)$/;
const HOTFIX_BRANCH = /^hotfix\/[a-z0-9][a-z0-9._/-]*$/;

function validateTitle(title) {
  if (typeof title !== "string" || title.length === 0 || title.length > 120) {
    return "The pull request title must contain between 1 and 120 characters.";
  }
  if (title.trim() !== title) {
    return "The pull request title must not contain surrounding whitespace.";
  }

  const match = CONVENTIONAL_TITLE.exec(title);
  if (match === null || title.endsWith(".")) {
    return (
      "Use an English Conventional Commits title with an allowed type, " +
      "a lowercase description, and no trailing period."
    );
  }

  return null;
}

function validateBranchOrigin(pullRequest) {
  if (pullRequest === null || typeof pullRequest !== "object") {
    return "The pull request payload is missing.";
  }

  const baseRef = pullRequest.base?.ref;
  if (baseRef === "develop") {
    return null;
  }
  if (baseRef !== "main") {
    return "Pull requests must target develop or main.";
  }

  const headRef = pullRequest.head?.ref;
  const headRepository = pullRequest.head?.repo?.full_name;
  const baseRepository = pullRequest.base?.repo?.full_name;
  const sameRepository =
    typeof headRepository === "string" && headRepository === baseRepository;
  const allowedBranch =
    headRef === "develop" ||
    (typeof headRef === "string" && HOTFIX_BRANCH.test(headRef));

  if (!sameRepository || !allowedBranch) {
    return "Pull requests to main must come from develop or a local hotfix/* branch.";
  }

  return null;
}

function validateEvent(event) {
  const failures = [];
  const titleFailure = validateTitle(event?.pull_request?.title);
  const branchFailure = validateBranchOrigin(event?.pull_request);

  if (titleFailure !== null) {
    failures.push(titleFailure);
  }
  if (branchFailure !== null) {
    failures.push(branchFailure);
  }

  return failures;
}

function main(environment = process.env) {
  if (environment.GITHUB_EVENT_NAME !== "pull_request") {
    throw new Error("This policy only supports the pull_request event.");
  }

  const eventPath = environment.GITHUB_EVENT_PATH;
  if (typeof eventPath !== "string" || eventPath.length === 0) {
    throw new Error("GITHUB_EVENT_PATH is not set.");
  }

  const event = JSON.parse(readFileSync(eventPath, "utf8"));
  const failures = validateEvent(event);
  if (failures.length > 0) {
    for (const failure of failures) {
      process.stderr.write(`Policy failed: ${failure}\n`);
    }
    process.exitCode = 1;
    return;
  }

  process.stdout.write("Pull request title and branch origin are valid.\n");
}

if (require.main === module) {
  main();
}

module.exports = {
  main,
  validateBranchOrigin,
  validateEvent,
  validateTitle,
};
