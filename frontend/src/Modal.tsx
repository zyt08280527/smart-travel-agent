import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export function Modal({ open, title, onClose, children }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    if (!open) { if (element.open) element.close?.(); element.removeAttribute('open'); return }
    const previous = document.activeElement as HTMLElement | null
    if (element.showModal) element.showModal()
    else element.setAttribute('open', '')
    element.querySelector<HTMLButtonElement>('button')?.focus()
    return () => { if (element.open) element.close?.(); element.removeAttribute('open'); previous?.focus() }
  }, [open])
  return <dialog ref={dialog} className="travel-modal" aria-label={title} aria-modal="true"
    onCancel={(event) => { event.preventDefault(); onClose() }}>
    <div className="modal-heading"><strong>{title}</strong><button type="button" onClick={onClose} aria-label={`关闭${title}`}>关闭</button></div>
    {children}
  </dialog>
}
