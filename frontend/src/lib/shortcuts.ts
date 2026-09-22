/**
 * Shared guard for global keyboard shortcuts.
 *
 * A single-letter shortcut is only safe when the keystroke isn't meant for
 * something else — a text field the technician is typing in, or a modal
 * that has taken over the screen. Both checks live here so every component
 * that binds a shortcut applies the same rule.
 */
export function shouldIgnoreShortcut(event: KeyboardEvent): boolean {
  // Modifier combinations belong to the browser/OS, not to us.
  if (event.metaKey || event.ctrlKey || event.altKey) return true

  const target = event.target as HTMLElement | null
  if (target) {
    const tag = target.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) {
      return true
    }
  }

  // A dialog is modal by definition: while one is open it owns the keyboard.
  return document.querySelector('[role="dialog"]') !== null
}
