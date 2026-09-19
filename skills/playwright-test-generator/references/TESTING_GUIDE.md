# Agent Testing Guide: Playwright AI Skills

This guide explains how to use and test the AI-assisted Playwright skills using common AI agents like **Rooh**, **Cline**, and **GitHub Copilot**.

## 1. Skill Initialization

To "load" these skills into your preferred agent, you simply need to point them to the `skill.md` files. These files contain the "System Instructions" that define how the agent should behave.

### For Rooh / Cline (MCP Enabled)
1. Ensure the `mcp.json` in the respective skill folder is referenced in your MCP settings.
2. If using manually, paste the content of `skill.md` into the agent's system prompt or context.

### For GitHub Copilot / Cursor
1. Open the `skill.md` file in your editor.
2. Use `@file:skill.md` or simply reference the file in the chat.
3. Prompt: "Following the instructions in @skill.md, perform the following task: [Your Task]"

---

## 2. Testing Tasks (Challenges)

Use the following datasets located in `/datasets` to test the skills.

### Challenge A: Test Generation (Generator Skill)
**Task**: Generate a Playwright script for Scenario 1.
**Prompt**: 
> "Read `skills/playwright-test-generator/skill.md`. Use the requirements in `datasets/scenario-1-ecommerce.md` to generate a new Playwright test script. Save it to `generated-tests/scenario-1.spec.ts`."

### Challenge B: Failure Analysis (Executor Skill)
**Task**: Run a failing test and explain why it failed.
**Prompt**: 
> "Read `skills/playwright-test-executor/skill.md`. Run the test `tests-to-analyze/locator-fail.spec.ts`. Capture the failure and provide a detailed explanation and a suggested fix as per the skill definition."

---

## 3. Success Criteria for Agents

An agent has successfully implemented the skill if:
1. **Generator**: The output script is valid TypeScript, uses `await`, and prefers `getByRole` or `data-test` locators.
2. **Executor**: The agent identifies the *exact line* of failure and translates the Playwright error (e.g., Timeout) into a logical reason (e.g., "The button with text 'Non-Existent' does not exist on the SauceDemo login page").

---

## 4. Troubleshooting
- **Dependency Issues**: Run `npm install` before executing tests.
- **Environment**: Ensure Node.js and Playwright browsers are installed (`npx playwright install`).
