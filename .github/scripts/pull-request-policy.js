"use strict";

const { execFileSync } = require("node:child_process");
const { readFileSync } = require("node:fs");

const CONVENTIONAL_TITLE =
  /^(build|chore|ci|docs|feat|fix|perf|refactor|revert|test)(\([a-z0-9][a-z0-9._/-]*\))?!?: ([a-z0-9][\x20-\x7e]*)$/;
const RELEASE_BRANCH = /^release\/sen-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const HOTFIX_BRANCH = /^hotfix\/sen-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const FULL_GIT_SHA = /^[0-9a-f]{40}$/i;

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
    typeof headRef === "string" &&
    (RELEASE_BRANCH.test(headRef) || HOTFIX_BRANCH.test(headRef));

  if (!sameRepository || !allowedBranch) {
    return (
      "Pull requests to main must come from a local " +
      "release/sen-<id>-<slug> or hotfix/sen-<id>-<slug> branch."
    );
  }

  return null;
}

function isReleasePullRequest(pullRequest) {
  const headRepository = pullRequest?.head?.repo?.full_name;
  const baseRepository = pullRequest?.base?.repo?.full_name;

  return (
    pullRequest?.base?.ref === "main" &&
    typeof pullRequest?.head?.ref === "string" &&
    RELEASE_BRANCH.test(pullRequest.head.ref) &&
    typeof headRepository === "string" &&
    headRepository === baseRepository
  );
}

function requireGitSha(value, label) {
  if (typeof value !== "string" || !FULL_GIT_SHA.test(value)) {
    throw new Error(`${label} must be a full 40-character Git SHA.`);
  }

  return value.toLowerCase();
}

function gitErrorDetail(error) {
  const stderr = error?.stderr;
  if (typeof stderr === "string" && stderr.trim().length > 0) {
    return stderr.trim().split(/\r?\n/).at(-1);
  }
  if (Buffer.isBuffer(stderr) && stderr.length > 0) {
    return stderr.toString("utf8").trim().split(/\r?\n/).at(-1);
  }
  if (error instanceof Error) {
    return error.message;
  }

  return "unknown Git error";
}

function createNonInteractiveGitEnvironment(environment = process.env) {
  return {
    ...environment,
    GCM_INTERACTIVE: "Never",
    GIT_TERMINAL_PROMPT: "0",
  };
}

function executeGit(args, cwd) {
  try {
    return execFileSync("git", args, {
      cwd,
      encoding: "utf8",
      env: createNonInteractiveGitEnvironment(),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    }).trim();
  } catch (error) {
    throw new Error(gitErrorDetail(error));
  }
}

function resolveGitObject(revision, label, cwd, runGit) {
  let resolved;
  try {
    resolved = runGit(["rev-parse", "--verify", revision], cwd);
  } catch (error) {
    throw new Error(
      `Release integrity could not resolve ${label}: ${gitErrorDetail(error)}.`,
    );
  }

  return requireGitSha(resolved, `Resolved ${label}`);
}

function resolveReachableTrees(ref, cwd, runGit) {
  let output;
  try {
    output = runGit(["log", "--format=%T", ref], cwd);
  } catch (error) {
    throw new Error(
      `Release integrity could not enumerate trees reachable from ${ref}: ` +
        `${gitErrorDetail(error)}.`,
    );
  }

  if (typeof output !== "string" || output.length === 0) {
    throw new Error(
      `Release integrity found no tree hashes reachable from ${ref}.`,
    );
  }

  const trees = new Set();
  for (const [index, tree] of output.split(/\r?\n/).entries()) {
    trees.add(requireGitSha(tree, `${ref} tree ${index + 1}`));
  }
  return trees;
}

function inspectNeutralMerge(developTree, cwd, runGit) {
  try {
    const output = runGit(
      ["merge-tree", "--write-tree", "origin/develop", "origin/main"],
      cwd,
    );
    const mergeTree = requireGitSha(output, "git merge-tree output");
    if (mergeTree === developTree) {
      return { neutral: true, reason: null };
    }
    return {
      neutral: false,
      reason: "git merge-tree produced content different from origin/develop",
    };
  } catch (error) {
    return {
      neutral: false,
      reason: `git merge-tree did not prove a clean merge: ${gitErrorDetail(error)}`,
    };
  }
}

function validateReleaseIntegrity(pullRequest, options = {}) {
  if (!isReleasePullRequest(pullRequest)) {
    throw new Error(
      "Release integrity can only validate a local release branch targeting main.",
    );
  }

  const cwd = options.cwd ?? process.cwd();
  const runGit = options.runGit ?? executeGit;
  const headRef = pullRequest.head.ref;
  const headSha = requireGitSha(
    pullRequest.head.sha,
    "The pull request head SHA",
  );
  const releaseRemoteRef = `origin/${headRef}`;

  try {
    runGit(
      [
        "fetch",
        "--no-tags",
        "--force",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/develop:refs/remotes/origin/develop",
        `+refs/heads/${headRef}:refs/remotes/origin/${headRef}`,
      ],
      cwd,
    );
  } catch (error) {
    throw new Error(
      "Release integrity could not fetch the current main, develop, and " +
        `release refs: ${gitErrorDetail(error)}.`,
    );
  }

  const mainSha = resolveGitObject(
    "origin/main^{commit}",
    "origin/main",
    cwd,
    runGit,
  );
  const developSha = resolveGitObject(
    "origin/develop^{commit}",
    "origin/develop",
    cwd,
    runGit,
  );
  const releaseSha = resolveGitObject(
    `${releaseRemoteRef}^{commit}`,
    releaseRemoteRef,
    cwd,
    runGit,
  );

  if (releaseSha !== headSha) {
    throw new Error(
      "The pull request head SHA is not the current tip of the release branch.",
    );
  }

  try {
    runGit(["merge-base", "--is-ancestor", "origin/main", headSha], cwd);
  } catch {
    throw new Error(
      "The release head must contain the current origin/main commit.",
    );
  }

  let revision;
  try {
    revision = runGit(["rev-list", "--parents", "-n", "1", headSha], cwd);
  } catch (error) {
    throw new Error(
      `Release integrity could not inspect the release parents: ${gitErrorDetail(error)}.`,
    );
  }

  const [commit, ...parents] = revision.split(/\s+/);
  if (commit.toLowerCase() !== headSha || parents.length !== 2) {
    throw new Error(
      "The release head must be exactly one merge commit with two parents.",
    );
  }
  if (
    parents[0].toLowerCase() !== developSha ||
    parents[1].toLowerCase() !== mainSha
  ) {
    throw new Error(
      "The release merge parents must be the current origin/develop first " +
        "and the current origin/main second.",
    );
  }

  const releaseTree = resolveGitObject(
    `${headSha}^{tree}`,
    "release tree",
    cwd,
    runGit,
  );
  const developTree = resolveGitObject(
    "origin/develop^{tree}",
    "origin/develop tree",
    cwd,
    runGit,
  );
  if (releaseTree !== developTree) {
    throw new Error(
      "The release tree must be exactly identical to the current origin/develop tree.",
    );
  }

  const mainTree = resolveGitObject(
    "origin/main^{tree}",
    "origin/main tree",
    cwd,
    runGit,
  );
  const neutralMerge = inspectNeutralMerge(developTree, cwd, runGit);
  let synchronizationProof = "neutral-merge";
  if (!neutralMerge.neutral) {
    const developHistoryTrees = resolveReachableTrees(
      "origin/develop",
      cwd,
      runGit,
    );
    if (!developHistoryTrees.has(mainTree)) {
      throw new Error(
        "Release integrity requires either a clean origin/develop + " +
          "origin/main merge that preserves the origin/develop tree, or the " +
          "legacy fallback where the current origin/main tree is reachable " +
          `from origin/develop. ${neutralMerge.reason}.`,
      );
    }
    synchronizationProof = "reachable-main-tree";
  }

  return {
    developSha,
    headSha,
    mainSha,
    mainTree,
    synchronizationProof,
    treeSha: releaseTree,
  };
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

function evaluatePolicy(event, options = {}) {
  const failures = validateEvent(event);
  if (
    failures.length === 0 &&
    isReleasePullRequest(event?.pull_request)
  ) {
    try {
      validateReleaseIntegrity(event.pull_request, options);
    } catch (error) {
      failures.push(
        error instanceof Error ? error.message : "Release integrity failed.",
      );
    }
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
  const failures = evaluatePolicy(event, {
    cwd: environment.GITHUB_WORKSPACE || process.cwd(),
  });
  if (failures.length > 0) {
    for (const failure of failures) {
      process.stderr.write(`Policy failed: ${failure}\n`);
    }
    process.exitCode = 1;
    return;
  }

  process.stdout.write("Pull request policy is valid.\n");
}

if (require.main === module) {
  main();
}

module.exports = {
  createNonInteractiveGitEnvironment,
  evaluatePolicy,
  executeGit,
  isReleasePullRequest,
  main,
  requireGitSha,
  validateBranchOrigin,
  validateEvent,
  validateReleaseIntegrity,
  validateTitle,
};
