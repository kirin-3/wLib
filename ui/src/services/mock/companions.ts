// SPDX-License-Identifier: GPL-3.0-or-later
import type { CompanionTool } from "../api";

interface MockCompanions { tools: CompanionTool[]; overrides: Record<string, Record<string, boolean>> }
export const readMockCompanions = (): MockCompanions => {
  try { return JSON.parse(localStorage.getItem("wlib-mock-companions") || '{"tools":[],"overrides":{}}') as MockCompanions; }
  catch { return { tools: [], overrides: {} }; }
};
export const writeMockCompanions = (store: MockCompanions) => localStorage.setItem("wlib-mock-companions", JSON.stringify(store));
