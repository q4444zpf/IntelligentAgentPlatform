function copyTextWithSelection(text: string): void {
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  document.body.appendChild(textarea);

  try {
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    if (!document.execCommand('copy')) throw new Error('Clipboard copy command was rejected');
  } finally {
    textarea.remove();
    activeElement?.focus();
  }
}

export async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // Some browsers expose the API but reject it outside a secure context.
  }

  copyTextWithSelection(text);
}
