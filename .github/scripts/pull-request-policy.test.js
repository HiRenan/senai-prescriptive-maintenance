"use strict";

const assert = require("node:assert/strict");
const { describe, test } = require("node:test");

const {
  validateBranchOrigin,
  validateEvent,
  validateTitle,
} = require("./pull-request-policy.js");

const repository = "HiRenan/senai-prescriptive-maintenance";

function pullRequest(baseRef, headRef, headRepository = repository) {
  return {
    title: "ci: add continuous integration and security gates",
    base: {
      ref: baseRef,
      repo: { full_name: repository },
    },
    head: {
      ref: headRef,
      repo: { full_name: headRepository },
    },
  };
}

describe("pull request title policy", () => {
  test("accepts project and Dependabot Conventional Commits titles", () => {
    assert.equal(
      validateTitle("ci: add continuous integration and security gates"),
      null,
    );
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
      validateBranchOrigin(pullRequest("develop", "ci/sen-16-github-security")),
      null,
    );
  });

  test("allows local develop and hotfix branches targeting main", () => {
    assert.equal(validateBranchOrigin(pullRequest("main", "develop")), null);
    assert.equal(
      validateBranchOrigin(pullRequest("main", "hotfix/urgent-fix")),
      null,
    );
  });

  test("rejects task and fork branches targeting main", () => {
    assert.notEqual(
      validateBranchOrigin(pullRequest("main", "feat/direct-to-main")),
      null,
    );
    assert.notEqual(
      validateBranchOrigin(pullRequest("main", "develop", "example/fork")),
      null,
    );
  });

  test("rejects unsupported target branches", () => {
    assert.notEqual(validateBranchOrigin(pullRequest("release", "develop")), null);
  });
});

test("reports independent title and branch failures", () => {
  const event = {
    pull_request: {
      ...pullRequest("main", "feat/direct-to-main"),
      title: "invalid title",
    },
  };

  assert.equal(validateEvent(event).length, 2);
});
