export function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className={`card w-full ${wide ? "max-w-5xl" : "max-w-2xl"} p-5`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold">{title}</h3>
          <button className="text-slate-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
