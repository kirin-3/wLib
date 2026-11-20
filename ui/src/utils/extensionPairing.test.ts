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

test("an open question is not asked twice, and a request that comes back is asked again", async () => {
  const answers: [string, boolean][] = [];
  let questions = 0;
  let pending = [entry];
  const api = {
    getExtensionPairing: async () => ({ success: true, pending, paired: [] }),
    resolveExtensionPairing: async (id: string, allow: boolean) => {
      answers.push([id, allow]);
      pending = [];
      // The first request expires while the question is open.
      return { success: answers.length > 1 };
    },
  };
  const refuse = async () => { questions++; return false; };
  await Promise.all([answerPairingRequests(api, refuse), answerPairingRequests(api, refuse)]);
  assert.equal(questions, 1);
  assert.deepEqual(answers, [["AB12-CD34", false]]);
  // The same code is pending again later (it expired and the extension asked again).
  pending = [{ ...entry, at: "2025-01-01T00:10:00Z" }];
  await answerPairingRequests(api, refuse);
  assert.equal(questions, 2);
});

test("an overlapping check skips requests already answered since its snapshot", async () => {
  const second = { ...entry, id: "EF56-7890" };
  let pending = [entry, second];
  const firstAnswerSent = Promise.withResolvers<void>();
  const firstReply = Promise.withResolvers<void>();
  const api = {
    getExtensionPairing: async () => ({ success: true, pending, paired: [] }),
    resolveExtensionPairing: async (id: string) => {
      pending = pending.filter((request) => request.id !== id);
      if (id === entry.id) {
        firstAnswerSent.resolve();
        await firstReply.promise;
      }
      return { success: true };
    },
  };
  const questions: string[] = [];
  const allow = async ({ message }: ReturnType<typeof pairingQuestion>) => {
    questions.push(message);
    return true;
  };

  const firstCheck = answerPairingRequests(api, allow);
  const secondCheck = answerPairingRequests(api, allow);
  await firstAnswerSent.promise;
  await secondCheck;
  firstReply.resolve();
  await firstCheck;

  assert.deepEqual(questions, [pairingQuestion(entry).message, pairingQuestion(second).message]);
});
