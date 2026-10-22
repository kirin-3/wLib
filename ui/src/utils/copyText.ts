// SPDX-License-Identifier: GPL-3.0-or-later
export async function copyText(text: string, timeoutMs = 1500): Promise<boolean> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    if (navigator.clipboard?.writeText) {
      await Promise.race([
        navigator.clipboard.writeText(text),
        new Promise<never>((_, reject) => {
          timeout = setTimeout(() => reject(new Error("Clipboard request timed out")), timeoutMs);
        }),
      ]);
      return true;
    }
  } catch { /* Try the legacy webview clipboard path. */ }
  finally { if (timeout !== undefined) clearTimeout(timeout); }
  const previous = document.activeElement as HTMLElement | null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(textarea);
  try {
    textarea.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
    previous?.focus();
  }
}
