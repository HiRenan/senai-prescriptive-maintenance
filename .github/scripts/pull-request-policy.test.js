"use strict";

const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const {
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} = require("node:fs");
const { tmpdir } = require("node:os");
const { isAbsolute, join, relative, resolve } = require("node:path");
const { describe, test } = require("node:test");

const {
  createNonInteractiveGitEnvironment,
  evaluatePolicy,
  executeGit,
  validateBranchOrigin,
  validateEvent,
  validateReleaseIntegrity,
  validateTitle,
} = require("./pull-request-policy.js");

const repository = "HiRenan/senai-prescriptive-maintenance";
const releaseBranch = "release/sen-20-foundation";
const validTitle = "ci: make release promotion repeatable";

test("forces every Git subprocess into non-interactive mode", () => {
  const environment = createNonInteractiveGitEnvironment({
    PATH: "test-path",
  });

  assert.equal(environment.PATH, "test-path");
  assert.equal(environment.GIT_TERMINAL_PROMPT, "0");
  assert.equal(environment.GCM_INTERACTIVE, "Never");
});

function pullRequest(baseRef, headRef, options = {}) {
  return {
    title: options.title ?? validTitle,
    base: {
      ref: baseRef,
      repo: { full_name: options.baseRepository ?? repository },
    },
    head: {
      ref: headRef,
      repo: { full_name: options.headRepository ?? repository },
      sha: options.sha ?? "0".repeat(40),
    },
  };
}

function eventFor(pullRequestPayload) {
  return { pull_request: pullRequestPayload };
}

function git(cwd, args, options = {}) {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    ...options,
  }).trim();
}

function removeTemporaryRepository(target) {
  const temporaryRoot = resolve(tmpdir());
  const resolvedTarget = resolve(target);
  const relativeTarget = relative(temporaryRoot, resolvedTarget);
  if (
    relativeTarget.length === 0 ||
    relativeTarget.startsWith("..") ||
    isAbsolute(relativeTarget)
  ) {
    throw new Error("Refusing to remove a path outside the system temp directory.");
  }

  rmSync(resolvedTarget, { force: true, maxRetries: 3, recursive: true });
}

function createRepositoryFixture() {
  const root = mkdtempSync(join(tmpdir(), "sen-20-pr-policy-"));
  const remote = join(root, "remote.git");
  const source = join(root, "source");
  const checkout = join(root, "checkout");
  mkdirSync(source);
  mkdirSync(checkout);

  git(root, ["init", "--bare", remote]);
  git(source, ["init"]);
  git(source, ["config", "user.name", "Renan Mocelin"]);
  git(source, ["config", "user.email", "renanryuakame@gmail.com"]);

  function writeTree(content) {
    writeFileSync(join(source, "state.txt"), content, "utf8");
    git(source, ["add", "state.txt"]);
    return git(source, ["write-tree"]);
  }

  function writeTreeFrom(tree, files) {
    git(source, ["read-tree", tree]);
    for (const [path, content] of Object.entries(files)) {
      writeFileSync(join(source, path), content, "utf8");
      git(source, ["add", path]);
    }
    return git(source, ["write-tree"]);
  }

  function commit(tree, parents, message) {
    const args = ["commit-tree", tree];
    for (const parent of parents) {
      args.push("-p", parent);
    }
    return git(source, args, { input: `${message}\n` });
  }

  const baseTree = writeTree("base\n");
  const base = commit(baseTree, [], "base");
  const main = commit(baseTree, [base], "main baseline");
  const developTree = writeTree("validated develop\n");
  const develop = commit(developTree, [base], "develop candidate");
  const release = commit(developTree, [develop, main], "reconcile main");

  git(source, ["remote", "add", "origin", remote]);

  function push(branch, sha) {
    git(source, [
      "push",
      "--force",
      "origin",
      `${sha}:refs/heads/${branch}`,
    ]);
  }

  push("main", main);
  push("develop", develop);
  push(releaseBranch, release);

  git(checkout, ["init"]);
  git(checkout, ["remote", "add", "origin", remote]);

  return {
    base,
    baseTree,
    checkout,
    commit,
    develop,
    developTree,
    main,
    mainTree: baseTree,
    push,
    release,
    remote,
    source,
    writeTree,
    writeTreeFrom,
    remove() {
      removeTemporaryRepository(root);
    },
  };
}

function withRepositoryFixture(callback) {
  const fixture = createRepositoryFixture();
  try {
    callback(fixture);
  } finally {
    fixture.remove();
  }
}

function evaluateRelease(fixture, headSha) {
  fixture.push(releaseBranch, headSha);
  return evaluatePolicy(
    eventFor(pullRequest("main", releaseBranch, { sha: headSha })),
    { cwd: fixture.checkout },
  );
}

function inspectRelease(fixture, headSha) {
  fixture.push(releaseBranch, headSha);
  return validateReleaseIntegrity(
    pullRequest("main", releaseBranch, { sha: headSha }),
    { cwd: fixture.checkout },
  );
}

function createUnsynchronizedRelease(fixture) {
  const unsynchronizedTree = fixture.writeTreeFrom(fixture.baseTree, {
    "hotfix.txt": "main-only hotfix\n",
  });
  const currentMain = fixture.commit(
    unsynchronizedTree,
    [fixture.main],
    "unsynchronized hotfix",
  );
  fixture.push("main", currentMain);
  const release = fixture.commit(
    fixture.developTree,
    [fixture.develop, currentMain],
    "discard unsynchronized hotfix",
  );
  return { currentMain, release };
}

describe("pull request title policy", () => {
  test("accepts project and Dependabot Conventional Commits titles", () => {
    assert.equal(validateTitle(validTitle), null);
    assert.equal(
      validateTitle("chore(deps): bump locked development dependencies"),
      null,
    );
    assert.equal(validateTitle("fix(api)!: reject invalid settings"), null);
  });

  test("rejects unsupported types and non-conventional descriptions", () => {
    assert.notEqual(validateTitle("feature: add maintenance dashboard"), null);
    assert.notEqual(validateTitle("ci: Add security gates"), null);
    assert.notEqual(validateTitle("ci: adicionar segurança"), null);
    assert.notEqual(validateTitle("ci: add security gates."), null);
    assert.notEqual(validateTitle("update security gates"), null);
  });
});

describe("pull request branch origin policy", () => {
  test("allows task branches targeting develop", () => {
    assert.equal(
      validateBranchOrigin(pullRequest("develop", "ci/sen-20-release-policy")),
      null,
    );
  });

  test("allows well-formed local release and hotfix branches targeting main", () => {
    assert.equal(
      validateBranchOrigin(pullRequest("main", releaseBranch)),
      null,
    );
    assert.equal(
      validateBranchOrigin(
        pullRequest("main", "hotfix/sen-42-reject-invalid-token"),
      ),
      null,
    );
  });

  test("rejects develop and task branches targeting main", () => {
    assert.notEqual(validateBranchOrigin(pullRequest("main", "develop")), null);
    assert.notEqual(
      validateBranchOrigin(pullRequest("main", "feat/sen-21-direct-to-main")),
      null,
    );
  });

  test("rejects malformed release and hotfix branch names", () => {
    for (const branch of [
      "release/foundation",
      "release/sen-0-foundation",
      "release/sen-20-Foundation",
      "release/sen-20-foundation_1",
      "hotfix/urgent-fix",
      "hotfix/sen-42-",
    ]) {
      assert.notEqual(validateBranchOrigin(pullRequest("main", branch)), null);
    }
  });

  test("rejects fork branches targeting main", () => {
    assert.notEqual(
      validateBranchOrigin(
        pullRequest("main", releaseBranch, {
          headRepository: "example/fork",
        }),
      ),
      null,
    );
  });

  test("rejects unsupported target branches", () => {
    assert.notEqual(
      validateBranchOrigin(pullRequest("release", releaseBranch)),
      null,
    );
  });
});

describe("release integrity gate", () => {
  test("creates the documented release worktree without moving the primary checkout", () => {
    withRepositoryFixture((fixture) => {
      const releaseWorktree = resolve(fixture.source, "..", "release-worktree");
      git(fixture.source, [
        "fetch",
        "--no-tags",
        "origin",
        "+refs/heads/develop:refs/remotes/origin/develop",
      ]);
      git(fixture.source, ["branch", "develop", fixture.develop]);
      git(fixture.source, ["switch", "develop"]);

      assert.equal(git(fixture.source, ["branch", "--show-current"]), "develop");
      assert.equal(git(fixture.source, ["status", "--short"]), "");
      git(fixture.source, [
        "worktree",
        "add",
        releaseWorktree,
        "-b",
        "release/sen-20-docs-check",
        "origin/develop",
      ]);

      assert.equal(git(fixture.source, ["branch", "--show-current"]), "develop");
      assert.equal(git(fixture.source, ["status", "--short"]), "");
      assert.equal(
        git(releaseWorktree, ["branch", "--show-current"]),
        "release/sen-20-docs-check",
      );
      assert.equal(git(releaseWorktree, ["rev-parse", "HEAD"]), fixture.develop);
    });
  });

  test("accepts a clean merge that preserves the current develop tree", () => {
    withRepositoryFixture((fixture) => {
      assert.equal(
        inspectRelease(fixture, fixture.release).synchronizationProof,
        "neutral-merge",
      );
    });
  });

  test("accepts the reachable-tree fallback for a conflicting legacy baseline", () => {
    withRepositoryFixture((fixture) => {
      const currentDevelopTree = fixture.writeTreeFrom(fixture.developTree, {
        "state.txt": "current develop\n",
      });
      const currentDevelop = fixture.commit(
        currentDevelopTree,
        [fixture.develop],
        "advance develop after legacy baseline",
      );
      const legacyMain = fixture.commit(
        fixture.developTree,
        [fixture.main],
        "legacy squash tree",
      );
      fixture.push("develop", currentDevelop);
      fixture.push("main", legacyMain);
      const release = fixture.commit(
        currentDevelopTree,
        [currentDevelop, legacyMain],
        "legacy reconciliation",
      );

      assert.equal(
        inspectRelease(fixture, release).synchronizationProof,
        "reachable-main-tree",
      );
    });
  });

  test("rejects a release that does not contain current main", () => {
    withRepositoryFixture((fixture) => {
      const unrelated = fixture.commit(
        fixture.baseTree,
        [fixture.base],
        "unrelated history",
      );
      const release = fixture.commit(
        fixture.developTree,
        [fixture.develop, unrelated],
        "missing main",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /contain the current origin\/main/,
      );
    });
  });

  test("rejects a common commit instead of a reconciliation merge", () => {
    withRepositoryFixture((fixture) => {
      const release = fixture.commit(
        fixture.developTree,
        [fixture.main],
        "ordinary commit",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /exactly one merge commit with two parents/,
      );
    });
  });

  test("rejects inverted release parents", () => {
    withRepositoryFixture((fixture) => {
      const release = fixture.commit(
        fixture.developTree,
        [fixture.main, fixture.develop],
        "inverted parents",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /origin\/develop first.*origin\/main second/,
      );
    });
  });

  test("rejects release commits with extra parents", () => {
    withRepositoryFixture((fixture) => {
      const thirdParent = fixture.commit(
        fixture.baseTree,
        [fixture.base],
        "third parent",
      );
      const release = fixture.commit(
        fixture.developTree,
        [fixture.develop, fixture.main, thirdParent],
        "extra parent",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /exactly one merge commit with two parents/,
      );
    });
  });

  test("rejects a release tree that differs from current develop", () => {
    withRepositoryFixture((fixture) => {
      const release = fixture.commit(
        fixture.mainTree,
        [fixture.develop, fixture.main],
        "divergent tree",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /exactly identical to the current origin\/develop tree/,
      );
    });
  });

  test("rejects an unsynchronized hotfix even when release matches develop", () => {
    withRepositoryFixture((fixture) => {
      const { release } = createUnsynchronizedRelease(fixture);

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /requires either a clean origin\/develop.*legacy fallback/,
      );
    });
  });

  test("accepts a synchronized hotfix after develop has advanced", () => {
    withRepositoryFixture((fixture) => {
      const hotfixTree = fixture.writeTreeFrom(fixture.baseTree, {
        "hotfix.txt": "synchronized hotfix\n",
      });
      const currentMain = fixture.commit(
        hotfixTree,
        [fixture.main],
        "hotfix on main",
      );
      const synchronizedDevelopTree = fixture.writeTreeFrom(
        fixture.developTree,
        { "hotfix.txt": "synchronized hotfix\n" },
      );
      const currentDevelop = fixture.commit(
        synchronizedDevelopTree,
        [fixture.develop],
        "synchronize hotfix after task",
      );
      fixture.push("main", currentMain);
      fixture.push("develop", currentDevelop);
      const release = fixture.commit(
        synchronizedDevelopTree,
        [currentDevelop, currentMain],
        "neutral hotfix reconciliation",
      );

      assert.equal(
        inspectRelease(fixture, release).synchronizationProof,
        "neutral-merge",
      );
    });
  });

  test("rejects a conflicting merge without the legacy fallback", () => {
    withRepositoryFixture((fixture) => {
      const conflictingTree = fixture.writeTreeFrom(fixture.baseTree, {
        "state.txt": "conflicting main change\n",
      });
      const currentMain = fixture.commit(
        conflictingTree,
        [fixture.main],
        "conflicting main change",
      );
      fixture.push("main", currentMain);
      const release = fixture.commit(
        fixture.developTree,
        [fixture.develop, currentMain],
        "conflicting reconciliation",
      );

      assert.match(
        evaluateRelease(fixture, release).join(" "),
        /merge-tree did not prove a clean merge/,
      );
    });
  });

  test("rejects a release after develop advances", () => {
    withRepositoryFixture((fixture) => {
      const currentDevelop = fixture.commit(
        fixture.developTree,
        [fixture.develop],
        "advance develop",
      );
      fixture.push("develop", currentDevelop);

      assert.match(
        evaluateRelease(fixture, fixture.release).join(" "),
        /current origin\/develop first/,
      );
    });
  });

  test("rejects a release after main advances", () => {
    withRepositoryFixture((fixture) => {
      const currentMain = fixture.commit(
        fixture.mainTree,
        [fixture.main],
        "advance main",
      );
      fixture.push("main", currentMain);

      assert.match(
        evaluateRelease(fixture, fixture.release).join(" "),
        /contain the current origin\/main/,
      );
    });
  });

  test("rejects an event SHA that is not the current release tip", () => {
    withRepositoryFixture((fixture) => {
      const movedRelease = fixture.commit(
        fixture.developTree,
        [fixture.release],
        "move release",
      );
      fixture.push(releaseBranch, movedRelease);
      const failures = evaluatePolicy(
        eventFor(
          pullRequest("main", releaseBranch, { sha: fixture.release }),
        ),
        { cwd: fixture.checkout },
      );

      assert.match(failures.join(" "), /not the current tip/);
    });
  });

  test("rejects an invalid event SHA before invoking Git", () => {
    let gitCalls = 0;
    const failures = evaluatePolicy(
      eventFor(
        pullRequest("main", releaseBranch, {
          sha: "0".repeat(40) + ";invalid",
        }),
      ),
      {
        runGit() {
          gitCalls += 1;
          return "";
        },
      },
    );

    assert.match(failures.join(" "), /full 40-character Git SHA/);
    assert.equal(gitCalls, 0);
  });

  test("fails closed when a required remote ref is unavailable", () => {
    withRepositoryFixture((fixture) => {
      git(fixture.source, [
        "push",
        "origin",
        "--delete",
        "develop",
      ]);

      assert.match(
        evaluateRelease(fixture, fixture.release).join(" "),
        /could not fetch the current main, develop, and release refs/,
      );
    });
  });

  test("fails closed when merge-tree returns malformed output", () => {
    withRepositoryFixture((fixture) => {
      const { release } = createUnsynchronizedRelease(fixture);
      fixture.push(releaseBranch, release);
      const failures = evaluatePolicy(
        eventFor(
          pullRequest("main", releaseBranch, { sha: release }),
        ),
        {
          cwd: fixture.checkout,
          runGit(args, cwd) {
            if (args[0] === "merge-tree") {
              return "not-a-tree";
            }
            return executeGit(args, cwd);
          },
        },
      );

      assert.match(failures.join(" "), /must be a full 40-character Git SHA/);
    });
  });

  test("fails closed when merge-tree cannot run and no fallback matches", () => {
    withRepositoryFixture((fixture) => {
      const { release } = createUnsynchronizedRelease(fixture);
      fixture.push(releaseBranch, release);
      const failures = evaluatePolicy(
        eventFor(pullRequest("main", releaseBranch, { sha: release })),
        {
          cwd: fixture.checkout,
          runGit(args, cwd) {
            if (args[0] === "merge-tree") {
              throw new Error("simulated merge-tree failure");
            }
            return executeGit(args, cwd);
          },
        },
      );

      assert.match(failures.join(" "), /simulated merge-tree failure/);
    });
  });

  test("fails closed when fallback history returns an invalid tree hash", () => {
    withRepositoryFixture((fixture) => {
      const { release } = createUnsynchronizedRelease(fixture);
      fixture.push(releaseBranch, release);
      const failures = evaluatePolicy(
        eventFor(pullRequest("main", releaseBranch, { sha: release })),
        {
          cwd: fixture.checkout,
          runGit(args, cwd) {
            if (args[0] === "merge-tree") {
              return fixture.mainTree;
            }
            if (args[0] === "log" && args[1] === "--format=%T") {
              return "not-a-tree";
            }
            return executeGit(args, cwd);
          },
        },
      );

      assert.match(failures.join(" "), /must be a full 40-character Git SHA/);
    });
  });
});

test("does not run the release gate for task or hotfix events", () => {
  let gitCalls = 0;
  const options = {
    runGit() {
      gitCalls += 1;
      throw new Error("Git must not run for this flow.");
    },
  };

  assert.deepEqual(
    evaluatePolicy(
      eventFor(pullRequest("develop", "ci/sen-20-release-policy")),
      options,
    ),
    [],
  );
  assert.deepEqual(
    evaluatePolicy(
      eventFor(
        pullRequest("main", "hotfix/sen-42-reject-invalid-token"),
      ),
      options,
    ),
    [],
  );
  assert.equal(gitCalls, 0);
});

test("rejects direct develop and fork events before invoking Git", () => {
  let gitCalls = 0;
  const options = {
    runGit() {
      gitCalls += 1;
      throw new Error("Git must not run for rejected metadata.");
    },
  };

  assert.match(
    evaluatePolicy(
      eventFor(pullRequest("main", "develop")),
      options,
    ).join(" "),
    /must come from a local/,
  );
  assert.match(
    evaluatePolicy(
      eventFor(
        pullRequest("main", releaseBranch, {
          headRepository: "example/fork",
        }),
      ),
      options,
    ).join(" "),
    /must come from a local/,
  );
  assert.equal(gitCalls, 0);
});

test("reports independent title and branch failures", () => {
  const event = eventFor(
    pullRequest("main", "feat/sen-21-direct-to-main", {
      title: "invalid title",
    }),
  );

  assert.equal(validateEvent(event).length, 2);
});
