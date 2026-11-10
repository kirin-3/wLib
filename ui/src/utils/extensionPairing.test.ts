// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { answerPairingRequests, pairingQuestion } from "./extensionPairing.ts";

const entry = { id: "AB12-CD34", browser: "Firefox", origin: "moz-extension://x", at: "" };

test("the question names the browser and the code", () => {
  const question = pairingQuestion(entry);
  assert.match(question.message, /Firefox/);
  assert.match(question.message, /AB12-CD34/);
  assert.equal(question.confirmLabel, "Allow");
});

test("each pending request is asked once and answered with the user's choice", async () => {
  const answers: [string, boolean][] = [];
  let questions = 0;
  const api = {
    getExtensionPairing: async () => ({ success: true, pending: [entry], paired: [] }),
    resolveExtensionPairing: async (id: string, allow: boolean) => {
      answers.push([id, allow]);
      return { success: true };
    },
  };
  const refuse = async () => { questions++; return false; };
  await answerPairingRequests(api, refuse);
  await answerPairingRequests(api, refuse);
  assert.equal(questions, 1);
  assert.deepEqual(answers, [["AB12-CD34", false]]);
});
